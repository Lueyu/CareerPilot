"""Plan from observed answer gaps, never from absence of domain evidence."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Evidence, LearningTask


def _criterion_key(value):
    title = value.get("title", "") if isinstance(value, dict) else str(value)
    return re.sub(r"^(?:要点|评分项)\s*[:：]\s*", "", title.strip()).casefold()


async def plan_tasks_for_workspace(session: AsyncSession, workspace_id: int, limit: int = 3):
    """Only return actual work. UNKNOWN is a reason to diagnose, not to invent a gap."""
    return (await session.execute(select(LearningTask).where(
        LearningTask.workspace_id == workspace_id,
        LearningTask.kind != "RETEST", LearningTask.status.in_(("PENDING", "IN_PROGRESS")),
    ).order_by(LearningTask.id).limit(limit))).scalars().all()


async def plan_tasks_for_exam(session: AsyncSession, workspace_id: int, exam, limit: int = 3):
    """Persist each task's question, rubric criterion and real Evidence foreign keys."""
    existing = (await session.execute(select(LearningTask).where(
        LearningTask.workspace_id == workspace_id, LearningTask.source_exam_id == exam.id,
        LearningTask.kind != "RETEST",
    ).order_by(LearningTask.id))).scalars().all()
    if existing:
        return existing
    tasks = []
    for eq in sorted(exam.questions, key=lambda item: (item.answer.points or 0) / max(item.answer.max_points or 5, .1) if item.answer else 0):
        answer = eq.answer
        if answer is None or answer.points is None:
            continue
        evidence = (await session.execute(select(Evidence).where(
            Evidence.workspace_id == workspace_id, Evidence.short_code == f"EV-X{exam.id}-{eq.order_no}",
        ))).scalar_one_or_none()
        if evidence is None:
            continue
        snapshot = eq.frozen_snapshot or {}
        rubric = snapshot.get("rubric") or eq.question.rubric or {}
        rubric_criteria=rubric.get('criteria') or rubric.get('points') or []
        by_title = {_criterion_key(p): p for p in rubric_criteria}
        feedback = answer.feedback or {}
        matches = [by_title[_criterion_key(p)] for p in feedback.get("missed_points") or [] if _criterion_key(p) in by_title]
        if not matches and answer.verdict != "CORRECT" and (snapshot.get("qtype") or eq.question.qtype) == "mcq":
            matches = [{"title": "本题选项判断", "detail": "说明选项依据，并用不同情境重新验证。"}]
        followups = feedback.get("followups") or []
        gap_type = (followups[0].get("kind") if followups and isinstance(followups[0], dict) else None) or "ObservedCriterionGap"
        for criterion in matches:
            if len(tasks) >= limit:
                break
            title = (criterion.get("title") if isinstance(criterion, dict) else str(criterion))[:160]
            if any(t.source_exam_question_id == eq.id and t.source_criterion == title for t in tasks):
                continue
            detail = (criterion.get("detail") or '；'.join(criterion.get('required_evidence') or [])) if isinstance(criterion, dict) else ""
            frozen_answer = snapshot.get("submitted_answer")
            if isinstance(frozen_answer, dict):
                excerpt = frozen_answer.get("text_answer") or frozen_answer.get("transcript") or ""
            else:
                excerpt = (evidence.meta or {}).get("answer_excerpt") or answer.text_answer or ""
            observation = {
                "exam_id": exam.id, "exam_question_id": eq.id, "question_id": eq.question_id,
                "evidence_id": evidence.id, "evidence_ref": evidence.short_code,
                "question_title": snapshot.get("title") or eq.question.title,
                "criterion": title, "criterion_detail": detail,
                "criterion_id":criterion.get('criterion_id') if isinstance(criterion,dict) else None,
                "rubric_version":rubric.get('rubric_version','legacy-unversioned'),
                "answer_revision_id":snapshot.get('answer_revision_id'),
                "criterion_result":next((r for r in feedback.get('criterion_scores',[]) if r['criterion_id']==criterion.get('criterion_id')),None) if isinstance(criterion,dict) else None,
                "answer_excerpt": excerpt[:2000],
                "reasoning": feedback.get("judge_reasoning") or feedback.get("advice") or "本题评分项尚未满足。",
                "points": answer.points, "max_points": answer.max_points,
                "judge_version": answer.judge_version,
                "scope_note": "这是本题的表现观察，不代表整个能力域等级。",
            }
            task = LearningTask(
                workspace_id=workspace_id, skill_id=evidence.skill_id,
                title=f"补强：{title}", kind="UNDERSTAND", status="PENDING",
                source_exam_id=exam.id, source_exam_question_id=eq.id, source_evidence_id=evidence.id,
                source_criterion=title, gap_type=gap_type, why_ref=evidence.short_code,
                why=f"考试 {exam.id} 第 {eq.order_no} 题未满足「{title}」。依据：{observation['reasoning']}",
                acceptance=f"用自己的话解释「{title}」，给一个可验证的实例；再独立回答一道新题。{detail}",
                est_minutes=25, content={"observation": observation, "steps": [
                    f"对照自己的原回答，定位「{title}」的缺失。", f"解释并练习：{detail or title}。",
                    "保存笔记或实践产物；学习完成后独立回答新题。",
                ]},
            )
            session.add(task)
            tasks.append(task)
        if len(tasks) >= limit:
            break
    await session.flush()
    # The goal links to the same scored observation; it creates no new grade or
    # mastery evidence. Keep existing task IDs and user progress intact.
    from fastapi import HTTPException

    from app.models import Workspace
    from app.services.learning_feedback import ensure_goal
    workspace = await session.get(Workspace, workspace_id)
    for task in tasks:
        try:
            await ensure_goal(session, workspace, task)
        except HTTPException as error:
            if error.status_code != 409: raise
            # Older observations can remain readable without manufacturing a
            # versioned goal. Preparation explicitly requires source review.
            task.content = {**(task.content or {}), 'goal_binding_status':'NEEDS_REVIEW',
                            'goal_binding_reason':str(error.detail)}
    return tasks
