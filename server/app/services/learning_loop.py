"""Learning completion schedules checks; only scored new answers yield observations."""
import datetime as dt
from sqlalchemy import select
from app.models import LearningTask
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
    from app.services.planner import _criterion_key
    criterion = _criterion_key(task.source_criterion or "")
    checks, bindings = [], []
    for eq in exam.questions:
        # Another paper may have been created for this same task. Neither its
        # mutable task flags nor a later bank edit establishes this paper's scope.
        rubric = (eq.frozen_snapshot or {}).get('rubric') or {}
        matched = [c for c in (rubric.get('criteria') or rubric.get('points') or [])
                   if criterion and _criterion_key(c) == criterion]
        if not matched:
            continue
        feedback = (eq.answer.feedback if eq.answer else None) or {}
        if rubric.get('schema_version') == 2:
            ids = {c['criterion_id'] for c in matched}
            rows = [r for r in feedback.get('criterion_scores', []) if r.get('criterion_id') in ids]
            version = rubric.get('rubric_version')
            valid = bool(version) and feedback.get('rubric_version') == version \
                and feedback.get('validation_status') == 'VALID' \
                and len(rows) == len(ids) and {r['criterion_id'] for r in rows} == ids
            checks.append(all(r['score'] >= 3 for r in rows) if valid else None)
            bindings.extend({'exam_question_id':getattr(eq, 'id', None),
                'criterion_id':criterion_id, 'rubric_version':version} for criterion_id in sorted(ids))
        else:
            checks.append(criterion in {_criterion_key(p) for p in feedback.get('hit_points', [])}
                and criterion not in {_criterion_key(p) for p in feedback.get('missed_points', [])})
            bindings.append({'exam_question_id':getattr(eq, 'id', None),
                'criterion_title':task.source_criterion, 'rubric_version':'legacy-unversioned'})
    covered = bool(checks)
    criterion_passed = any(checks) if covered and all(c is not None for c in checks) else None
    scope = ('本场冻结题目包含原失分项同名评分项；按本场评分项和版本判断本次作答。' if covered else
             '本场冻结题目未直接覆盖原失分评分项；不能据此声称原评分项已通过。')
    outcome = ("原失分评分项在本次新题中满足要求" if criterion_passed is True else
               "原失分评分项在本次新题中仍未满足要求" if criterion_passed is False else
               "已覆盖原失分评分项，但缺少一致版本的有效评分依据，尚未验证" if covered else
               "完成同域新题评分；原失分评分项尚未直接验证")
    task.content = {**(task.content or {}), "retest_result": {
        "exam_id": exam.id, "score": exam.score, "passed": passed,
        "outcome": outcome, "source_criterion_covered": covered,
        "source_criterion_passed": criterion_passed,
        "assessment_scope": scope, "criterion_bindings": bindings,
        "mode": (task.content or {}).get("verification_mode", "immediate"),
        "completed_at": exam.completed_at.isoformat(),
        "scope_note": "仅记录这次作答的表现，不代表已证明长期保持或整个能力域掌握。",
    }}
    from app.services import learning_memory
    if hasattr(learning_memory, 'on_verification_completed'):
        await learning_memory.on_verification_completed(session, exam, task)
    await emit_event(session, exam.workspace_id, "learning.retest_scored",
                     {"task_id": task.id, "exam_id": exam.id, "passed": passed}, "learning_task", task.id)
