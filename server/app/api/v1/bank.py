"""面经库 API: 题库浏览 + 导入流水线(解析→审核→入题库)。"""
from __future__ import annotations

import datetime as dt
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import ImportItem, ImportSession, Question, Skill, Workspace
from app.services.llm import AgentAdapter
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()

# 旧题库 topic → 新能力域 key 映射(与 seed 一致)
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
            select(Question).where(
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
    """上传 → 解析(规则/LLM) → 提取题目 → 技能映射 → 查重 → 生成草稿 → 待审核。"""
    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "bank_import", "导入面经: 上传与解析", short_code=f"TR-I{int(t0)}")

    source_text = (req.pasted_text or "").strip() or "\n".join(req.file_names or [])
    if not source_text:
        raise HTTPException(status_code=400, detail="empty import: pasted_text or file_names required")

    n_sessions = (await session.execute(select(func.count(ImportSession.id)))).scalar() or 0
    imp = ImportSession(
        short_code=f"IM-{100 + n_sessions + 1}",
        workspace_id=ws.id,
        filename=(req.file_names or [None])[0],
        paste_text=(req.pasted_text or "")[:10000] or None,
        status="PARSING",
        meta={"source": "pasted" if req.pasted_text else "files", "chars": len(source_text)},
    )
    session.add(imp)
    await session.flush()
    add_span(session, trace, "parse", 40, detail={"chars": len(source_text)})

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


async def _extract_questions(text: str) -> list[dict]:
    """LLM 可用走 LLM, 否则规则: 疑问句/编号行视为题目。"""
    adapter = AgentAdapter()
    if adapter.active:
        sys_prompt = (
            "你是面经解析引擎。从用户粘贴的面试记录中提取面试题。"
            '返回 JSON 数组, 每项 {"title": str, "skill_key": one of agent/tools/memory/rag/eval/backend/reliability/deploy, '
            '"confidence": 0-1, "draft_answer": str}。不要解释。'
        )
        try:
            out = await adapter.chat(sys_prompt, text[:8000], json_mode=True)
            if out and isinstance(out, list):
                return [x for x in out if isinstance(x, dict) and x.get("title")]
        except Exception:
            pass

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


def _guess_skill(text: str) -> str:
    mapping = {
        "agent": ["agent", "智能体", "规划", "multi", "react", "harness"],
        "tools": ["tool", "工具", "mcp", "function call", "调用"],
        "memory": ["memory", "记忆", "状态", "state"],
        "rag": ["rag", "检索", "向量", "embedding", "上下文", "context", "prompt"],
        "eval": ["eval", "评估", "评测", "测试", "benchmark", "指标"],
        "backend": ["api", "后端", "并发", "数据库", "算法", "复杂度", "系统设计"],
        "reliability": ["安全", "失败", "重试", "可靠", "异常", "幻觉", "注入"],
        "deploy": ["部署", "k8s", "监控", "observability", "日志"],
    }
    low = text.lower()
    for key, kws in mapping.items():
        if any(k in low for k in kws):
            return key
    return "agent"


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
        "items": [
            {
                "id": it.id,
                "title": it.title,
                "extract_confidence": it.extract_confidence,
                "suggested_skill_key": it.suggested_skill_key,
                "draft_answer": it.draft_answer,
                "draft_rubric": it.draft_rubric,
                "is_duplicate_of": it.is_duplicate_of,
                "status": it.status,
            }
            for it in items
        ],
    }


@router.post("/bank/imports/{import_id}/approve")
async def approve_import(
    import_id: int,
    req: ApproveItemReq,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """审核通过 → 生成 Question(私有) + 可选证据 → 入题库。重复项标记 MERGED 保留。"""
    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "bank_import", "导入面经: 审核入库", short_code=f"TR-A{import_id}")
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
    for it in items:
        edits = (req.edits or {}).get(it.id, {})
        title = edits.get("title", it.title)
        skill_key = edits.get("skill_key", it.suggested_skill_key)
        skill = sm.get(skill_key or "", sm["agent"])
        if it.is_duplicate_of:
            it.status = "MERGED"
            continue
        question = Question(
            short_code=edits.get("short_code", f"Q-U{imp.id}-{it.id}"),
            workspace_id=ws.id,
            title=title,
            qtype=edits.get("qtype", "open"),
            topic=edits.get("topic", "harness"),
            skill_id=skill.id,
            difficulty=edits.get("difficulty", 3),
            options=edits.get("options"),
            answer_html=edits.get("answer_html", it.draft_answer or ""),
            rubric=edits.get("rubric", it.draft_rubric or {"points": [{"title": "要点", "keywords": [title[:10]]}], "max_points": 5.0}),
            company=edits.get("company", "用户导入"),
            role=edits.get("role"),
            level=edits.get("level"),
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
