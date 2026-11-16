"""考试中心 API: 状态机 CREATED→ACTIVE→SUBMITTED→TRANSCRIPT_CONFIRMED→FROZEN→SCORING→COMPLETED。

提交后同步走: 誊写确认→冻结→评分→出报告→写证据→重算能力→生成学习任务(全链路 trace 落盘)。
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json as _json
import re as _re
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import (
    Evidence, Exam, ExamAnswer, ExamQuestion, SkillState, Workspace,
)
from app.services.competency import recompute_skill_states
from app.services.llm import adapter as llm_adapter
from app.services.planner import plan_tasks_for_workspace
from app.services.scorer import build_feedback, score_question
from app.services.selection import select_questions_for_exam
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()


class CreateExamReq(BaseModel):
    kind: str = Exam.KIND_DIAGNOSIS
    question_count: int = 3
    duration_minutes: int = 30


class SaveAnswerReq(BaseModel):
    order_no: int
    choice_index: int | None = None
    text_answer: str | None = None
    transcript: str | None = None


EXAM_KINDS = {
    Exam.KIND_DIAGNOSIS: {"title": "独立诊断", "desc": "自适应选题, 定位当前能力差距", "default_minutes": 30},
    Exam.KIND_FOCUSED: {"title": "专项考试", "desc": "针对单一能力域的深入验证", "default_minutes": 20},
    Exam.KIND_BANK: {"title": "面经考试", "desc": "用面经题库组卷实战演练", "default_minutes": 45},
    Exam.KIND_PROJECT: {"title": "项目面试", "desc": "围绕项目经历的深挖式问答", "default_minutes": 40},
}


@router.get("/exams")
async def list_exams(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    history = (
        await session.execute(
            select(Exam).where(Exam.workspace_id == ws.id).order_by(Exam.id.desc()).limit(20)
        )
    ).scalars().all()
    counts = {
        kind: sum(1 for e in history if e.kind == kind)
        for kind in EXAM_KINDS
    }
    return {
        "kinds": [
            {
                "kind": kind,
                "title": meta["title"],
                "desc": meta["desc"],
                "default_minutes": meta["default_minutes"],
                "history_count": counts[kind],
            }
            for kind, meta in EXAM_KINDS.items()
        ],
        "history": [
            {
                "id": e.id,
                "title": e.title,
                "kind": e.kind,
                "status": e.status,
                "question_count": len(e.questions),
                "score": e.score,
                "created_at": e.created_at.isoformat(),
                "completed_at": e.completed_at.isoformat() if e.completed_at else None,
            }
            for e in history
        ],
    }


@router.post("/exams")
async def create_exam(
    req: CreateExamReq,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    if req.kind not in EXAM_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown exam kind '{req.kind}'")
    meta = EXAM_KINDS[req.kind]

    exam = Exam(
        workspace_id=ws.id,
        title=meta["title"],
        kind=req.kind,
        status=Exam.STATUS_ACTIVE,
        duration_minutes=req.duration_minutes or meta["default_minutes"],
        started_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(exam)
    await session.flush()

    # 选题 + trace
    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "exam_creation", f"创建考试: {meta['title']}", short_code=f"TR-E{exam.id}")
    questions, reason = await select_questions_for_exam(session, ws.id, exam, req.question_count)
    add_span(session, trace, "select_questions", int((time.perf_counter() - t0) * 1000), detail={"count": len(questions), "reason": reason})
    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000))

    exam.selection_reason = reason
    for i, q in enumerate(questions, start=1):
        session.add(ExamQuestion(exam_id=exam.id, question_id=q.id, order_no=i))
    await emit_event(session, ws.id, "exam.created", {"exam_id": exam.id, "kind": req.kind}, "exam", exam.id)
    await session.commit()

    return await _exam_detail(session, ws, exam.id)


@router.get("/exams/{exam_id}")
async def get_exam(
    exam_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    return await _exam_detail(session, ws, exam_id)


@router.post("/exams/{exam_id}/answers")
async def save_answer(
    exam_id: int,
    req: SaveAnswerReq,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    exam = await _get_own_exam(session, ws, exam_id)
    if exam.status != Exam.STATUS_ACTIVE:
        raise HTTPException(status_code=409, detail=f"exam status is {exam.status}, cannot save answers")
    eq = next((q for q in exam.questions if q.order_no == req.order_no), None)
    if eq is None:
        raise HTTPException(status_code=404, detail="question not found in exam")

    answer = eq.answer
    if answer is None:
        answer = ExamAnswer(exam_question_id=eq.id, max_points=(eq.question.rubric or {}).get("max_points", 5.0))
        session.add(answer)
        await session.flush()
        eq.answer = answer
    answer.choice_index = req.choice_index
    answer.text_answer = req.text_answer
    answer.transcript = req.transcript
    await session.commit()
    return {"ok": True, "saved_at": dt.datetime.now(dt.timezone.utc).isoformat()}


@router.post("/exams/{exam_id}/submit")
async def submit_exam(
    exam_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """提交 → 誊写确认 → 冻结 → 评分 → 报告 → 证据 → 重算能力 → 学习任务。"""
    exam = await _get_own_exam(session, ws, exam_id)
    if exam.status not in (Exam.STATUS_ACTIVE, Exam.STATUS_SUBMITTED):
        raise HTTPException(status_code=409, detail=f"exam status is {exam.status}, cannot submit")

    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "diagnosis_scoring", f"评分: {exam.title}", short_code=f"TR-S{exam.id}")

    # 1) SUBMITTED: 答卷只读
    exam.status = Exam.STATUS_SUBMITTED
    exam.submitted_at = dt.datetime.now(dt.timezone.utc)
    add_span(session, trace, "submit", 5, detail={"status": "SUBMITTED"})

    # 2) TRANSCRIPT_CONFIRMED
    exam.status = Exam.STATUS_TRANSCRIPT_CONFIRMED
    add_span(session, trace, "transcript_confirm", 8, detail={"status": "TRANSCRIPT_CONFIRMED"})

    # 3) FROZEN: 题目+rubric+参考答案快照
    exam.status = Exam.STATUS_FROZEN
    for eq in exam.questions:
        if eq.frozen_snapshot is None:
            q = eq.question
            eq.frozen_snapshot = {
                "title": q.title, "qtype": q.qtype, "options": q.options,
                "rubric": q.rubric, "skill_key": q.skill.key if q.skill else None,
                "answer_html": q.answer_html,
            }
    add_span(session, trace, "freeze", 6, detail={"status": "FROZEN", "snapshots": len(exam.questions)})

    # 4) SCORING
    exam.status = Exam.STATUS_SCORING
    add_span(session, trace, "scoring", 10, detail={"status": "SCORING"})

    gained = 0.0
    total = 0.0
    for eq in exam.questions:
        q = eq.question
        answer = eq.answer
        total += (q.rubric or {}).get("max_points", 5.0)
        if answer is None:
            answer = ExamAnswer(exam_question_id=eq.id, max_points=(q.rubric or {}).get("max_points", 5.0))
            session.add(answer)
            await session.flush()
            eq.answer = answer
        result = score_question(q, choice_index=answer.choice_index, text=answer.text_answer)
        answer.points = result["points"]
        answer.verdict = result["verdict"]
        answer.feedback = build_feedback(q, result)
        answer.evidence_refs = []
        gained += result["points"]

    exam.total_points = total
    exam.gained_points = round(gained, 1)
    exam.score = round(100 * gained / total, 1) if total else 0.0

    # 5) COMPLETED + 报告小结
    exam.status = Exam.STATUS_COMPLETED
    exam.completed_at = dt.datetime.now(dt.timezone.utc)
    exam.summary = {
        "verdicts": [
            {"order_no": eq.order_no, "title": eq.question.title[:40], "verdict": eq.answer.verdict if eq.answer else "WRONG"}
            for eq in exam.questions
        ],
        "score": exam.score,
    }

    # 6) 写证据(每题一条)
    ev_counter = 1
    for eq in exam.questions:
        q = eq.question
        answer = eq.answer
        verdict = answer.verdict if answer else "WRONG"
        support = {"CORRECT": 0.6, "PARTIAL": 0.15, "WRONG": -0.4}.get(verdict, 0.0)
        grade = "VERIFIED" if verdict == "CORRECT" else ("MEDIUM" if verdict == "PARTIAL" else "LOW")
        ev = Evidence(
            short_code=f"EV-X{exam.id}-{eq.order_no}",
            workspace_id=ws.id,
            skill_id=q.skill_id,
            kind="EXAM_ANSWER",
            title=f"{exam.title} Q{eq.order_no}: {q.title[:60]}",
            detail=f"判定 {verdict}, 得分 {answer.points if answer else 0}",
            grade=grade,
            support=support,
            source_ref=f"exam:{exam.id}#q{eq.order_no}",
            occurred_at=exam.completed_at,
        )
        session.add(ev)
        if answer is not None:
            answer.evidence_refs = [ev.short_code]
        ev_counter += 1
    add_span(session, trace, "write_evidence", 12, detail={"evidence": len(exam.questions)})

    # 7) 重算能力 + 生成学习任务
    states = await recompute_skill_states(session, ws.id)
    tasks = await plan_tasks_for_workspace(session, ws.id)
    add_span(session, trace, "recompute_and_plan", 15, detail={"tasks": len(tasks)})

    cost = round(0.02 * len(exam.questions), 3)
    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000), cost)
    await emit_event(session, ws.id, "exam.completed", {"exam_id": exam.id, "score": exam.score}, "exam", exam.id)
    await session.commit()

    return await _exam_report(session, ws, exam.id)


@router.get("/exams/{exam_id}/report")
async def exam_report(
    exam_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    return await _exam_report(session, ws, exam_id)


async def _get_own_exam(session: AsyncSession, ws: Workspace, exam_id: int) -> Exam:
    exam = (
        await session.execute(
            select(Exam).where(Exam.id == exam_id, Exam.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="exam not found")
    return exam


async def _exam_detail(session: AsyncSession, ws: Workspace, exam_id: int) -> dict:
    exam = await _get_own_exam(session, ws, exam_id)
    return {
        "id": exam.id,
        "title": exam.title,
        "kind": exam.kind,
        "status": exam.status,
        "duration_minutes": exam.duration_minutes,
        "selection_reason": exam.selection_reason,
        "started_at": exam.started_at.isoformat() if exam.started_at else None,
        "questions": [
            {
                "order_no": eq.order_no,
                "short_code": eq.question.short_code,
                "title": eq.question.title,
                "qtype": eq.question.qtype,
                "options": eq.question.options,
                "difficulty": eq.question.difficulty,
                "skill": eq.question.skill.name if eq.question.skill else None,
                "rubric_visible": False,  # 答题中不暴露 rubric
                "answer": (
                    {
                        "choice_index": eq.answer.choice_index,
                        "text_answer": eq.answer.text_answer,
                        "transcript": eq.answer.transcript,
                    }
                    if eq.answer
                    else None
                ),
            }
            for eq in exam.questions
        ],
    }


def _strip_html(text: str | None, limit: int = 800) -> str:
    if not text:
        return ""
    plain = _html.unescape(_re.sub(r"<[^>]+>", " ", text))
    plain = _re.sub(r"\s+", " ", plain).strip()
    return plain[:limit] + ("…" if len(plain) > limit else "")


def _skill_breakdown(eqs: list[ExamQuestion]) -> list[dict]:
    """按能力域聚合本次考试各题得分, 薄弱的排前面。"""
    groups: dict[str, dict] = {}
    for eq in eqs:
        q = eq.question
        key = q.skill.key if q.skill else "general"
        name = q.skill.name if q.skill else "综合"
        a = eq.answer
        pts = a.points if a is not None and a.points is not None else 0.0
        mp = (q.rubric or {}).get("max_points", 5.0)
        g = groups.setdefault(key, {
            "skill_key": key, "skill_name": name, "q_count": 0,
            "earned": 0.0, "max": 0.0,
            "verdicts": {"CORRECT": 0, "PARTIAL": 0, "WRONG": 0},
            "order_nos": [],
        })
        g["q_count"] += 1
        g["earned"] += pts
        g["max"] += mp
        if a is not None and a.verdict in g["verdicts"]:
            g["verdicts"][a.verdict] += 1
        g["order_nos"].append(eq.order_no)
    out = []
    for g in groups.values():
        g["earned"] = round(g["earned"], 1)
        g["max"] = round(g["max"], 1)
        g["ratio"] = round(g["earned"] / g["max"], 2) if g["max"] else 0.0
        out.append(g)
    out.sort(key=lambda x: x["ratio"])
    return out


def _overall_diagnosis(breakdown: list[dict], exam: Exam, states_map: dict[str, SkillState]) -> dict:
    """确定性诊断结论(无 LLM 也成立): 覆盖域/强项/薄弱/优先行动。"""
    strong = [g for g in breakdown if g["ratio"] >= 0.8]
    mid = [g for g in breakdown if 0.6 <= g["ratio"] < 0.8]
    weak = [g for g in breakdown if g["ratio"] < 0.6]
    weakest = weak[0] if weak else (mid[0] if mid else (breakdown[0] if breakdown else None))

    parts: list[str] = []
    if breakdown:
        parts.append(f"本次诊断覆盖了 {len(breakdown)} 个能力域：{'、'.join(g['skill_name'] for g in breakdown)}。")
    if strong:
        parts.append(f"{'、'.join(g['skill_name'] for g in strong)} 表现扎实，可以保持。")
    if mid:
        parts.append(f"{'、'.join(g['skill_name'] for g in mid)} 达到基本线但还不稳，建议查漏。")
    if weak:
        parts.append(f"{'、'.join(g['skill_name'] for g in weak)} 明显薄弱，是当前和目标之间最主要的差距。")
    else:
        parts.append("本次没有明显薄弱项，可以进入下一个能力域的诊断。")

    priority: dict | None = None
    if weakest is not None:
        st = states_map.get(weakest["skill_key"])
        cur = st.estimated_level if st else None
        status = st.status if st else "UNKNOWN"
        priority = {
            "skill_key": weakest["skill_key"],
            "skill_name": weakest["skill_name"],
            "ratio": weakest["ratio"],
            "current_level": cur,
            "status": status,
            "advice": (
                f"优先补「{weakest['skill_name']}」：本次得分率 {int(weakest['ratio'] * 100)}%，"
                + (f"当前估计 {cur:.1f}/5（{status}）。" if cur is not None else "尚无能力估计，先通过学习与实践积累证据。")
                + " 今日学习任务已按这个差距重新生成。"
            ),
        }

    return {
        "conclusion": "".join(parts),
        "strong": [g["skill_name"] for g in strong],
        "mid": [g["skill_name"] for g in mid],
        "weak": [g["skill_name"] for g in weak],
        "priority": priority,
    }


async def _states_map(session: AsyncSession, ws: Workspace, breakdown: list[dict]) -> dict[str, SkillState]:
    keys = [g["skill_key"] for g in breakdown]
    if not keys:
        return {}
    rows = (
        await session.execute(
            select(SkillState).where(SkillState.workspace_id == ws.id)
        )
    ).scalars().all()
    return {r.skill.key: r for r in rows if r.skill and r.skill.key in keys}


DEEP_DIAG_SYSTEM = """你是资深的 AI 工程师面试官兼职业教练。基于一场考试的全部题目、考生作答、评分结果与能力域统计，输出一份详细的中文诊断报告。
要求：
1. conclusion：2-4 句通俗、直接的总体诊断——先说整体表现，再点出最重要的差距，最后给一句方向性建议。不要空话。
2. strengths/weaknesses：按能力域归纳，每条给出具体理由（引用题目行为/作答内容），不笼统。weaknesses 里给出 gap（离能稳定回答还差什么）。
3. actions：3-5 条可执行的行动建议（学什么、练什么、怎么验证），按优先级排序，具体到可操作。
4. per_question：必须为每一道题各生成一个条目（键=题号数字字符串，如 "1"、"2"），不得省略任何一题，不得出现占位键/注释键。每条给出 why（为什么得这个分——结合作答内容说清楚差在哪）与 how（回答或改进的正确姿势，教学式展开，120-200 字）。
5. 只依据给出的材料，不编造未给出的信息。
输出 JSON，结构：
{"conclusion": str, "strengths": [{"skill": str, "why": str}], "weaknesses": [{"skill": str, "why": str, "gap": str}], "actions": [{"title": str, "detail": str}], "per_question": {"<order_no>": {"why": str, "how": str}}}"""

DEEP_DIAG_FIX = """你是资深的 AI 工程师面试官。基于材料为指定题目逐题输出剖析。
只输出一个 JSON 对象，结构：
{"per_question": {"<order_no>": {"why": str, "how": str}}}
要求：键必须是题号数字字符串（如 "2"）；每道指定题目都必须有条目，不得省略；why=为什么得这个分（结合作答内容，120-200 字），how=正确姿势/如何改进（教学式，120-200 字）；禁止占位键、注释键或任何额外键。"""


def _norm_pq_key(k) -> str | None:
    s = str(k).strip().upper()
    if s.startswith("Q"):
        s = s[1:].strip()
    return s if s.isdigit() else None


def _sanitize_pq(pq, expect: set) -> dict:
    """只保留「题号 -> {why, how}」的合法条目, 丢弃 LLM 幻觉出的占位键(如 ':{')。"""
    out = {}
    for k, v in (pq or {}).items():
        nk = _norm_pq_key(k)
        if nk and nk in expect and isinstance(v, dict) and v.get("why") and v.get("how"):
            out[nk] = {"why": str(v["why"]), "how": str(v["how"])}
    return out


@router.post("/exams/{exam_id}/report/deep")
async def exam_report_deep(
    exam_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """生成(并缓存) LLM 深度诊断。失败时诚实报错, 不造假数据。"""
    exam = await _get_own_exam(session, ws, exam_id)
    if exam.status != Exam.STATUS_COMPLETED:
        raise HTTPException(status_code=409, detail="exam not completed yet")

    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "diagnosis_deep", f"深度诊断: {exam.title}", trigger="manual", trigger_ref=f"exam:{exam.id}")

    breakdown = _skill_breakdown(exam.questions)
    states_map = await _states_map(session, ws, breakdown)
    deterministic = _overall_diagnosis(breakdown, exam, states_map)

    if not llm_adapter.active:
        await finish_trace(session, trace, "FAILED", int((time.perf_counter() - t0) * 1000), fail_reason="llm not configured")
        await session.commit()
        raise HTTPException(status_code=409, detail="LLM 未配置：请在「设置与工作空间 → 模型」里配置模型后重试（确定性结论已在报告中给出）")

    lines = [f"考试：{exam.title}（{EXAM_KINDS.get(exam.kind, {}).get('title', exam.kind)}）", f"总分：{exam.score}/100（{exam.gained_points}/{exam.total_points} 分）", ""]
    lines.append("能力域统计（得分率升序）：")
    for g in breakdown:
        st = states_map.get(g["skill_key"])
        cur = f"当前估计 {st.estimated_level:.1f}/5 · {st.status}" if st and st.estimated_level is not None else "当前 UNKNOWN"
        v = g["verdicts"]
        lines.append(f"- {g['skill_name']}：{g['earned']}/{g['max']}（{int(g['ratio'] * 100)}%），题号 {g['order_nos']}，判定 ✓{v['CORRECT']} △{v['PARTIAL']} ✗{v['WRONG']}，{cur}")
    lines.append("")
    q_material: dict[int, str] = {}
    for eq in exam.questions:
        q = eq.question
        a = eq.answer
        fb = (a.feedback if a else None) or {}
        hits = "、".join((p.get("title", p) if isinstance(p, dict) else str(p)) for p in (fb.get("hit_points") or [])) or "无"
        misses = "、".join((p.get("title", p) if isinstance(p, dict) else str(p)) for p in (fb.get("missed_points") or [])) or "无"
        yours = a.text_answer or (f"选择题，选了 {a.choice_index}" if a and a.choice_index is not None else "未作答")
        block = "\n".join([
            f"【Q{eq.order_no}】（{q.skill.name if q.skill else '综合'} · {q.qtype} · {a.points if a else 0}/{(q.rubric or {}).get('max_points', 5)} 分 · {a.verdict if a else 'WRONG'}）",
            f"题目：{q.title}",
            f"考生作答：{_strip_html(yours, 500)}",
            f"命中要点：{hits}；缺失要点：{misses}",
            f"参考答案（摘要）：{_strip_html((eq.frozen_snapshot or {}).get('answer_html') or q.answer_html, 800)}",
        ])
        q_material[eq.order_no] = block
        lines.append(block)
        lines.append("")
    lines.append(f"确定性结论（供参考）：{deterministic['conclusion']}")

    resp = await llm_adapter.chat(DEEP_DIAG_SYSTEM, "\n".join(lines), json_mode=True, timeout=120)
    if resp is None:
        await finish_trace(session, trace, "FAILED", int((time.perf_counter() - t0) * 1000), fail_reason="llm call failed")
        await session.commit()
        raise HTTPException(status_code=502, detail="LLM 调用失败，请稍后重试（确定性结论已在报告中给出，不受影响）")

    try:
        deep_raw = _json.loads(resp["content"])
    except Exception:
        await finish_trace(session, trace, "FAILED", int((time.perf_counter() - t0) * 1000), fail_reason="llm bad json")
        await session.commit()
        raise HTTPException(status_code=502, detail="LLM 返回格式异常，请重试一次")

    # 确定性校验: 只收合法条目; 缺题时用最小化材料补齐一次(修复 deepseek 偶发漏题/占位键问题)。
    expect = {str(eq.order_no) for eq in exam.questions}
    per_q = _sanitize_pq(deep_raw.get("per_question"), expect)
    retried = False
    missing = sorted(expect - set(per_q.keys()), key=int)
    if missing:
        fix = [f"第一次输出缺少题目 {('、'.join(missing))} 的 per_question 条目（或出现了占位键）。请严格按要求为每道题补齐。", ""]
        for no in missing:
            eq = next((e for e in exam.questions if str(e.order_no) == no), None)
            if eq is not None and eq.order_no in q_material:
                fix.append(q_material[eq.order_no])
                fix.append("")
        resp2 = await llm_adapter.chat(DEEP_DIAG_FIX, "\n".join(fix), json_mode=True, timeout=120)
        if resp2:
            try:
                per_q.update(_sanitize_pq(_json.loads(resp2["content"]).get("per_question"), expect))
                retried = True
            except Exception:
                pass

    deep = {
        "conclusion": str(deep_raw.get("conclusion", "")),
        "strengths": [s for s in (deep_raw.get("strengths") or []) if isinstance(s, dict)],
        "weaknesses": [s for s in (deep_raw.get("weaknesses") or []) if isinstance(s, dict)],
        "actions": [s for s in (deep_raw.get("actions") or []) if isinstance(s, dict)],
        "per_question": per_q,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": (await llm_adapter._effective()).get("model"),
        "retried": retried,
    }
    exam.summary = {**(exam.summary or {}), "diagnosis_deep": deep}
    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000), 0.02)
    await emit_event(session, ws.id, "exam.deep_diagnosis", {"exam_id": exam.id}, "exam", exam.id)
    await session.commit()
    return deep


async def _exam_report(session: AsyncSession, ws: Workspace, exam_id: int) -> dict:
    exam = await _get_own_exam(session, ws, exam_id)
    if exam.status != Exam.STATUS_COMPLETED:
        raise HTTPException(status_code=409, detail="exam not completed yet")
    breakdown = _skill_breakdown(exam.questions)
    states_map = await _states_map(session, ws, breakdown)
    summary = exam.summary or {}
    deep = summary.get("diagnosis_deep")
    return {
        "id": exam.id,
        "title": exam.title,
        "kind": exam.kind,
        "status": exam.status,
        "score": exam.score,
        "total_points": exam.total_points,
        "gained_points": exam.gained_points,
        "selection_reason": exam.selection_reason,
        "summary": summary,
        "skill_breakdown": breakdown,
        "overall": _overall_diagnosis(breakdown, exam, states_map),
        "diagnosis_deep": deep,
        "has_deep": bool(deep),
        "completed_at": exam.completed_at.isoformat() if exam.completed_at else None,
        "questions": [
            {
                "order_no": eq.order_no,
                "short_code": eq.question.short_code,
                "title": eq.question.title,
                "qtype": eq.question.qtype,
                "options": eq.question.options,
                "skill": eq.question.skill.name if eq.question.skill else None,
                "reference_answer": (eq.frozen_snapshot or {}).get("answer_html") or eq.question.answer_html,
                "frozen": eq.frozen_snapshot,
                "answer": (
                    {
                        "choice_index": eq.answer.choice_index,
                        "text_answer": eq.answer.text_answer,
                        "points": eq.answer.points,
                        "max_points": eq.answer.max_points,
                        "verdict": eq.answer.verdict,
                        "feedback": eq.answer.feedback,
                        "evidence_refs": eq.answer.evidence_refs,
                    }
                    if eq.answer
                    else None
                ),
            }
            for eq in exam.questions
        ],
    }
