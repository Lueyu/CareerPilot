"""面经库 API: 题库浏览 + 导入流水线(解析→审核→入题库)。"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import time
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.api.deps import get_workspace
from app.db import SessionLocal, get_session
from app.models import Exam, ImportItem, ImportSession, Project, Question, Skill, StudyInterviewQuestion, StudySession, Trace, Workspace
from app.models.question import STUDY_CHAT_REVIEW_META
from app.services.answer_gen import generate_answer
from app.services.answer_guides import current_reference, example_for, for_question as teaching_guide_for
from app.services import bank_review_actions as review_actions
from app.services.experienced_bank import is_owned_question, list_installed_pack, owned_question_clause
from app.services.interview_rewrite import rewrite_interview_questions
from app.services.llm import AgentAdapter
from app.services.project_context import PROFILE_LABELS, project_profile
from app.services.text_extract import MAX_FILE_BYTES, MAX_TEXT_CHARS, extract_many
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()

# 旧题库 topic → 新能力域 key 映射(与 seed 一致)
# 新能力域 key → 旧题库主题(仅用于列表展示 chip)
SKILL_TOPIC = {
    "agent": "harness",
    "tools": "harness",
    "memory": "ctx",
    "rag": "rag",
    "eval": "eval",
    "backend": "infer",
    "reliability": "sys",
    "deploy": "eval",
}

TOPIC_SKILL = {
    "rag": "rag",
    "harness": "agent",
    "ctx": "rag",
    "infer": "backend",
    "eval": "eval",
    "sys": "reliability",
    "algo": "backend",
    "biz": "tools",
}


async def _skill_map(session: AsyncSession) -> dict[str, Skill]:
    skills = (await session.execute(select(Skill))).scalars().all()
    return {s.key: s for s in skills}


def _study_review_clause(workspace_id: int):
    return (Question.workspace_id == workspace_id) & Question.generation_meta.contains(STUDY_CHAT_REVIEW_META)


def _with_visible_study_session(query, workspace_id: int):
    return (
        query.add_columns(StudySession.id)
        .outerjoin(
            StudyInterviewQuestion,
            (StudyInterviewQuestion.question_id == Question.id) & _study_review_clause(workspace_id),
        )
        .outerjoin(
            StudySession,
            (StudySession.id == StudyInterviewQuestion.session_id)
            & (StudySession.workspace_id == workspace_id)
            & StudySession.hidden_at.is_(None),
        )
    )


@router.get("/bank")
async def list_bank(
    status: str = "all",
    company: str | None = None,
    topic: str | None = None,
    skill: str | None = None,
    q: str | None = None,
    companies: Annotated[list[str] | None, Query()] = None,
    topics: Annotated[list[str] | None, Query()] = None,
    sources: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 60,
    offset: Annotated[int, Query(ge=0)] = 0,
    scope: str | None = None,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """List public and workspace questions, or only workspace questions with scope=mine."""
    workspace_filter = Question.workspace_id == ws.id
    if scope != "mine":
        workspace_filter = Question.workspace_id.is_(None) | workspace_filter
    base = select(Question).where(
        workspace_filter,
        Question.canonical_question_id.is_(None),  # 变体不单独列出
        (Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY')
         | _study_review_clause(ws.id)),
    )
    company_filter = (Question.company.in_(companies) if companies else
                      Question.company.icontains(company, autoescape=True) if company else True)
    topic_filter = Question.topic.in_(topics or [topic]) if topics or topic else True
    source_filter = Question.source_type.in_(sources) if sources else True
    if skill:
        sm = await _skill_map(session)
        sk = sm.get(skill)
        if sk:
            base = base.where(Question.skill_id == sk.id)
    if q:
        base = base.where(Question.title.icontains(q.strip(), autoescape=True))
    filtered = base.where(company_filter, topic_filter, source_filter)
    total = (await session.execute(select(func.count()).select_from(filtered.subquery()))).scalar() or 0
    rows = (
        await session.execute(
            _with_visible_study_session(filtered, ws.id).order_by(Question.id).offset(offset).limit(limit)
        )
    ).all()

    # 公司聚合(左侧过滤栏)
    companies = (
        await session.execute(
            select(Question.company, func.count(Question.id))
            .where(base.where(topic_filter, source_filter).whereclause)
            .group_by(Question.company)
            .order_by(func.count(Question.id).desc(), Question.company)
        )
    ).all()
    topics = (
        await session.execute(
            select(Question.topic, func.count(Question.id))
            .where(base.where(company_filter, source_filter).whereclause)
            .group_by(Question.topic)
            .order_by(Question.topic)
        )
    ).all()
    source_counts = (await session.execute(select(Question.source_type, func.count(Question.id))
        .where(base.where(company_filter, topic_filter).whereclause).group_by(Question.source_type)
        .order_by(Question.source_type))).all()

    return {
        "total": total,
        "items": [
            {
                "id": r.id,
                "short_code": r.short_code,
                "title": r.title,
                "qtype": r.qtype,
                "topic": r.topic,
                "skill": r.skill.name if r.skill else None,
                "skill_key": r.skill.key if r.skill else None,
                "difficulty": r.difficulty,
                "company": r.company,
                "source_type": r.source_type,
                "review_only": r.is_study_chat_review,
                "study_session_id": study_session_id,
                "tags": r.tags or [],
                "occurred_on": r.occurred_on.isoformat() if r.occurred_on else None,
            }
            for r, study_session_id in rows
        ],
        "facets": {
            "companies": [{"name": c, "count": n} for c, n in companies if c],
            "topics": [{"name": t, "count": n} for t, n in topics if t],
            "sources": [{"name": source, "count": count} for source, count in source_counts],
        },
    }


@router.get("/bank/interview-sets")
async def list_interview_sets(
    ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session),
):
    imports = (await session.scalars(select(ImportSession).where(
        ImportSession.workspace_id == ws.id,
        ImportSession.meta['interview_sets'].as_string().is_not(None),
    ).order_by(ImportSession.id.desc()))).all()
    return {"items": [entry for imported in imports for entry in reversed((imported.meta or {}).get('interview_sets', []))]}


@router.get("/bank/experienced")
async def list_experienced_bank(
    _ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session),
):
    return await list_installed_pack(session)


@router.get("/bank/{short_code}")
async def bank_detail(
    short_code: str,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    result = (
        await session.execute(
            _with_visible_study_session(select(Question), ws.id)
            .options(joinedload(Question.skill), selectinload(Question.variants))
            .where(
                Question.short_code == short_code,
                (Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id),
                (Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY')
                 | owned_question_clause() | _study_review_clause(ws.id)),
            )
        )
    ).one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="question not found")
    question, study_session_id = result
    from app.services.learning_exposure import record_reference_exposure
    teaching_guide = teaching_guide_for(question)
    beginner_example = example_for(question)
    if question.answer_html or question.rubric or teaching_guide or beginner_example:
        await record_reference_exposure(session, ws, question, source='bank_detail')
        await session.commit()
    return {
        "id": question.id,
        "short_code": question.short_code,
        "title": question.title,
        "qtype": question.qtype,
        "topic": question.topic,
        "skill": question.skill.name if question.skill else None,
        "difficulty": question.difficulty,
        "options": question.options,
        "answer_html": current_reference(question),
        "beginner_example": beginner_example,
        "teaching_guide": teaching_guide,
        "rubric": question.rubric,
        "company": question.company,
        "role": question.role,
        "level": question.level,
        "occurred_on": question.occurred_on.isoformat() if question.occurred_on else None,
        "source_type": question.source_type,
        "review_only": question.is_study_chat_review,
        "study_session_id": study_session_id,
        "tags": question.tags or [],
        "keywords": question.keywords or [],
        "generation_meta": question.generation_meta,
        "variants": [v.short_code for v in question.variants if v.workspace_id in (None, ws.id)
                     and (v.generation_meta or {}).get('assessment_use') != 'PRACTICE_ONLY'],
    }


# ---------------- 导入流水线 ----------------

class StartImportReq(BaseModel):
    pasted_text: str | None = None
    file_names: list[str] | None = None


class ApproveItemReq(BaseModel):
    item_ids: list[int] | None = None  # None=全部通过
    edits: dict[int, dict] | None = None  # item_id → {title, skill_key, draft_rubric...}


@router.post("/bank/imports")
async def start_import(
    req: StartImportReq,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """粘贴文本导入: 立即建导入会话并返回, 流水线后台执行, 前端轮询真实进度。"""
    source_text = (req.pasted_text or "").strip() or "\n".join(req.file_names or [])
    if not source_text:
        raise HTTPException(status_code=400, detail="empty import: pasted_text or file_names required")
    return await _start_import_session(
        session,
        ws,
        paste_text=source_text,
        filename=(req.file_names or [None])[0],
        meta={"source": "pasted" if req.pasted_text else "files", "chars": len(source_text), "files": [], "pasted_text": source_text},
        raw_items=[],
    )


@router.post("/bank/imports/upload")
async def start_import_upload(
    files: list[UploadFile] = File(default_factory=list),
    pasted_text: str = Form(default=""),
    company: str = Form(default=""),
    level: str = Form(default=""),
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """拖拽/选择文件上传(PDF / DOCX / Markdown / TXT / JPG / PNG) + 可选粘贴文本。

    立即建导入会话并返回(耗时解析在后台执行), 前端轮询 /bank/imports/{id} 获取真实进度。
    每个文件独立解析: 成功的进入流水线, 失败的把原因回给前端展示, 不影响其他文件。
    """
    raw_items: list[tuple[str, bytes]] = []
    if len(files) > 12 or len(pasted_text) > MAX_TEXT_CHARS:
        raise HTTPException(413, "每次最多上传 12 个文件，粘贴文本最多 60,000 字符")
    for f in files:
        if not f or not f.filename:
            continue
        raw_items.append((f.filename, await f.read(MAX_FILE_BYTES + 1)))

    if not raw_items and not (pasted_text or "").strip():
        raise HTTPException(status_code=400, detail="没有可解析的内容: 请选择文件或粘贴文本")

    basic_meta = [
        {"name": name, "suffix": name.rsplit(".", 1)[-1].lower() if "." in name else "", "size": len(raw), "ok": True, "chars": 0}
        for name, raw in raw_items
    ]
    return await _start_import_session(
        session,
        ws,
        paste_text=(pasted_text or "").strip() or None,
        filename=", ".join(name for name, _ in raw_items)[:255] or None,
        meta={
            "source": "uploads" if raw_items else "pasted",
            "files": basic_meta,
            "company": company or None,
            "level": level or None,
            "pasted_text": (pasted_text or "").strip() or None,
        },
        raw_items=raw_items,
    )


# 后台导入任务注册表: 防止 asyncio.Task 被 GC, 结束后移除
_BG_IMPORT_TASKS: set[asyncio.Task] = set()

IMPORT_STEPS = ["解析文档", "提取实体", "技能映射", "去重", "生成草稿"]


async def _start_import_session(
    session: AsyncSession,
    ws: Workspace,
    *,
    paste_text: str | None,
    filename: str | None,
    meta: dict,
    raw_items: list[tuple[str, bytes]],
) -> dict:
    """建 ImportSession(status=UPLOADED) → 提交 → 后台跑流水线 → 立即返回。"""
    imp = ImportSession(
        short_code=f"IM-{uuid4().hex[:24]}",
        workspace_id=ws.id,
        filename=filename,
        paste_text=(paste_text or "") or None,
        status="UPLOADED",
        meta={**meta, "progress": {"step": 0, "total": 5, "note": "已接收，排队解析中"}},
    )
    session.add(imp)
    await session.commit()

    task = asyncio.create_task(
        _process_import(
            imp.id,
            ws.id,
            raw_items=raw_items,
            paste_text=paste_text or "",
            filename=filename,
        )
    )
    _BG_IMPORT_TASKS.add(task)
    task.add_done_callback(_BG_IMPORT_TASKS.discard)

    return {
        "id": imp.id,
        "short_code": imp.short_code,
        "status": imp.status,
        "meta": imp.meta,
        "items": [],
    }


async def _process_import(
    import_id: int,
    workspace_id: int,
    *,
    raw_items: list[tuple[str, bytes]],
    paste_text: str,
    filename: str | None,
) -> None:
    """后台导入流水线(独立 DB 会话): 解析→提取→技能映射→去重→生成草稿。

    每完成一个阶段就把 meta.progress.step 落库并 commit, 前端轮询看到的是真实进度。
    """
    session = SessionLocal()
    trace: Trace | None = None
    t0 = time.perf_counter()
    try:
        ws = (
            await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        ).scalar_one()
        trace = await start_trace(
            session, workspace_id, "bank_import", "导入面经: 上传与解析",
            short_code=await _unique_trace_code(session, f"TR-I{int(t0)}"),
        )

        async def advance(step: int, note: str) -> None:
            imp = (await session.execute(select(ImportSession).where(ImportSession.id == import_id))).scalar_one()
            imp.status = "PARSING"
            imp.meta = {**(imp.meta or {}), "progress": {"step": step, "total": 5, "note": note}}
            await session.commit()

        # 阶段 0: 解析文档(文件 OCR/Office 解析是最慢的一步; to_thread 避免阻塞事件循环)
        paste_text = paste_text.strip()
        text_parts = [("粘贴文本", paste_text)] if paste_text else []
        if raw_items:
            t_stage = time.perf_counter()
            extracted = await asyncio.to_thread(extract_many, raw_items)
            parse_ms = int((time.perf_counter() - t_stage) * 1000)
            file_meta = [x.as_meta() for x in extracted.files]
            ok_names = [f.name for f in extracted.ok_files]
            text_parts.extend((f.name, f.text) for f in extracted.ok_files)
        else:
            file_meta, ok_names = [], []
        source_separator = "\n\n"
        source_text = source_separator.join(text for _, text in text_parts)[:MAX_TEXT_CHARS].strip()
        imported = await session.get(ImportSession, import_id)
        imported.meta = {**(imported.meta or {}), "files": file_meta, "extracted_source_text": source_text}
        await advance(1, f"已解析 {len(file_meta) or '文本'} · {len(source_text)} 字符")
        if not source_text:
            detail = "没有可解析的内容"
            if file_meta:
                detail = "; ".join(f"{m['name']}: {m['error']}" for m in file_meta if m["error"]) or detail
            raise RuntimeError(detail)
        add_span(session, trace, "parse", parse_ms if raw_items else 5, detail={"chars": len(source_text), "files": len(file_meta)})

        # 阶段 1: 提取题目(规则/LLM)
        t_stage = time.perf_counter()
        candidates = []
        by_title = {}
        remaining_chars = MAX_TEXT_CHARS
        for name, text in text_parts:
            if remaining_chars <= 0:
                break
            text = text[:remaining_chars]
            remaining_chars -= len(text) + len(source_separator)
            if not text.strip():
                continue
            for candidate in await _extract_questions(text):
                key = re.sub(r"\W+", "", candidate["title"]).casefold()
                if key in by_title:
                    names = by_title[key]["source_files"]
                    if name not in names:
                        names.append(name)
                else:
                    candidate["source_files"] = [name]
                    by_title[key] = candidate
                    candidates.append(candidate)
        candidates = candidates[:20]
        if not candidates:
            raise RuntimeError("已读取文字，但没有识别到面试题；请检查截图是否清楚，或粘贴原题。")
        extract_ms = int((time.perf_counter() - t_stage) * 1000)
        await advance(2, f"提取到 {len(candidates)} 道候选题")
        add_span(session, trace, "extract", extract_ms, detail={"candidates": len(candidates)})

        # 阶段 2: 技能映射(映射在提取阶段已打标, 此处加载技能表并核对)
        sm = await _skill_map(session)
        all_questions = (
            await session.execute(
                select(Question.id, Question.title).where(
                    (Question.workspace_id.is_(None)) | (Question.workspace_id == workspace_id),
                    Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY')
                )
            )
        ).all()
        title_map = {t.strip().lower(): qid for qid, t in all_questions}
        unknown_skills = {c["skill_key"] for c in candidates if c["skill_key"] not in sm}
        await advance(3, f"映射到 {len(sm)} 个能力域" + (f"，{len(unknown_skills)} 个未知域回退" if unknown_skills else ""))
        add_span(session, trace, "map", 15, detail={"skills": len(sm), "fallback": len(unknown_skills)})

        # 阶段 3: 去重(与现有题库按标题比对)
        dup_map: dict[int, int | None] = {}
        n_dup = 0
        for i, c in enumerate(candidates):
            dup_id = title_map.get(c["title"].strip().lower())
            dup_map[i] = dup_id
            if dup_id:
                n_dup += 1
        await advance(4, f"发现 {n_dup} 道重复题" if n_dup else "无重复题")
        add_span(session, trace, "dedup", 20, detail={"duplicates": n_dup})

        # 阶段 4: 为缺答案的题生成详细讲解(LLM 优先, 未配置则规则模板), 再写导入项
        # 重复题直接复用原题答案, 不浪费 LLM 调用(审核时合并)
        need_answer = [
            c for i, c in enumerate(candidates)
            if not (c.get("draft_answer") or "").strip() and not dup_map[i]
        ]
        t_stage = time.perf_counter()
        filled = await _fill_answers(session, need_answer, sm, workspace_id=workspace_id)
        answer_ms = int((time.perf_counter() - t_stage) * 1000)
        add_span(session, trace, "answer", answer_ms, detail={"generated": filled})

        n_new = 0
        for i, c in enumerate(candidates):
            item = ImportItem(
                session_id=import_id,
                title=c["title"],
                extract_confidence=c["confidence"],
                suggested_skill_key=c["skill_key"],
                draft_answer=c.get("draft_answer", ""),
                draft_rubric=c.get("rubric"),
                generation_meta={"source_files": c["source_files"], "extraction_method": c["extraction_method"]},
                is_duplicate_of=dup_map[i],
                status="DRAFT",
            )
            session.add(item)
            if not dup_map[i]:
                n_new += 1
        add_span(session, trace, "draft", 30, detail={"drafts": n_new})

        imp = (await session.execute(select(ImportSession).where(ImportSession.id == import_id))).scalar_one()
        imp.status = "REVIEW"
        imp.meta = {
            **(imp.meta or {}),
            "source": "uploads" if ok_names else "pasted",
            "chars": len(source_text),
            "files": file_meta,
            "progress": {"step": 5, "total": 5, "note": f"提取 {len(candidates)} 道题 · 待审核"},
        }
        if filename and ok_names:
            imp.filename = ", ".join(ok_names)[:255]
        await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000), 0.08)
        await emit_event(session, workspace_id, "import.review_ready", {"import_id": import_id}, "import", import_id)
        await session.commit()
    except Exception as e:  # noqa: BLE001 — 后台任务必须自己兜底, 状态可见
        await session.rollback()
        try:
            imp = (
                await session.execute(select(ImportSession).where(ImportSession.id == import_id))
            ).scalar_one()
            imp.status = "FAILED"
            imp.meta = {**(imp.meta or {}), "error": str(e)[:500], "progress": {"step": 0, "total": 5, "note": "解析失败"}}
            if trace is not None:
                await finish_trace(session, trace, "FAILED", int((time.perf_counter() - t0) * 1000), 0.0, fail_reason=str(e)[:300])
            await emit_event(session, workspace_id, "import.failed", {"import_id": import_id, "error": str(e)[:300]}, "import", import_id)
            await session.commit()
        except Exception:  # noqa: BLE001 — 兜底失败只能记日志
            await session.rollback()
    finally:
        await session.close()


@router.get("/bank/imports/pending")
async def pending_imports(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """待审核导入(REVIEW 状态会话)。注意: 必须先于 /bank/imports/{import_id} 注册。"""
    sessions = (
        await session.execute(
            select(ImportSession)
            .where(ImportSession.workspace_id == ws.id, ImportSession.status == "REVIEW")
            .order_by(ImportSession.id.desc())
        )
    ).scalars().all()
    result = []
    for s in sessions:
        result.append(await _import_review(session, ws, s.id))
    return {"imports": result}


@router.get("/bank/imports/{import_id}")
async def import_status(
    import_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """导入会话状态/进度轮询: status=UPLOADED|PARSING|REVIEW|FAILED; REVIEW 时返回完整 items。"""
    return await _import_review(session, ws, import_id)


async def _fill_answers(session: AsyncSession, candidates: list[dict], sm: dict[str, Skill], *, workspace_id: int | None = None) -> int:
    """为没有答案的题目生成讲解, 直接写入 candidate 的 draft_answer / rubric。"""
    todo = candidates[:20]
    if not todo:
        return 0
    hints_by_skill = {}
    if workspace_id is not None:
        from app.services.bank_review_memory import load_review_feedback

        skill_keys = {candidate.get("skill_key") or "agent" for candidate in todo}
        for hint in await load_review_feedback(session, workspace_id, skill_keys):
            hints_by_skill.setdefault(hint["skill_key"], []).append(hint)
    sem = asyncio.Semaphore(8)

    async def work(c: dict) -> None:
        skill_key = c.get("skill_key") or "agent"
        skill = sm.get(skill_key)
        skill_name = skill.name if skill else skill_key
        hints = hints_by_skill.get(skill_key)
        options = {"review_feedback": hints} if hints else {}
        async with sem:
            html, rubric, source = await generate_answer(c["title"], skill_key, skill_name, "open", **options)
        c["draft_answer"] = html
        c["rubric"] = {**rubric, "source": source}
        c["answer_source"] = source

    await asyncio.gather(*(work(c) for c in todo))
    return len(todo)


SKILL_KEYS = ("agent", "tools", "memory", "rag", "eval", "backend", "reliability", "deploy")


def _parse_llm_questions(content: str) -> list[dict]:
    """把 LLM 返回的 JSON 文本(可能带 ``` 围栏 / 包在对象里)解析成题目列表。"""
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|```$", "", text, flags=re.MULTILINE).strip()
    data: Any
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 容错: 截取第一个 [ 或 { 到最后一个 ] 或 }
        start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=-1)
        end = max(text.rfind("]"), text.rfind("}"))
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return []
    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for x in items:
        if not isinstance(x, dict) or not str(x.get("title") or "").strip():
            continue
        skill = str(x.get("skill_key") or "").strip().lower()
        title = str(x["title"]).strip()[:200]
        out.append(
            {
                "title": title,
                "skill_key": skill if skill in SKILL_KEYS else _guess_skill(title),
                "confidence": float(x.get("confidence") or 0.8),
                "draft_answer": str(x.get("draft_answer") or "").strip(),
                "company": str(x.get("company") or "").strip() or None,
                "level": str(x.get("level") or "").strip() or None,
                "occurred_on": str(x.get("occurred_on") or "").strip() or None,
            }
        )
    return out[:20]


QA_HEAD = re.compile(r"^\s*(?:问|问题|Q|q)\s*[:：]\s*(.+)$")
NUMBERED_HEAD = re.compile(r"^\s*[0-9]+[.、]\s*(\S.+)$")


def _extract_structured_questions(text: str) -> list[dict]:
    """按原文顺序提取问答和编号题，保留问答的原文答案。"""
    items: list[dict] = []
    cur_q: str | None = None
    buf: list[str] = []
    has_qa = False
    for ln in text.splitlines():
        qa = QA_HEAD.match(ln)
        numbered = NUMBERED_HEAD.match(ln)
        title = numbered.group(1).strip() if numbered and 6 <= len(ln.strip()) <= 120 else ""
        # 答案里的编号步骤可能叙述“检查系统是如何工作的”。
        numbered_question = title and (
            cur_q is None or title.endswith(("?", "？"))
            or (title.startswith(("什么", "如何", "怎么", "为什么")) and not title.endswith(("。", ".")))
        )
        if qa or numbered_question:
            if cur_q and len(cur_q) >= 6:
                items.append({"title": cur_q[:200], "answer": "\n".join(buf).strip(), "confidence": 0.7})
                has_qa = True
            cur_q, buf = None, []
            if qa:
                cur_q = qa.group(1).strip()
            else:
                items.append({"title": title, "answer": "", "confidence": 0.55 if title.endswith(("?", "？")) else 0.4})
            continue
        if cur_q is not None:
            if ln.strip().startswith(("答", "A:", "A：", "a:")):
                ln = ln.split("：", 1)[-1].split(":", 1)[-1].strip()
            buf.append(ln)
    if cur_q and len(cur_q) >= 6:
        items.append({"title": cur_q[:200], "answer": "\n".join(buf).strip(), "confidence": 0.7})
        has_qa = True
    return items if has_qa else []


async def _extract_questions(text: str) -> list[dict]:
    """LLM 优先。长文本或 LLM 无结果时，用规则扫描全文。"""
    prompt_limit = 8000
    model_candidates = []
    adapter = AgentAdapter()
    if adapter.active:
        sys_prompt = (
            "你是面经解析引擎。从用户粘贴的面试记录中提取面试题。"
            '返回 JSON 对象 {"questions": [{"title": str, '
            '"skill_key": one of agent/tools/memory/rag/eval/backend/reliability/deploy, '
            '"confidence": 0-1, "draft_answer": str(原文中的答案要点, 没有就留空), '
            '"company": str, "level": str}]}。不要解释。'
        )
        try:
            out = await adapter.chat(sys_prompt, text[:prompt_limit], json_mode=True)
            if out and out.get("content"):
                parsed = _parse_llm_questions(out["content"])
                if parsed:
                    model_candidates = [{**item, "extraction_method": "model"} for item in parsed]
                    if len(text) <= prompt_limit:
                        return model_candidates
        except Exception:
            pass

    # 规则: 按原文顺序识别问答和编号题
    structured = _extract_structured_questions(text)
    if structured:
        return model_candidates + [
            {
                "title": p["title"],
                "skill_key": _guess_skill(p["title"]),
                "confidence": p["confidence"],
                "draft_answer": p["answer"],
                "extraction_method": "rules",
            }
            for p in structured[:20]
        ]

    # 规则降级: 按行找题目特征
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    results = []
    for ln in lines:
        looks_like_q = (
            ln.endswith("?") or ln.endswith("？")
            or any(ln.startswith(p) for p in ("1.", "2.", "3.", "4.", "5.", "Q", "问", "-"))
            or "什么" in ln or "如何" in ln or "怎么" in ln or "为什么" in ln
        )
        if looks_like_q and 6 <= len(ln) <= 120:
            results.append(
                {
                    "title": ln.lstrip("0123456789.-、Q: ")[:120],
                    "skill_key": _guess_skill(ln),
                    "confidence": 0.55 if ln.endswith(("?", "？")) else 0.4,
                    "draft_answer": "",
                    "extraction_method": "rules",
                }
            )
    return model_candidates + results[:20]


SKILL_KEYWORDS: list[tuple[str, list[str]]] = [
    # 顺序即优先级: 命中数量相同时, 靠前的赢(越具体越靠前)
    ("deploy", ["部署", "k8s", "监控", "observability", "可观测", "埋点", "trace", "日志", "告警"]),
    ("eval", ["eval", "评估", "评测", "测试", "benchmark", "指标", "打分"]),
    ("tools", ["tool", "工具", "mcp", "function call", "调用"]),
    ("memory", ["memory", "记忆", "状态管理", "上下文压缩", "会话"]),
    ("rag", ["rag", "检索", "向量", "embedding", "重排", "知识库", "上下文"]),
    ("reliability", ["安全", "失败", "重试", "幂等", "可靠", "异常", "幻觉", "注入", "超时"]),
    ("agent", ["agent", "智能体", "规划", "multi", "react", "harness", "任务分解"]),
    ("backend", ["api", "后端", "并发", "数据库", "算法", "复杂度", "系统设计", "缓存"]),
]


def _guess_skill(text: str) -> str:
    """按关键词命中数打分; 同分时按 SKILL_KEYWORDS 的优先级(越具体越靠前)取第一个。"""
    low = text.lower()
    best_key, best_score = "agent", 0
    for key, kws in SKILL_KEYWORDS:
        score = sum(1 for k in kws if k in low)
        if score > best_score:
            best_key, best_score = key, score
    return best_key


async def _import_review(session: AsyncSession, ws: Workspace, import_id: int) -> dict:
    imp = (
        await session.execute(
            select(ImportSession).where(ImportSession.id == import_id, ImportSession.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if imp is None:
        raise HTTPException(status_code=404, detail="import not found")
    items = (
        await session.execute(select(ImportItem).where(ImportItem.session_id == imp.id).order_by(ImportItem.id))
    ).scalars().all()
    return {
        "id": imp.id,
        "short_code": imp.short_code,
        "status": imp.status,
        "meta": imp.meta,
        "items": [_item_view(it) for it in items],
    }


def _item_view(it: ImportItem) -> dict:
    rubric = it.draft_rubric if isinstance(it.draft_rubric, dict) else {}
    origin_meta = {key: value for key, value in (it.generation_meta or {}).items() if key != "bank_review"}
    return {
        "id": it.id,
        "title": it.title,
        "extract_confidence": it.extract_confidence,
        "suggested_skill_key": it.suggested_skill_key,
        "draft_answer": it.draft_answer,
        "draft_rubric": it.draft_rubric,
        "has_answer": bool(review_actions.visible_answer(it.draft_answer)),
        "has_reviewable_answer": review_actions.has_reviewable_answer(it),
        "answer_source": rubric.get("source"),
        "is_duplicate_of": it.is_duplicate_of,
        "generation_meta": origin_meta if it.generation_meta is not None else None,
        "question_id": it.question_id,
        "merged_into": it.merged_into,
        "status": it.status,
        **review_actions.review_view(it),
    }


class InterviewSetRequest(BaseModel):
    project_id: int
    item_ids: list[int] = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=120)
    request_id: str = Field(min_length=1, max_length=60)
    edits: dict[int, str] = Field(default_factory=dict, max_length=20)


class InterviewPaperRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=60)
    duration_minutes: int = Field(default=30, ge=1, le=240)


async def _owned_import(session, ws, import_id, *, lock=False):
    query = select(ImportSession).where(ImportSession.workspace_id == ws.id, ImportSession.id == import_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    imported = await session.scalar(query)
    if imported is None:
        raise HTTPException(404, "未找到导入记录")
    return imported


@router.post("/bank/imports/{import_id}/interview-sets", status_code=201)
async def create_interview_set(
    import_id: int, req: InterviewSetRequest,
    ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session),
):
    from app.services.study_project_evidence import collect_project_evidence

    imported = await _owned_import(session, ws, import_id)
    title = req.title.strip()
    item_ids = list(dict.fromkeys(req.item_ids))
    edits = {item_id: value.strip() for item_id, value in req.edits.items()}
    if any(item_id not in item_ids or not 6 <= len(text) <= 1000 for item_id, text in edits.items()):
        raise HTTPException(422, "修订只能用于已选原题，长度须为 6–1000 字")
    if not title:
        raise HTTPException(422, "请输入套题名称")
    if imported.status not in {"REVIEW", "APPROVED"} or (imported.meta or {}).get("generation_task_id"):
        raise HTTPException(409, "导入题目尚不可用于改写")
    prior = next((entry for entry in (imported.meta or {}).get('interview_sets', [])
                  if entry['request_id'] == req.request_id), None)
    if prior:
        if (prior['title'], prior['project_id'], prior['item_ids'], prior.get('edits', {})) != (title, req.project_id, item_ids, {str(key): value for key, value in edits.items()}):
            raise HTTPException(409, "此生成请求已用于另一套题目")
        return prior
    project = await session.scalar(select(Project).where(
        Project.workspace_id == ws.id, Project.id == req.project_id, Project.archived_at.is_(None),
    ))
    if project is None:
        raise HTTPException(404, "未找到可用项目")
    project_version = (project.generation, project.last_seen_sha, project.latest_valid_analysis_id, project.meta)
    by_id = {item.id: item for item in imported.items if item.status != ImportItem.STATUS_REJECTED}
    if any(item_id not in by_id for item_id in item_ids):
        raise HTTPException(422, "选择的原题不属于这个导入批次，或已被驳回")
    skills = await _skill_map(session)
    prepared = []
    facts = {
        f'P{project.id}-{key}': {'label': PROFILE_LABELS[key], 'provenance': 'user_statement', 'text': value[:600]}
        for key, value in project_profile(project).items() if value
    }
    personal_refs = list(facts)
    model_items = []
    for item_id in item_ids:
        item = by_id[item_id]
        skill = skills.get(item.suggested_skill_key) or skills.get('agent')
        if skill is None:
            raise HTTPException(409, "工作空间未配置题目能力域")
        original_title = edits.get(item_id, item.title)
        records = await collect_project_evidence(session, ws.id, [skill.id], project.id, original_title)
        evidence = next((record for record in records if record['provenance'] == 'repository_observation'
                         and record.get('revision') and record.get('path')), None)
        refs = list(personal_refs)
        if evidence:
            ref = f'R{item.id}'
            facts[ref] = {**evidence, 'text': evidence['text'][:350]}
            refs.append(ref)
        model_items.append({'item_id': item.id, 'original_question': original_title[:500], 'allowed_fact_refs': refs})
        prepared.append((item, skill, evidence, original_title))
    rewrites, fallback_reason = await rewrite_interview_questions(project.name, model_items, facts)
    imported = await _owned_import(session, ws, import_id, lock=True)
    entries = (imported.meta or {}).get('interview_sets', [])
    prior = next((entry for entry in entries if entry['request_id'] == req.request_id), None)
    if prior:
        if (prior['title'], prior['project_id'], prior['item_ids'], prior.get('edits', {})) != (title, req.project_id, item_ids, {str(key): value for key, value in edits.items()}):
            raise HTTPException(409, "此生成请求已用于另一套题目")
        return prior
    current = (await session.execute(select(Project.generation, Project.last_seen_sha, Project.latest_valid_analysis_id, Project.meta).where(
        Project.id == project.id, Project.workspace_id == ws.id, Project.archived_at.is_(None),
    ))).one_or_none()
    if current != project_version:
        raise HTTPException(409, "项目证据版本已更新，请重新生成")
    selected_items = (await session.scalars(select(ImportItem).where(
        ImportItem.session_id == import_id, ImportItem.id.in_(item_ids),
    ).order_by(ImportItem.id).with_for_update().execution_options(populate_existing=True))).all()
    if len(selected_items) != len(item_ids) or any(item.status == ImportItem.STATUS_REJECTED for item in selected_items):
        raise HTTPException(409, "原题审核状态已变化，请重新选择")
    set_id = uuid4().hex
    questions = []
    skipped = []
    for index, (item, skill, evidence, original_title) in enumerate(prepared, 1):
        code = f"IS-{set_id[:23]}-{index}"
        rewrite = rewrites.get(item.id)
        responsibility_ref = f'P{project.id}-responsibilities'
        refs = rewrite['fact_refs'] if rewrite else ([responsibility_ref] if responsibility_ref in facts else [])
        if not rewrite and evidence:
            refs.append(f'R{item.id}')
        sources = [{'ref': ref, **facts[ref]} for ref in refs]
        provenance = {"source_item_id": item.id, "original_title": original_title, "extracted_title": item.title,
                      "source_files": (item.generation_meta or {}).get('source_files', []),
                      "evidence": {key: value for key, value in evidence.items() if key != 'text'} if evidence and f'R{item.id}' in refs else None,
                      "fact_refs": sources, "rewrite_method": "model" if rewrite else "conditional_rules",
                      "rewrite_fallback_reason": None if rewrite else fallback_reason}
        if rewrite:
            prompt = rewrite['question']
        else:
            anchor = facts[responsibility_ref]['text'][:180] if responsibility_ref in refs else (evidence['label'] if evidence else '')
            context = f'参考资料「{anchor}」，' if anchor else ''
            prompt = (f"原面经题：{original_title}\n\n{context}假如「{project.name}」需要应对原问里的情形，"
                      "你会如何设计和验证？请区分本人已做的工作、仓库中能查到的行为，以及你的假设。")
            skipped.append({"source_item_id": item.id, "title": original_title, "reason": fallback_reason})
        points = [{"title": name, "keywords": words} for name, words in (
            ("定位可核查的实现并解释行为" if evidence else "区分假设与已有实现", [evidence['path'] if evidence else "假设", "实现"]),
            ("结合面经题解释取舍并区分缺口", ["取舍", "缺口"]),
            ("给出验证方法和预期结果", ["测试", "预期"]),
        )]
        question = Question(short_code=code, workspace_id=ws.id, title=prompt, qtype="open",
                            topic=SKILL_TOPIC.get(skill.key, 'harness'), skill_id=skill.id, difficulty=3,
                            source_type="GENERATED", is_demo=ws.is_demo, rubric={"points": points, "max_points": 5},
                            generation_meta={"assessment_use": "PRACTICE_ONLY", "interview_set_id": set_id, **provenance})
        session.add(question)
        await session.flush()
        questions.append({"id": question.id, "title": prompt, **provenance})
    entry = {"id": set_id, "title": title, "request_id": req.request_id, "import_id": import_id,
             "project_id": project.id, "project_name": project.name, "item_ids": item_ids,
             "questions": questions, "skipped": skipped, "edits": {str(key): value for key, value in edits.items()}}
    imported.meta = {**(imported.meta or {}), "interview_sets": [*entries, entry]}
    await session.commit()
    return entry


@router.post("/bank/imports/{import_id}/interview-sets/{set_id}/paper")
async def create_interview_paper(
    import_id: int, set_id: str, req: InterviewPaperRequest,
    ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session),
):
    from app.api.v1.exams import CreateExamReq, issue_exam

    imported = await _owned_import(session, ws, import_id)
    entry = next((item for item in (imported.meta or {}).get('interview_sets', []) if item['id'] == set_id), None)
    if entry is None:
        raise HTTPException(404, "未找到面试套题")
    return await issue_exam(CreateExamReq(kind=Exam.KIND_BANK,
        question_ids=[question['id'] for question in entry['questions']], duration_minutes=req.duration_minutes,
        idempotency_key=f"IS:{set_id}:{req.request_id}"), ws, session, interview_set=entry)


@router.post("/bank/imports/{import_id}/items/{item_id}/answer")
async def regenerate_item_answer(
    import_id: int,
    item_id: int,
    force: bool = True,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """为某条导入草稿(重新)生成详细解答。已有答案且 force=false 时直接返回。"""
    imported, item = await review_actions.lock_review_item(session, ws, import_id, item_id)
    if review_actions.generated_import(imported, item):
        raise HTTPException(409, "请在补题工作台编辑或新建批次，普通重新生成会丢失项目资料关联")
    if not force and (item.draft_answer or "").strip():
        return _item_view(item)
    if item.status != ImportItem.STATUS_DRAFT:
        raise HTTPException(409, "仅待审核草稿可以编辑")
    review_actions.ensure_not_revising(item)

    sm = await _skill_map(session)
    skill_key = item.suggested_skill_key or "agent"
    skill = sm.get(skill_key)
    title, skill_name = item.title, skill.name if skill else skill_key
    revision = review_actions.review_view(item)["review_revision"]
    await session.commit()
    answer_html, rubric, source = await generate_answer(title, skill_key, skill_name)
    _, item = await review_actions.lock_review_item(session, ws, import_id, item_id)
    if item.status != ImportItem.STATUS_DRAFT or review_actions.review_view(item)["review_revision"] != revision:
        raise HTTPException(409, "草稿已更新，请重新打开后编辑")
    review_actions.ensure_not_revising(item)
    item.draft_answer = answer_html
    item.draft_rubric = {**rubric, "source": source}
    review_actions.bump_review_revision(item)
    await session.commit()
    return _item_view(item)


@router.post("/bank/questions/{short_code}/answer")
async def generate_question_answer(
    short_code: str,
    force: bool = False,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """为题库中「没有答案」的题目补写详细解答并落库(已有答案默认不覆盖)。"""
    question = (
        await session.execute(
            select(Question)
            .options(joinedload(Question.skill))
            .where(
                Question.short_code == short_code,
                (Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id),
            )
        )
    ).scalar_one_or_none()
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")

    if force and is_owned_question(question):
        raise HTTPException(403, "默认题库为只读")
    if question.generation_meta and force:
        raise HTTPException(409, "此题来自项目补题工作台，请创建新批次以保留资料与来源")
    if (question.answer_html or "").strip() and not force:
        from app.services.learning_exposure import record_reference_exposure
        await record_reference_exposure(session, ws, question, source='bank_answer')
        await session.commit()
        return {
            "short_code": question.short_code,
            "answer_html": question.answer_html,
            "rubric": question.rubric,
            "generated": False,
        }

    if question.workspace_id is None:
        raise HTTPException(403, "共享题库只读；请在补题工作台创建个人版本")

    skill_key = question.skill.key if question.skill else _guess_skill(question.title)
    skill_name = question.skill.name if question.skill else skill_key
    answer_html, rubric, source = await generate_answer(question.title, skill_key, skill_name, question.qtype)
    question.answer_html = answer_html
    if not question.rubric or force:
        question.rubric = {**rubric, "source": source}
    from app.services.learning_exposure import record_reference_exposure
    await record_reference_exposure(session, ws, question, source='bank_answer_generated')
    await session.commit()
    return {
        "short_code": question.short_code,
        "answer_html": answer_html,
        "rubric": question.rubric,
        "generated": True,
        "source": source,
    }


async def _unique_trace_code(session: AsyncSession, base: str) -> str:
    """trace 编码加序号去重(重复审核/重置库后 id 会复用)。"""
    code, n = base, 1
    while (await session.execute(select(Trace.id).where(Trace.short_code == code))).first():
        n += 1
        code = f"{base}-{n}"
    return code


@router.post("/bank/imports/{import_id}/approve")
async def approve_import(
    import_id: int,
    req: ApproveItemReq,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """审核学习材料入私有题库；不生成能力证据。"""
    from app.api.v1.question_generation import workspace_lock

    await workspace_lock(session, ws.id)
    imp = await _owned_import(session, ws, import_id, lock=True)
    return await _approve_locked_import(session, ws, imp, req)


@router.post("/bank/imports/{import_id}/items/{item_id}/review")
async def review_import_item(
    import_id: int,
    item_id: int,
    req: review_actions.ReviewRequest,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    return await review_actions.review_import_item(session, ws, import_id, item_id, req)


async def _approve_locked_import(session, ws, imp, req, *, commit=True, require_review_content=False, generated=False):
    if generated or review_actions.generated_import(imp):
        from app.services.question_review import approve_generated

        return await approve_generated(session, ws, imp.id, req.item_ids, req.edits, locked_import=imp,
                                       commit=commit, require_review_content=require_review_content)
    t0 = time.perf_counter()
    trace = await start_trace(
        session, ws.id, "bank_import", "导入面经: 审核入库",
        short_code=await _unique_trace_code(session, f"TR-A{imp.id}"),
    )
    if imp.status not in ("REVIEW", "APPROVED"):
        raise HTTPException(status_code=409, detail=f"import status is {imp.status}")

    items = (
        await session.execute(
            select(ImportItem).where(ImportItem.session_id == imp.id, ImportItem.status == "DRAFT")
            .order_by(ImportItem.id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalars().all()
    if req.item_ids is not None:
        items = [it for it in items if it.id in req.item_ids]

    sm = await _skill_map(session)
    created = []
    for it in items:
        review_actions.ensure_not_revising(it)
        edits = (req.edits or {}).get(it.id, {})
        title = edits.get("title", it.title)
        skill_key = edits.get("skill_key", it.suggested_skill_key)
        skill = sm.get(skill_key or "") or sm.get("agent")
        if skill is None:
            raise HTTPException(409, "工作空间未配置题目能力域")
        title_changed = title != it.title
        if require_review_content:
            it.is_duplicate_of = await _find_import_duplicate(session, ws.id, title, it.is_duplicate_of)
        if it.is_duplicate_of and not title_changed:
            it.status = "MERGED"
            it.merged_into = it.is_duplicate_of
            if not require_review_content:
                review_actions.bump_review_revision(it)
            continue
        answer_html = edits.get("answer_html", it.draft_answer or "")
        rubric = edits.get("rubric", it.draft_rubric)
        # 兜底: 审核时仍然没有答案的题, 现场补一份(LLM 优先, 否则规则模板)
        replace_draft = title_changed and not (str(edits.get("answer_html") or "").strip() and edits.get("rubric"))
        if not require_review_content and (replace_draft or not str(answer_html or "").strip()):
            answer_html, auto_rubric, source = await generate_answer(
                title, skill.key if skill else "agent", skill.name if skill else "agent", edits.get("qtype", "open"),
            )
            rubric = {**auto_rubric, "source": source} if replace_draft or not rubric else rubric
        question = Question(
            short_code=edits.get("short_code", f"Q-U{imp.id}-{it.id}"),
            workspace_id=ws.id,
            title=title,
            qtype=edits.get("qtype", "open"),
            topic=edits.get("topic", SKILL_TOPIC.get(skill_key or "", "harness")),
            skill_id=skill.id,
            difficulty=edits.get("difficulty", 3),
            options=edits.get("options"),
            answer_html=answer_html,
            rubric=rubric or {"points": [{"title": "要点", "keywords": [title[:10]]}], "max_points": 5.0},
            company=edits.get("company", (imp.meta or {}).get("company") or "用户导入"),
            role=edits.get("role"),
            level=edits.get("level", (imp.meta or {}).get("level") or None),
            source_type="USER_PRIVATE",
            tags=edits.get("tags", []),
            is_demo=ws.is_demo,
            generation_meta={"source_import_id": imp.id, "source_item_id": it.id,
                             "source_files": (it.generation_meta or {}).get('source_files', []),
                             "extracted_title": it.title},
        )
        session.add(question)
        await session.flush()
        it.status = "APPROVED"
        it.question_id = question.id
        if not require_review_content:
            review_actions.bump_review_revision(it)
        created.append(question.short_code)
    add_span(session, trace, "approve", 25, detail={"created": len(created), "merged": sum(1 for i in items if i.is_duplicate_of)})

    # 仅当该会话不再剩 DRAFT 项时才结束审核(单题通过保持 REVIEW, 其余题仍可继续审)
    await session.flush()
    remaining = (
        await session.execute(
            select(func.count(ImportItem.id)).where(
                ImportItem.session_id == imp.id, ImportItem.status == "DRAFT"
            )
        )
    ).scalar_one()
    if remaining == 0:
        imp.status = "APPROVED"
    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000), 0 if require_review_content else 0.02)
    await emit_event(session, ws.id, "import.approved", {"import_id": imp.id, "created": created}, "import", imp.id)
    if commit:
        await session.commit()
    return {"created": created, "import_id": imp.id, "status": imp.status}


async def _find_import_duplicate(session, workspace_id, title, suggested_id):
    from app.api.v1.question_generation import normalized_title

    rows = (await session.execute(select(Question.id, Question.title).where(
        (Question.workspace_id.is_(None) | (Question.workspace_id == workspace_id)),
        Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY'),
    ).order_by(Question.id))).all()
    matches = [question_id for question_id, existing_title in rows if normalized_title(existing_title) == normalized_title(title)]
    return suggested_id if suggested_id in matches else next(iter(matches), None)


# ---------------- 题目编辑 / 合并重复 / 审核驳回 ----------------

class QuestionUpdate(BaseModel):
    title: str | None = None
    topic: str | None = None
    skill_key: str | None = None
    difficulty: int | None = None
    company: str | None = None
    role: str | None = None
    level: str | None = None
    tags: list[str] | None = None


async def _own_question(session: AsyncSession, ws: Workspace, short_code: str) -> Question:
    q = (
        await session.execute(
            select(Question).where(Question.short_code == short_code)
        )
    ).scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail=f"题目 '{short_code}' 不存在")
    if q.workspace_id is None:
        raise HTTPException(403, "共享题库只读；请创建个人版本再编辑")
    if q.workspace_id is not None and q.workspace_id != ws.id:
        raise HTTPException(status_code=403, detail="无权访问其他工作空间的私有题目")
    if (q.generation_meta or {}).get("interview_set_id"):
        raise HTTPException(status_code=409, detail="面经集题目随原帖与项目依据保存，不能从通用题库修改")
    if q.is_study_chat_review:
        raise HTTPException(409, "学习聊天复习题随对话快照保存，请在学习聊天中重新生成")
    return q


@router.patch("/bank/questions/{short_code}")
async def update_question(
    short_code: str,
    body: QuestionUpdate,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    q = await _own_question(session, ws, short_code)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=422, detail="没有需要更新的字段")
    from copy import deepcopy
    from app.services.assessment_versions import digest
    from datetime import datetime, timezone
    before={k:deepcopy(getattr(q,k)) for k in ('title','qtype','topic','skill_id','difficulty','role','level','options','rubric','answer_html')}
    if "skill_key" in data:
        sm = await _skill_map(session)
        key = data.pop("skill_key")
        if key and key not in sm:
            raise HTTPException(status_code=404, detail=f"能力域 '{key}' 不存在")
        q.skill_id = sm[key].id if key else None
    if "difficulty" in data and data["difficulty"] is not None:
        data["difficulty"] = max(1, min(5, int(data["difficulty"])))
    for field, value in data.items():
        setattr(q, field, value)
    changed=[k for k in ('title','topic','skill_id','difficulty','role','level') if before[k]!=getattr(q,k)]
    if changed:
        previous={**before,'version':digest(before),'superseded_at':datetime.now(timezone.utc).isoformat(),'changed_fields':changed}
        meta=q.generation_meta or {}
        q.generation_meta={**meta,'question_versions':[*meta.get('question_versions',[]),previous]}
        q.rubric={**(q.rubric or {}),'review_required':True,'review_reason':'题意、层级或能力映射已变更，需要重新生成或复核评分标准'}
    await session.commit()
    return {"id": q.id, "short_code": q.short_code, "title": q.title, "topic": q.topic,
            "skill_id": q.skill_id, "difficulty": q.difficulty, "company": q.company,
            "role": q.role, "level": q.level, "tags": q.tags,
            "rubric_review_required":bool((q.rubric or {}).get('review_required'))}


class MergeReq(BaseModel):
    target_short_code: str


@router.post("/bank/questions/{short_code}/merge")
async def merge_question(
    short_code: str,
    body: MergeReq,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """把 q 合并进 target: q 成为 target 的变体(canonical_question_id=target.id)。"""
    q = await _own_question(session, ws, short_code)
    if q.canonical_question_id is not None:
        raise HTTPException(status_code=409, detail="该题已经是另一题的变体，不能重复合并")
    target = await _own_question(session, ws, body.target_short_code)
    if target.id == q.id:
        raise HTTPException(status_code=422, detail="不能合并到自己")
    if target.canonical_question_id == q.id:
        raise HTTPException(status_code=422, detail="目标题是本题的变体，方向反了")
    q.canonical_question_id = target.id
    await session.commit()
    return {"merged": q.short_code, "into": target.short_code, "canonical_question_id": target.id}


@router.post("/bank/imports/{import_id}/items/{item_id}/reject")
async def reject_import_item(
    import_id: int,
    item_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    imp, item = await review_actions.lock_review_item(session, ws, import_id, item_id)
    if item.status != ImportItem.STATUS_DRAFT:
        raise HTTPException(status_code=409, detail=f"仅待审核(DRAFT)的题可以驳回，当前状态 {item.status}")
    review_actions.ensure_not_revising(item)
    review_actions.bump_review_revision(item)
    remaining = await review_actions.reject_locked_item(session, imp, item)
    await session.commit()
    return {"item_id": item.id, "status": item.status, "session_status": imp.status, "remaining_draft": remaining}
