"""Learning completion schedules checks; only scored new answers yield observations."""
import datetime as dt

from sqlalchemy import select

from app.models import LearningTask
from app.services.retest_targets import goal_outcomes
from app.services.task_memory import record_task_completion
from app.services.tracing import emit_event

LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))


def retest_fields(task):
    return dict(workspace_id=task.workspace_id, skill_id=task.skill_id,
                source_exam_id=task.source_exam_id, source_exam_question_id=task.source_exam_question_id,
                source_evidence_id=task.source_evidence_id, source_criterion=task.source_criterion,
                gap_type=task.gap_type, why_ref=task.why_ref, retest_of=task.id,
                kind="RETEST", status="PENDING", est_minutes=15,
                acceptance="独立提交一道不同题目的答案并重新评分；完成不等于通过。")


async def schedule_retests(session, task):
    existing = (await session.execute(select(LearningTask).where(
        LearningTask.retest_of == task.id, LearningTask.retest_round > 0))).scalars().all()
    if existing:
        return existing
    completed = task.completed_at.astimezone(LOCAL_TZ).date()
    results = []
    for round_no, offset in enumerate((1, 3, 7), 1):
        retest = LearningTask(**retest_fields(task), retest_round=round_no,
                             title=f"{offset}天后复测：{task.source_criterion or task.title}",
                             why="学习完成后的延迟验证，需独立作答；尚未验证记忆保持。",
                             due_date=completed + dt.timedelta(days=offset),
                             content={"observation": (task.content or {}).get("observation"),
                                      "verification_mode": "delayed", "scheduled_delay_days": offset})
        session.add(retest)
        results.append(retest)
    await session.flush()
    return results


async def complete_retest_for_exam(session, exam):
    if not exam.retest_task_id or exam.status != "COMPLETED":
        return
    task = (await session.execute(select(LearningTask).where(
        LearningTask.id == exam.retest_task_id, LearningTask.workspace_id == exam.workspace_id))).scalar_one()
    if task.kind != "RETEST" or task.status == "DONE":
        return
    task.status = "DONE"
    task.completed_at = exam.completed_at
    passed = bool(exam.questions) and all(eq.answer and eq.answer.verdict == "CORRECT" for eq in exam.questions)
    outcomes = goal_outcomes(exam, task)
    primary = next(result for result in outcomes if result["primary"])
    covered = primary["covered"]
    status = primary["status"]
    criterion_passed = {"PASSED": True, "FAILED": False}.get(status)
    scope = ("按本场出卷时冻结的审核映射验证原失分项。" if covered
             else "已完成同能力域练习，原失分项缺少本场冻结的审核映射。")
    outcome = {
        "PASSED": "原失分项本次独立作答通过",
        "FAILED": "原失分项本次独立作答未通过",
        "ASSISTED": "已记录辅助作答得分；原失分项的独立验证待完成",
        "UNVERIFIED": "缺少一致版本的有效评分或独立性记录；原失分项待验证",
        "UNMAPPED": "完成同能力域练习；原失分项待验证",
    }[status]
    task.content = {**(task.content or {}), "retest_result": {
        "exam_id": exam.id, "score": exam.score, "passed": passed,
        "outcome": outcome, "source_criterion_covered": covered,
        "source_criterion_passed": criterion_passed,
        "assessment_scope": scope, "goal_outcomes": outcomes,
        "criterion_bindings": [item["binding"] for item in outcomes if item["binding"]],
        "mode": (task.content or {}).get("verification_mode", "immediate"),
        "completed_at": exam.completed_at.isoformat(),
        "scope_note": "仅记录这次作答的表现，不代表已证明长期保持或整个能力域掌握。",
    }}
    await record_task_completion(session, task)
    from app.services import learning_memory
    if hasattr(learning_memory, 'on_verification_completed'):
        await learning_memory.on_verification_completed(session, exam, task)
    await emit_event(session, exam.workspace_id, "learning.retest_scored",
                     {"task_id": task.id, "exam_id": exam.id, "passed": passed}, "learning_task", task.id)
