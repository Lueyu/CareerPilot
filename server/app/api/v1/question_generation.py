"""Two generation channels share frozen inputs, validation and private review."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from app.i18n import message as localized_message
from app.api.deps import get_workspace
from app.db import SessionLocal, get_session
from app.generation_i18n import generation_message, generation_skill_name, saved_language
from app.i18n import request_language
from app.models import (
    ImportItem,
    ImportSession,
    LearningTask,
    Project,
    ProjectAnalysis,
    Question,
    QuestionGeneration,
    Skill,
    Workspace,
)
from app.services import question_generation as protocol
from app.services.bank_review_memory import load_review_feedback
from app.services.project_context import build_project_context
from app.services.project_memory import issue_targets

router = APIRouter(prefix="/question-generation", tags=["question-generation"])
_running: set[asyncio.Task] = set()


class GenerationIn(BaseModel):
    kind: Literal["import", "supplement"] = "supplement"
    mode: Literal["api", "external"] = "external"
    count: int = Field(default=5, ge=1, le=20)
    skill_keys: list[str] = Field(default_factory=list, max_length=8)
    project_ids: list[int] = Field(default_factory=list, max_length=10)
    source_text: str = Field(default="", max_length=60000)
    purpose: str = Field(default="", max_length=3000)
    source_import_id: int | None = None
    request_key: str | None = Field(default=None, max_length=120)
    source_analysis_id: int | None = None
    source_issue_ids: list[str] = Field(default_factory=list, max_length=20)


class ResultIn(BaseModel):
    text: str = Field(min_length=1, max_length=500000)


class ItemEdit(BaseModel):
    item: dict
    revision: int | None = Field(default=None, ge=0)


def normalized_title(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


async def workspace_lock(session: AsyncSession, workspace_id: int):
    # Serialize create/approve across processes, not only within one event loop.
    await session.execute(text("SELECT pg_advisory_xact_lock(20261116, :ws)"), {"ws": workspace_id})


async def owned_task(session, ws_id, task_id, lock=False):
    query = select(QuestionGeneration).where(QuestionGeneration.id == task_id, QuestionGeneration.workspace_id == ws_id)
    if lock:
        query = query.with_for_update()
    task = await session.scalar(query.execution_options(populate_existing=True))
    if task is None:
        raise HTTPException(404, localized_message("errors.generation_job_not_found"))
    return task


async def task_view(session, task):
    result = {key: getattr(task, key) for key in ("id", "kind", "mode", "status", "prompt_text", "input_snapshot", "raw_response", "error", "import_session_id", "result_meta")}
    result["created_at"] = task.created_at.isoformat() if task.created_at else None
    if task.import_session_id:
        from app.api.v1.bank import _import_review
        result["review"] = await _import_review(session, type("Scope", (), {"id": task.workspace_id})(), task.import_session_id)
        if result["review"]["status"] == "APPROVED":
            result["status"] = "COMPLETE"
    return result


@router.get("")
async def list_tasks(ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(QuestionGeneration, ImportSession.status).outerjoin(ImportSession, ImportSession.id == QuestionGeneration.import_session_id).where(QuestionGeneration.workspace_id == ws.id).order_by(QuestionGeneration.id.desc()).limit(50))).all()
    return {"items": [{"id": t.id, "kind": t.kind, "mode": t.mode, "status": "COMPLETE" if review_status == "APPROVED" else t.status, "error": t.error, "import_session_id": t.import_session_id, "created_at": t.created_at.isoformat(), "purpose": t.input_snapshot.get("purpose", "")} for t, review_status in rows]}


@router.get("/suggestions")
async def suggestions(ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    skills = (await session.scalars(select(Skill).order_by(Skill.id))).all()
    counts = dict((await session.execute(select(Question.skill_id, func.count(Question.id)).where((Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id), Question.canonical_question_id.is_(None), Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY')).group_by(Question.skill_id))).all())
    language = request_language()
    items = []
    for skill in sorted(skills, key=lambda s: counts.get(s.id, 0))[:3]:
        name = generation_skill_name(skill.key, skill.name, language)
        items.append({
            "id": f"coverage-{skill.key}",
            "title": generation_message("suggestion.coverage_title").format(skill=name),
            "reason": generation_message("suggestion.coverage_reason").format(count=counts.get(skill.id, 0)),
            "kind": "supplement", "count": 5, "skill_keys": [skill.key], "project_ids": [],
            "purpose": generation_message("suggestion.coverage_purpose").format(skill=name),
        })
    projects = (await session.scalars(select(Project).where(Project.workspace_id == ws.id,Project.archived_at.is_(None)).order_by(Project.id).limit(10))).all()
    for p in projects:
        latest_analysis = p.latest_valid_analysis_id or await session.scalar(select(ProjectAnalysis.id).where(ProjectAnalysis.project_id == p.id,
            ProjectAnalysis.workspace_id == ws.id, ProjectAnalysis.status == 'SUCCESS').order_by(ProjectAnalysis.id.desc()).limit(1))
        if latest_analysis:
            from app.services.project_memory import active_project_memories, report_binding
            gaps=[m for m in await active_project_memories(session,ws.id,p.id)
                  if m.mtype=='ProjectIssue' and report_binding(m,latest_analysis)][:3]
            for gap in gaps:
                items.append({'id': f'project-issue-{gap.id}',
                    'title': generation_message('suggestion.issue_title').format(project=p.name),
                    'reason': generation_message('suggestion.issue_reason').format(observation=gap.content[:400]),
                    'kind': 'supplement', 'count': 3, 'skill_keys': [report_binding(gap,latest_analysis)['issue']['skill_key']],
                    'project_ids': [p.id], 'source_analysis_id': latest_analysis, 'source_issue_ids': [report_binding(gap,latest_analysis)['issue_id']],
                    'purpose': generation_message('suggestion.issue_purpose')})
        covered = await session.scalar(select(func.count()).select_from(Question).where(Question.workspace_id == ws.id, Question.generation_meta["item"]["project_id"].as_integer() == p.id))
        if not covered:
            items.append({
                "id": f"project-{p.id}", "title": generation_message("suggestion.project_title").format(project=p.name),
                "reason": generation_message("suggestion.project_reason"),
                "kind": "supplement", "count": 3, "skill_keys": [], "project_ids": [p.id],
                "purpose": generation_message("suggestion.project_purpose"),
            })
    tasks = (await session.scalars(select(LearningTask).where(LearningTask.workspace_id == ws.id, LearningTask.source_criterion.is_not(None)).order_by(LearningTask.id.desc()).limit(3))).all()
    for task in tasks:
        key = next((s.key for s in skills if s.id == task.skill_id), None)
        items.append({
            "id": f"learning-{task.id}", "title": generation_message("suggestion.learning_title"),
            "reason": generation_message("suggestion.learning_reason").format(task=task.id, criterion=task.source_criterion),
            "kind": "supplement", "count": 3, "skill_keys": [key] if key else [], "project_ids": [],
            "purpose": generation_message("suggestion.learning_purpose").format(criterion=task.source_criterion),
        })
    return {"items": items}


@router.post("")
async def create_task(req: GenerationIn, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    await workspace_lock(session, ws.id)
    request_key = req.request_key or uuid4().hex
    previous = await session.scalar(select(QuestionGeneration).where(QuestionGeneration.workspace_id == ws.id, QuestionGeneration.request_key == request_key))
    if previous:
        if GenerationIn.model_validate(previous.request_json).model_dump(exclude={"request_key"}) != req.model_dump(exclude={"request_key"}):
            raise HTTPException(409, localized_message("errors.the_inputs_for_this_request_id_have_changed_create"))
        return await task_view(session, previous)
    skills = (await session.scalars(select(Skill).order_by(Skill.id))).all()
    allowed = {s.key for s in skills}
    if set(req.skill_keys) - allowed:
        raise HTTPException(422, localized_message("errors.skill_not_found"))
    if not allowed:
        raise HTTPException(409, localized_message("errors.initialize_a_skill_domain_first"))
    try:
        projects = await build_project_context(session, ws.id, req.project_ids)
        targets = await issue_targets(session, ws.id, req.project_ids, req.source_analysis_id, req.source_issue_ids)
        if targets and (req.kind != 'supplement' or req.count < len(targets)):
            raise ValueError('项目问题考核请使用补题模式，题量至少覆盖每个所选问题')
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    source_text = req.source_text.strip()
    source_questions = []
    if req.source_import_id is not None:
        imp = await session.scalar(select(ImportSession).where(ImportSession.id == req.source_import_id, ImportSession.workspace_id == ws.id))
        if not imp:
            raise HTTPException(404, localized_message("errors.source_import_session_not_found"))
        metadata = imp.meta or {}
        if not source_text:
            source_text = metadata.get("extracted_source_text") or metadata.get("pasted_text") or imp.paste_text or ""
            if not source_text or (metadata.get("source") == "files" and not metadata.get("extracted_source_text")):
                raise HTTPException(422, localized_message("errors.this_older_import_has_no_full_source_text_paste"))
        for it in imp.items:
            source_id = it.question_id or it.merged_into or it.is_duplicate_of
            if source_id:
                q = await session.get(Question, source_id)
                if q and q.workspace_id in (None, ws.id) and (q.generation_meta or {}).get('assessment_use') != 'PRACTICE_ONLY':
                    source_questions.append({"id": q.id, "title": q.title, "canonical_question_id": q.canonical_question_id})
    if req.kind == "import" and not source_text:
        raise HTTPException(422, localized_message("errors.paste_an_interview_account_or_select_an_existing_import"))
    if len(source_text) > 60000:
        raise HTTPException(422, localized_message("errors.the_source_is_over_60_000_characters_import_it"))
    keys = req.skill_keys or [s.key for s in skills]
    # Supply existing titles so the generator can avoid duplicates or explicitly
    # identify an adaptation. Bound context size; semantic dedup still needs review.
    relevant_ids = [s.id for s in skills if s.key in keys]
    known = (await session.execute(select(Question.id, Question.title, Question.canonical_question_id).where((Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id), Question.skill_id.in_(relevant_ids), Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY')).order_by(Question.id).limit(200))).all()
    source_ids = {q["id"] for q in source_questions}
    source_questions.extend({"id": qid, "title": title, "canonical_question_id": canonical} for qid, title, canonical in known if qid not in source_ids)
    snapshot = {"kind": req.kind, "count": req.count, "skill_keys": keys, "skills": [{"key": s.key, "name": s.name} for s in skills if s.key in keys], "project_context": projects, "source_text": source_text, "source_questions": source_questions, "purpose": req.purpose}
    snapshot.update(source_analysis_id=req.source_analysis_id, source_issue_ids=req.source_issue_ids,
                    project_issue_targets=targets, generation_language=request_language())
    snapshot["bank_review_feedback"] = await load_review_feedback(session, ws.id, keys)
    task = QuestionGeneration(workspace_id=ws.id, kind=req.kind, mode=req.mode, request_key=request_key, request_json=req.model_dump(exclude={"request_key"}), input_snapshot=snapshot, prompt_text="")
    session.add(task)
    await session.flush()
    task.prompt_text = protocol.build_prompt(snapshot, task.id)
    await session.commit()
    return await task_view(session, task)


@router.get("/{task_id}")
async def get_task(task_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    return await task_view(session, await owned_task(session, ws.id, task_id))


async def store_response(session, task, raw, result_meta=None):
    task.raw_response = raw
    task.result_meta = result_meta or {"source": task.mode}
    try:
        items = protocol.parse_response(raw, task.input_snapshot, task.id)
        rendered = [protocol.render_item(item, task.input_snapshot) for item in items]
    except ValueError as exc:
        task.status, task.error = "FAILED", str(exc)
        await session.commit()
        return
    imp = ImportSession(short_code=f"IM-G{task.id}", workspace_id=task.workspace_id, paste_text=task.input_snapshot.get("source_text"), status="REVIEW", meta={"generation_task_id": task.id, "source": "question_generation", "mode": task.mode, "purpose": task.input_snapshot.get("purpose")})
    session.add(imp)
    await session.flush()
    existing = (await session.scalars(select(Question).options(lazyload("*")).where((Question.workspace_id.is_(None)) | (Question.workspace_id == task.workspace_id), Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY')))).all()
    titles = {normalized_title(q.title): q.id for q in existing}
    draft_snapshot = task.input_snapshot.copy()
    draft_snapshot.pop("bank_review_feedback", None)
    for item, content in zip(items, rendered):
        group = hashlib.sha256(normalized_title(item["original_question"] or item["title"]).encode()).hexdigest()
        meta = {"task_id": task.id, "source": task.mode, "item": item, "group_key": group, "input_snapshot": draft_snapshot}
        session.add(ImportItem(session_id=imp.id, title=item["title"], suggested_skill_key=item["skill_key"], suggested_type="open", draft_answer=content["answer_html"], draft_rubric=content["rubric"], generation_meta=meta, is_duplicate_of=titles.get(normalized_title(item["title"])), status="DRAFT"))
    task.import_session_id = imp.id
    task.status, task.error = "REVIEW", None
    await session.commit()


@router.post("/{task_id}/result")
async def submit_result(task_id: int, req: ResultIn, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    task = await owned_task(session, ws.id, task_id, lock=True)
    if task.mode != "external":
        raise HTTPException(409, localized_message("errors.use_the_generate_button_for_an_api_job"))
    if task.import_session_id:
        if req.text != task.raw_response:
            raise HTTPException(409, localized_message("errors.this_batch_is_already_in_review_create_a_new"))
    else:
        await store_response(session, task, req.text)
    return await task_view(session, task)


async def _run(task_id: int, workspace_id: int):
    try:
        async with SessionLocal() as session:
            task = await owned_task(session, workspace_id, task_id)
            prompt = task.prompt_text
        result = await protocol.generate_response(prompt)
        async with SessionLocal() as session:
            task = await owned_task(session, workspace_id, task_id, lock=True)
            if task.status == "RUNNING" and not task.import_session_id:
                await store_response(session, task, result["text"], {key: value for key, value in result.items() if key != "text"})
    except Exception:
        # Provider exceptions may contain credentials or headers: retain safe actionable text only.
        async with SessionLocal() as session:
            task = await owned_task(session, workspace_id, task_id, lock=True)
            if task.status == "RUNNING":
                task.status = "FAILED"
                task.error = generation_message("draft.generation_failed", saved_language(task.input_snapshot.get("generation_language")))
                await session.commit()


@router.post("/{task_id}/run")
async def run_task(task_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    task = await owned_task(session, ws.id, task_id, lock=True)
    if task.mode != "api":
        raise HTTPException(409, localized_message("errors.paste_the_result_from_the_external_chat"))
    if task.status != "RUNNING" and not task.import_session_id:
        task.status, task.error = "RUNNING", None
        await session.commit()
        runner = asyncio.create_task(_run(task.id, ws.id))
        _running.add(runner)
        runner.add_done_callback(_running.discard)
    return await task_view(session, task)


@router.patch("/{task_id}/items/{item_id}")
async def edit_item(task_id: int, item_id: int, req: ItemEdit, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    from app.services.bank_review_actions import bump_review_revision, ensure_not_revising, review_view

    task = await owned_task(session, ws.id, task_id, lock=True)
    await workspace_lock(session, ws.id)
    item = await session.scalar(select(ImportItem).where(ImportItem.id == item_id, ImportItem.session_id == task.import_session_id).with_for_update())
    if item is None:
        raise HTTPException(404, localized_message("errors.draft_not_found"))
    if item.status != "DRAFT":
        raise HTTPException(409, localized_message("errors.only_drafts_awaiting_review_can_be_edited"))
    ensure_not_revising(item)
    if req.revision is not None and req.revision != review_view(item)["review_revision"]:
        raise HTTPException(409, localized_message("errors.the_draft_has_changed_reopen_it_before_editing"))
    old = (item.generation_meta or {}).get("item", {})
    if req.item.get("original_question") != old.get("original_question") or req.item.get("source_question_id") != old.get("source_question_id"):
        raise HTTPException(422, localized_message("errors.editing_an_answer_cannot_change_the_original_question_or"))
    issue_refs = {t['fact_ref'] for t in task.input_snapshot.get('project_issue_targets') or []}
    if issue_refs.intersection(req.item.get('fact_refs') or []) != issue_refs.intersection(old.get('fact_refs') or []):
        raise HTTPException(422, localized_message("errors.create_a_new_batch_to_change_the_project_question"))
    try:
        validated = protocol.parse_response(json.dumps({"schema_version": 1, "batch_id": task.id, "items": [req.item]}, ensure_ascii=False), {**task.input_snapshot, "count": 1}, task.id, require_all_targets=False)[0]
        content = protocol.render_item(validated, task.input_snapshot)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    item.title, item.suggested_skill_key = validated["title"], validated["skill_key"]
    item.draft_answer, item.draft_rubric = content["answer_html"], content["rubric"]
    item.generation_meta = {**item.generation_meta, "item": validated, "user_edited": True}
    bump_review_revision(item)
    # Editing title cannot retain a stale duplicate marker.
    rows = (await session.execute(select(Question.id, Question.title).where((Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id), Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY')))).all()
    item.is_duplicate_of = next((qid for qid, title in rows if normalized_title(title) == normalized_title(item.title)), None)
    await session.commit()
    from app.api.v1.bank import _item_view
    return _item_view(item)


async def recover_interrupted():
    async with SessionLocal() as session:
        error = case(
            (QuestionGeneration.input_snapshot["generation_language"].as_string() == "en",
             generation_message("draft.generation_interrupted", "en")),
            else_=generation_message("draft.generation_interrupted", "zh-CN"),
        )
        await session.execute(update(QuestionGeneration).where(
            QuestionGeneration.status == "RUNNING",
        ).values(status="FAILED", error=error))
        await session.commit()
