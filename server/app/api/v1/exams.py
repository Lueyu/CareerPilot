"""考试中心 API: 状态机 CREATED→ACTIVE→SUBMITTED→TRANSCRIPT_CONFIRMED→FROZEN→SCORING→COMPLETED。

提交后同步走: 誊写确认→冻结→评分→出报告→写证据→重算能力→生成学习任务(全链路 trace 落盘)。
"""
from __future__ import annotations

import datetime as dt
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import (
    Evidence, Exam, ExamAnswer, ExamQuestion, Workspace,
)
from app.services.competency import recompute_skill_states
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

    # 3) FROZEN: 题目+rubric 快照
    exam.status = Exam.STATUS_FROZEN
    for eq in exam.questions:
        if eq.frozen_snapshot is None:
            q = eq.question
            eq.frozen_snapshot = {
                "title": q.title, "qtype": q.qtype, "options": q.options,
                "rubric": q.rubric, "skill_key": q.skill.key if q.skill else None,
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


async def _exam_report(session: AsyncSession, ws: Workspace, exam_id: int) -> dict:
    exam = await _get_own_exam(session, ws, exam_id)
    if exam.status != Exam.STATUS_COMPLETED:
        raise HTTPException(status_code=409, detail="exam not completed yet")
    return {
        "id": exam.id,
        "title": exam.title,
        "status": exam.status,
        "score": exam.score,
        "total_points": exam.total_points,
        "gained_points": exam.gained_points,
        "selection_reason": exam.selection_reason,
        "summary": exam.summary,
        "completed_at": exam.completed_at.isoformat() if exam.completed_at else None,
        "questions": [
            {
                "order_no": eq.order_no,
                "short_code": eq.question.short_code,
                "title": eq.question.title,
                "qtype": eq.question.qtype,
                "options": eq.question.options,
                "skill": eq.question.skill.name if eq.question.skill else None,
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
