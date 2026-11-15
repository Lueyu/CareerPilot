"""面经库 API: 题库浏览 + 导入流水线(解析→审核→入题库)。"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.api.deps import get_workspace
from app.db import get_session
from app.models import ImportItem, ImportSession, Question, Skill, Trace, Workspace
from app.services.answer_gen import generate_answer
from app.services.llm import AgentAdapter
from app.services.text_extract import extract_many
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


@router.get("/bank")
async def list_bank(
    status: str = "all",
    company: str | None = None,
    topic: str | None = None,
    skill: str | None = None,
    q: str | None = None,
    limit: int = 60,
    offset: int = 0,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """公共题库(161 迁移题) + 本工作空间私有题。status=pending 时列待审核导入项。"""
    base = select(Question).where(
        (Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id),
        Question.canonical_question_id.is_(None),  # 变体不单独列出
    )
    if company:
        base = base.where(Question.company.ilike(f"%{company}%"))
    if topic:
        base = base.where(Question.topic == topic)
    if skill:
        sm = await _skill_map(session)
        sk = sm.get(skill)
        if sk:
            base = base.where(Question.skill_id == sk.id)
    if q:
        base = base.where(Question.title.ilike(f"%{q}%"))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (
        await session.execute(base.order_by(Question.id).offset(offset).limit(limit))
    ).scalars().all()

    # 公司聚合(左侧过滤栏)
    companies = (
        await session.execute(
            select(Question.company, func.count(Question.id))
            .where((Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id))
            .group_by(Question.company)
            .order_by(func.count(Question.id).desc())
            .limit(10)
        )
    ).all()
    topics = (
        await session.execute(
            select(Question.topic, func.count(Question.id))
            .where((Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id))
            .group_by(Question.topic)
        )
    ).all()

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
                "tags": r.tags or [],
                "occurred_on": r.occurred_on.isoformat() if r.occurred_on else None,
            }
            for r in rows
        ],
        "facets": {
            "companies": [{"name": c, "count": n} for c, n in companies if c],
            "topics": [{"name": t, "count": n} for t, n in topics if t],
        },
    }


@router.get("/bank/{short_code}")
async def bank_detail(
    short_code: str,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    question = (
        await session.execute(
            select(Question)
            .options(joinedload(Question.skill), selectinload(Question.variants))
            .where(
                Question.short_code == short_code,
                (Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id),
            )
        )
    ).scalar_one_or_none()
    if question is None:
        raise HTTPException(status_code=404, detail="question not found")
    return {
        "id": question.id,
        "short_code": question.short_code,
        "title": question.title,
        "qtype": question.qtype,
        "topic": question.topic,
        "skill": question.skill.name if question.skill else None,
        "difficulty": question.difficulty,
        "options": question.options,
        "answer_html": question.answer_html,
        "rubric": question.rubric,
        "company": question.company,
        "role": question.role,
        "level": question.level,
        "occurred_on": question.occurred_on.isoformat() if question.occurred_on else None,
        "source_type": question.source_type,
        "tags": question.tags or [],
        "keywords": question.keywords or [],
        "variants": [v.short_code for v in question.variants],
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
    """粘贴文本导入。"""
    source_text = (req.pasted_text or "").strip() or "\n".join(req.file_names or [])
    if not source_text:
        raise HTTPException(status_code=400, detail="empty import: pasted_text or file_names required")
    return await _run_import(
        session,
        ws,
        source_text,
        filename=(req.file_names or [None])[0],
        meta={"source": "pasted" if req.pasted_text else "files", "chars": len(source_text), "files": []},
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
    """拖拽/选择文件上传(PDF / DOCX / Markdown / TXT) + 可选粘贴文本。

    每个文件独立解析: 成功的进入流水线, 失败的把原因回给前端展示, 不影响其他文件。
    """
    raw_items: list[tuple[str, bytes]] = []
    for f in files:
        if not f or not f.filename:
            continue
        raw_items.append((f.filename, await f.read()))

    extracted = extract_many(raw_items)
    file_meta = [x.as_meta() for x in extracted.files]
    ok_names = [f.name for f in extracted.ok_files]

    text_parts = [t for t in [(pasted_text or "").strip(), extracted.text] if t]
    source_text = "\n\n".join(text_parts).strip()
    if not source_text:
        detail = "没有可解析的内容"
        if file_meta:
            detail = "; ".join(f"{m['name']}: {m['error']}" for m in file_meta if m["error"]) or detail
        raise HTTPException(status_code=400, detail=detail)

    return await _run_import(
        session,
        ws,
        source_text,
        filename=", ".join(ok_names)[:255] or None,
        meta={
            "source": "uploads" if ok_names else "pasted",
            "chars": len(source_text),
            "files": file_meta,
            "company": company or None,
            "level": level or None,
        },
    )


async def _run_import(
    session: AsyncSession,
    ws: Workspace,
    source_text: str,
    *,
    filename: str | None,
    meta: dict,
) -> dict:
    """上传 → 解析(规则/LLM) → 提取题目 → 技能映射 → 查重 → 生成答案草稿 → 待审核。"""
    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "bank_import", "导入面经: 上传与解析", short_code=f"TR-I{int(t0)}")

    n_sessions = (await session.execute(select(func.count(ImportSession.id)))).scalar() or 0
    imp = ImportSession(
        short_code=f"IM-{100 + n_sessions + 1}",
        workspace_id=ws.id,
        filename=filename,
        paste_text=(source_text or "")[:10000] or None,
        status="PARSING",
        meta=meta,
    )
    session.add(imp)
    await session.flush()
    add_span(session, trace, "parse", 40, detail={"chars": len(source_text), "files": len(meta.get("files") or [])})

    # 提取候选题(规则: 按行拆, 疑似题目行), LLM 可用时走结构化提取
    candidates = await _extract_questions(source_text)
    add_span(session, trace, "extract", 60, detail={"candidates": len(candidates)})

    sm = await _skill_map(session)
    all_questions = (
        await session.execute(
            select(Question.id, Question.title).where(
                (Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id)
            )
        )
    ).all()
    title_map = {t.strip().lower(): qid for qid, t in all_questions}

    # 原文没带答案的题, 系统补写一份详细讲解(LLM 优先, 未配置则规则模板)
    need_answer = [c for c in candidates if not (c.get("draft_answer") or "").strip()]
    filled = await _fill_answers(session, need_answer, sm)
    add_span(session, trace, "answer", 90, detail={"generated": filled})

    n_new = n_dup = 0
    for i, c in enumerate(candidates, start=1):
        dup_id = title_map.get(c["title"].strip().lower())
        item = ImportItem(
            session_id=imp.id,
            title=c["title"],
            extract_confidence=c["confidence"],
            suggested_skill_key=c["skill_key"],
            draft_answer=c.get("draft_answer", ""),
            draft_rubric=c.get("rubric"),
            is_duplicate_of=dup_id,
            status="DRAFT",
        )
        session.add(item)
        if dup_id:
            n_dup += 1
        else:
            n_new += 1
    add_span(session, trace, "dedup", 20, detail={"duplicates": n_dup})
    add_span(session, trace, "draft", 30, detail={"drafts": n_new})

    imp.status = "REVIEW"
    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000), 0.08)
    await emit_event(session, ws.id, "import.review_ready", {"import_id": imp.id}, "import", imp.id)
    await session.commit()
    return await _import_review(session, ws, imp.id)


async def _fill_answers(session: AsyncSession, candidates: list[dict], sm: dict[str, Skill]) -> int:
    """为没有答案的题目生成讲解, 直接写入 candidate 的 draft_answer / rubric。"""
    todo = candidates[:20]
    if not todo:
        return 0
    sem = asyncio.Semaphore(4)

    async def work(c: dict) -> None:
        skill_key = c.get("skill_key") or "agent"
        skill = sm.get(skill_key)
        skill_name = skill.name if skill else skill_key
        async with sem:
            html, rubric, source = await generate_answer(c["title"], skill_key, skill_name, "open")
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


def _extract_qa_pairs(text: str) -> list[dict]:
    """识别「问：… 答：…」成对结构, 把已有答案一并带出来。"""
    lines = text.splitlines()
    pairs: list[dict] = []
    cur_q: str | None = None
    buf: list[str] = []
    for ln in lines:
        m = QA_HEAD.match(ln)
        if m:
            if cur_q and len(cur_q) >= 6:
                pairs.append({"title": cur_q[:200], "answer": "\n".join(buf).strip()})
            cur_q, buf = m.group(1).strip(), []
            continue
        if ln.strip().startswith(("答", "A:", "A：", "a:")):
            buf.append(ln.split("：", 1)[-1].split(":", 1)[-1].strip())
        elif cur_q is not None:
            buf.append(ln)
    if cur_q and len(cur_q) >= 6:
        pairs.append({"title": cur_q[:200], "answer": "\n".join(buf).strip()})
    return [p for p in pairs if p["title"]]


async def _extract_questions(text: str) -> list[dict]:
    """LLM 可用走 LLM, 否则规则: 疑问句/编号行视为题目。"""
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
            out = await adapter.chat(sys_prompt, text[:8000], json_mode=True)
            if out and out.get("content"):
                parsed = _parse_llm_questions(out["content"])
                if parsed:
                    return parsed
        except Exception:
            pass

    # 规则: 优先识别「问/答」成对结构(原文带答案时直接保留)
    pairs = _extract_qa_pairs(text)
    if len(pairs) >= 1:
        return [
            {
                "title": p["title"],
                "skill_key": _guess_skill(p["title"]),
                "confidence": 0.7,
                "draft_answer": p["answer"],
            }
            for p in pairs[:20]
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
                }
            )
    return results[:20]


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


@router.get("/bank/imports/pending")
async def pending_imports(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
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
    return {
        "id": it.id,
        "title": it.title,
        "extract_confidence": it.extract_confidence,
        "suggested_skill_key": it.suggested_skill_key,
        "draft_answer": it.draft_answer,
        "draft_rubric": it.draft_rubric,
        "has_answer": bool((it.draft_answer or "").strip()),
        "answer_source": rubric.get("source"),
        "is_duplicate_of": it.is_duplicate_of,
        "status": it.status,
    }


@router.post("/bank/imports/{import_id}/items/{item_id}/answer")
async def regenerate_item_answer(
    import_id: int,
    item_id: int,
    force: bool = True,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """为某条导入草稿(重新)生成详细解答。已有答案且 force=false 时直接返回。"""
    item = (
        await session.execute(
            select(ImportItem)
            .join(ImportSession, ImportSession.id == ImportItem.session_id)
            .where(ImportItem.id == item_id, ImportItem.session_id == import_id, ImportSession.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="import item not found")

    if not force and (item.draft_answer or "").strip():
        return _item_view(item)

    sm = await _skill_map(session)
    skill_key = item.suggested_skill_key or "agent"
    skill = sm.get(skill_key)
    answer_html, rubric, source = await generate_answer(item.title, skill_key, skill.name if skill else skill_key)
    item.draft_answer = answer_html
    item.draft_rubric = {**rubric, "source": source}
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

    if (question.answer_html or "").strip() and not force:
        return {
            "short_code": question.short_code,
            "answer_html": question.answer_html,
            "rubric": question.rubric,
            "generated": False,
        }

    skill_key = question.skill.key if question.skill else _guess_skill(question.title)
    skill_name = question.skill.name if question.skill else skill_key
    answer_html, rubric, source = await generate_answer(question.title, skill_key, skill_name, question.qtype)
    question.answer_html = answer_html
    if not question.rubric or force:
        question.rubric = {**rubric, "source": source}
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
    """审核通过 → 生成 Question(私有) + 可选证据 → 入题库。重复项标记 MERGED 保留。"""
    t0 = time.perf_counter()
    trace = await start_trace(
        session, ws.id, "bank_import", "导入面经: 审核入库",
        short_code=await _unique_trace_code(session, f"TR-A{import_id}"),
    )
    imp = (
        await session.execute(
            select(ImportSession).where(
                ImportSession.id == import_id, ImportSession.workspace_id == ws.id
            )
        )
    ).scalar_one_or_none()
    if imp is None:
        raise HTTPException(status_code=404, detail="import not found")
    if imp.status not in ("REVIEW", "APPROVED"):
        raise HTTPException(status_code=409, detail=f"import status is {imp.status}")

    items = (
        await session.execute(
            select(ImportItem).where(ImportItem.session_id == imp.id, ImportItem.status == "DRAFT")
        )
    ).scalars().all()
    if req.item_ids is not None:
        items = [it for it in items if it.id in req.item_ids]

    sm = await _skill_map(session)
    created = []
    generated = 0
    for it in items:
        edits = (req.edits or {}).get(it.id, {})
        title = edits.get("title", it.title)
        skill_key = edits.get("skill_key", it.suggested_skill_key)
        skill = sm.get(skill_key or "", sm["agent"])
        if it.is_duplicate_of:
            it.status = "MERGED"
            continue
        answer_html = edits.get("answer_html", it.draft_answer or "")
        rubric = edits.get("rubric", it.draft_rubric)
        # 兜底: 审核时仍然没有答案的题, 现场补一份(LLM 优先, 否则规则模板)
        if not str(answer_html or "").strip():
            answer_html, auto_rubric, source = await generate_answer(
                title, skill.key if skill else "agent", skill.name if skill else "agent"
            )
            rubric = rubric or {**auto_rubric, "source": source}
            generated += 1
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
        )
        session.add(question)
        await session.flush()
        it.status = "APPROVED"
        it.question_id = question.id
        created.append(question.short_code)
    add_span(session, trace, "approve", 25, detail={"created": len(created), "merged": sum(1 for i in items if i.is_duplicate_of)})

    imp.status = "APPROVED"
    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000), 0.02)
    await emit_event(session, ws.id, "import.approved", {"import_id": imp.id, "created": created}, "import", imp.id)
    await session.commit()
    return {"created": created, "import_id": imp.id, "status": imp.status}
