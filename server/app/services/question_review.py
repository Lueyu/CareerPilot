"""Approve validated generation drafts without calling an LLM or creating evidence."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import lazyload

from app.models import ImportItem, ImportSession, Question, QuestionGeneration, Skill
from app.services import question_generation as protocol


def issue_signature(meta):
    refs = set((meta.get('item') or {}).get('fact_refs') or [])
    snapshot = meta.get('input_snapshot') or {}
    # Stable fact IDs do not imply stable content: editing responsibilities keeps
    # P{id}-responsibilities but changes which personal claims an answer may make.
    contexts = {}
    for project in snapshot.get('project_context') or []:
        referenced = sorted((fact for fact in project.get('facts', []) if fact['id'] in refs), key=lambda fact: fact['id'])
        frozen = {'fingerprint': project.get('snapshot_fingerprint'),
                  'profile': project.get('profile'), 'referenced_facts': referenced}
        contexts[project['id']] = hashlib.sha256(json.dumps(frozen, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return sorted((target['analysis_id'], target['memory_id'], target['issue_id'], contexts.get(target['project_id']))
                  for target in snapshot.get('project_issue_targets') or []
                  if target['fact_ref'] in refs)


async def approve_generated(session, workspace, import_id, item_ids=None, edits=None):
    from app.api.v1.question_generation import normalized_title, workspace_lock
    from app.api.v1.bank import SKILL_TOPIC

    if edits:
        raise HTTPException(422, "请先在补题工作台保存结构化编辑，再审核入库")
    await workspace_lock(session, workspace.id)
    imp = await session.scalar(select(ImportSession).where(ImportSession.id == import_id, ImportSession.workspace_id == workspace.id).execution_options(populate_existing=True))
    if not imp or imp.status not in ("REVIEW", "APPROVED"):
        raise HTTPException(409, "当前会话不能审核")
    task = await session.scalar(select(QuestionGeneration).where(QuestionGeneration.import_session_id == imp.id, QuestionGeneration.workspace_id == workspace.id))
    if task is None:
        raise HTTPException(409, "生成记录缺失，无法确认资料来源")
    drafts = (await session.scalars(select(ImportItem).where(ImportItem.session_id == imp.id, ImportItem.status == "DRAFT").order_by(ImportItem.id).with_for_update().execution_options(populate_existing=True))).all()
    selected = [i for i in drafts if item_ids is None or i.id in item_ids]
    skills = {s.key: s for s in (await session.scalars(select(Skill))).all()}
    questions = list((await session.scalars(select(Question).options(lazyload("*")).where((Question.workspace_id.is_(None)) | (Question.workspace_id == workspace.id), Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY')))).all())
    by_id = {q.id: q for q in questions}
    by_title = {normalized_title(q.title): q for q in questions}
    by_group = {(q.generation_meta or {}).get("group_key"): q for q in reversed(questions) if q.generation_meta}
    created, merged = [], []
    for draft in selected:
        meta = draft.generation_meta or {}
        try:
            payload = protocol.parse_response(json.dumps({"schema_version": 1, "batch_id": task.id, "items": [meta.get("item")]}, ensure_ascii=False), {**task.input_snapshot, "count": 1}, task.id, require_all_targets=False)[0]
            content = protocol.render_item(payload, task.input_snapshot)
        except ValueError as exc:
            raise HTTPException(422, f"草稿 {draft.id}: {exc}") from exc
        if payload["question_mode"] == "needs_context" or payload.get("missing_context"):
            raise HTTPException(422, "项目资料仍待补充：请补全资料后创建新批次，不可将未知经历作为本人答案入库")
        duplicate = by_title.get(normalized_title(payload["title"]))
        # Same title is not the same project-specific answer. Preserve new source
        # context as a canonical-linked variant rather than discarding its rubric.
        if duplicate and (not issue_signature(meta) or issue_signature(meta) == issue_signature(duplicate.generation_meta or {})):
            draft.status, draft.merged_into, draft.is_duplicate_of = "MERGED", duplicate.id, duplicate.id
            merged.append({"item_id": draft.id, "question_id": duplicate.id})
            continue
        source_id = payload.get("source_question_id")
        # Preserve a source relation even if the model omitted the optional source ID.
        if source_id is None:
            source_id = next((s["id"] for s in task.input_snapshot.get("source_questions", []) if normalized_title(s["title"]) == normalized_title(payload["original_question"])), None)
        canonical = by_id.get(source_id) if source_id else (by_title.get(normalized_title(payload["original_question"])) or by_group.get(meta.get("group_key")))
        canonical = canonical or duplicate
        seen = set()
        while canonical and canonical.canonical_question_id:
            if canonical.id in seen or canonical.canonical_question_id not in by_id:
                raise HTTPException(409, "题目来源关系无效，请检查关联题")
            seen.add(canonical.id)
            canonical = by_id[canonical.canonical_question_id]
        skill = skills.get(payload["skill_key"])
        if not skill:
            raise HTTPException(422, "技能已不存在")
        q = Question(short_code=f"Q-G{task.id}-{draft.id}", workspace_id=workspace.id, title=payload["title"], qtype="open", topic=SKILL_TOPIC.get(skill.key, "harness"), skill_id=skill.id, difficulty=3, answer_html=content["answer_html"], rubric=content["rubric"], source_type="GENERATED", company=None, tags=["项目题" if payload["question_mode"] == "project" else "通用题"], is_demo=workspace.is_demo, canonical_question_id=canonical.id if canonical else None, generation_meta={**meta, "item": payload})
        session.add(q)
        await session.flush()
        draft.status, draft.question_id = "APPROVED", q.id
        by_id[q.id] = q
        by_title[normalized_title(q.title)] = q
        if meta.get("group_key"):
            by_group.setdefault(meta["group_key"], canonical or q)
        created.append(q.short_code)
    await session.flush()
    if all(d.status != "DRAFT" for d in drafts):
        imp.status, imp.reviewed_at = "APPROVED", datetime.now(timezone.utc)
    await session.commit()
    return {"created": created, "merged": merged, "import_id": imp.id, "status": imp.status}
