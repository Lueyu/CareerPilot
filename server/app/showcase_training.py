"""Seed 林舟's private training history inside the caller's transaction."""

from datetime import date, datetime, time, timedelta, timezone
from html import escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Evidence, Exam, ExamAnswer, ExamQuestion, LearningTask, Question, Skill, User, Workspace
from app.models.question import SOURCE_PRIVATE
from app.services.assessment_jobs import apply_result
from app.services.assessment_rubric import compile_rubric
from app.services.assessment_versions import ensure_revision, question_snapshot
from app.services.assessment_worker import rule_result
from app.services.competency import recompute_skill_states
from app.services.learning_exposure import independence_snapshot
from app.services.learning_loop import schedule_retests
from app.services.retest_targets import goal_outcomes
from app.showcase_questions import BASELINE, DIAGNOSTIC, QUESTIONS, RETEST


LOCAL_TIME = timezone(timedelta(hours=8))
REVIEW_VERSION = "manual-review-v1"


async def seed_training(session: AsyncSession, workspace: Workspace, user: User, today: date) -> None:
    """Populate a new, already-persisted personal workspace."""
    domains = {prompt.domain for prompt in QUESTIONS}
    skills = {skill.key: skill for skill in await session.scalars(select(Skill).where(Skill.key.in_(domains)))}
    if missing := domains - skills.keys():
        raise RuntimeError(f"Missing shared training skills: {', '.join(sorted(missing))}")

    questions = []
    for number, prompt in enumerate(QUESTIONS, 1):
        skill = skills[prompt.domain]
        question = Question(
            short_code=f"LZ-{workspace.id}-{number:02}",
            workspace_id=workspace.id,
            title=prompt.title,
            qtype="open",
            topic=prompt.domain,
            skill=skill,
            difficulty=prompt.difficulty,
            role=workspace.target_role,
            level="2–4 年",
            source_type=SOURCE_PRIVATE,
            tags=["AI 应用后端", skill.name],
            answer_html=f"<p>{escape(prompt.reference)}</p>",
            created_at=datetime.combine(today - timedelta(days=12), time(19, 0), LOCAL_TIME),
            rubric={"points": list(prompt.criteria), "max_points": 5},
            generation_meta={
                "origin": "library_import",
                "assessment_use": "PRACTICE_ONLY",
                "library_answer": prompt.reference,
            },
        )
        session.add(question)
        questions.append(question)
    await session.flush()

    base_date = today - timedelta(days=5)
    baseline, evidence = await _record_exam(
        session, workspace.id, questions, BASELINE,
        title="AI 应用后端摸底练习", kind=Exam.KIND_BANK, day=base_date, minutes=45,
        reason="用异步导入服务和工单知识助手的典型场景，检查设计、排障和评估表达。",
    )

    completed = datetime.combine(today - timedelta(days=3), time(20), LOCAL_TIME)
    practice = _task(
        baseline, evidence[2], "用条件更新拦住迟到的 worker", "在写入边界校验 fencing token",
        "把检查移到提交点，分别验证新旧两个执行者的提交结果。",
        ["画出 A 暂停、B 接管、A 恢复的时序。", "写带代次的条件更新，并检查影响行数。", "在事务里记录 outbox，断言只产生一个可发布结果。"],
        "同一任务切换代次后，旧 worker 写入影响零行，新 worker 正常完成。",
        completed.date(), completed_at=completed,
    )
    eval_task = _task(
        baseline, evidence[4], "为 reranker 灰度写出回滚条件", "制定回滚判断",
        "原答只给了整体分数，权限泄露、引用退步和尾延迟都缺少停损条件。",
        ["固定无答案、跨权限与高频工单三组用例。", "分别写下质量与 p95 延迟指标及分母。", "写清停损阈值、观察窗口与切回动作。"],
        "提交一张切换判断表，逐项解释触发条件和负责的回滚动作。", today,
    )
    idempotency_task = _task(
        baseline, evidence[0], "补齐同键异参的导入契约", "处理同键异参",
        "原答遇到唯一键冲突就返回原任务，可能把另一个文件误判为重试。",
        ["列出同键同参、同键异参和键已过期三种输入。", "写出每种输入的返回值和任务状态。", "加一组并发提交断言，仅一个任务进入队列。"],
        "同键同参返回原任务 ID，同键异参返回 409，队列里只有一个任务。", today + timedelta(days=1),
    )
    tasks = [practice, eval_task, idempotency_task]
    session.add_all(tasks)
    await session.flush()
    baseline.summary = {
        **baseline.summary,
        "learning_tasks": [
            {"id": task.id, "title": task.title, "source_criterion": task.source_criterion,
             "source_exam_question_id": task.source_exam_question_id}
            for task in tasks
        ],
    }

    first, _, _ = await schedule_retests(session, practice)
    retest, _ = await _record_exam(
        session, workspace.id, questions, RETEST,
        title="延迟复测 · 陈旧执行者与下游发布", kind=Exam.KIND_FOCUSED,
        day=first.due_date, minutes=15,
        reason="换到工单摘要补发场景，检查能否迁移提交点的代次校验。",
        retest_task_id=first.id,
    )
    first.status = LearningTask.STATUS_DONE
    first.completed_at = retest.completed_at
    first.content = {
        **first.content,
        "retest_result": {
            "exam_id": retest.id, "score": retest.score, "passed": False,
            "outcome": "完成同能力域练习；原失分项待验证",
            "source_criterion_covered": False, "source_criterion_passed": None,
            "assessment_scope": "本次保留新题作答与人工评分，原评分项尚无审核映射。",
            "goal_outcomes": goal_outcomes(retest, first), "mode": "delayed",
            "completed_at": retest.completed_at.isoformat(),
        },
    }
    await _record_diagnostic(session, workspace, skills, today - timedelta(days=4))


async def _record_diagnostic(session, workspace, skills, day):
    origin = "synthetic_linzhou_fixture"
    started = datetime.combine(day, time(19, 30), LOCAL_TIME)
    submitted = started + timedelta(minutes=13)
    completed = started + timedelta(minutes=14)
    exam = Exam(
        workspace_id=workspace.id, title="基础诊断 · 后端与检索", kind=Exam.KIND_DIAGNOSIS,
        status=Exam.STATUS_COMPLETED, duration_minutes=15,
        selection_reason="分开检查后端数据一致性与检索基础判断，每个能力域三道独立题。",
        started_at=started, submitted_at=submitted, completed_at=completed, created_at=started,
        questions=[],
    )
    questions = []
    for index, prompt in enumerate(DIAGNOSTIC, 1):
        question = Question(
            workspace_id=workspace.id, short_code=f"LZ-{workspace.id}-D{index:02}",
            title=prompt.title, qtype="mcq", topic=prompt.domain, skill=skills[prompt.domain],
            difficulty=3, options=list(prompt.options), role=workspace.target_role,
            level="2–4 年", source_type=SOURCE_PRIVATE, answer_html=f"<p>{escape(prompt.reference)}</p>",
            rubric={"answer_index": prompt.correct, "max_points": 5,
                    "points": [{"title": prompt.criterion}]},
            generation_meta={"origin": origin, "library_answer": prompt.reference},
            created_at=started,
        )
        session.add(question)
        questions.append(question)
    await session.flush()

    for index, (question, prompt) in enumerate(zip(questions, DIAGNOSTIC, strict=True), 1):
        exam.questions.append(ExamQuestion(
            question=question, order_no=index, creation_snapshot=question_snapshot(question),
            answer=ExamAnswer(choice_index=prompt.selected, updated_at=completed),
        ))
    session.add(exam)
    await session.flush()

    for item in exam.questions:
        revision = await ensure_revision(session, item)
        base = item.creation_snapshot
        independence, assistance = await independence_snapshot(session, workspace.id, exam, item, base)
        if not independence["eligible_for_independent_verification"]:
            raise RuntimeError(f"Diagnostic question {item.question_id} is not independent")
        item.frozen_snapshot = {
            **base, "assessment_mode": "OFFICIAL", "independence": independence, "assistance": assistance,
            "submitted_answer": revision.payload, "answer_revision_id": revision.id,
            "answer_revision": revision.revision, "answer_hash": revision.content_hash, "origin": origin,
        }
        apply_result(item.answer, rule_result(item.frozen_snapshot))

    await _record_exam_evidence(session, exam, mode="OFFICIAL", origin=origin)
    await recompute_skill_states(session, workspace.id)


async def _record_exam(session, workspace_id, questions, attempts, *, title, kind, day, minutes, reason,
                       retest_task_id=None):
    started = datetime.combine(day, time(19, 30), LOCAL_TIME)
    submitted = started + timedelta(minutes=minutes - 3)
    completed = submitted + timedelta(minutes=2)
    exam = Exam(
        workspace_id=workspace_id, title=title, kind=kind, status=Exam.STATUS_COMPLETED,
        retest_task_id=retest_task_id, duration_minutes=minutes, selection_reason=reason,
        created_at=started, started_at=started, submitted_at=submitted, completed_at=completed,
        questions=[],
    )
    for order, attempt in enumerate(attempts, 1):
        question = questions[attempt.question]
        rubric = compile_rubric(
            question.rubric, question_id=question.id, title=question.title,
            skill_ids=[question.skill_id], role_level=question.level, reference_answer=question.answer_html,
        )
        criteria = []
        for criterion, score, quote in zip(rubric["criteria"], attempt.scores, attempt.quotes, strict=True):
            label = criterion["title"]
            criteria.append({
                "criterion_id": criterion["criterion_id"], "title": label, "score": score, "max_score": 4,
                "answer_quotes": [quote], "satisfied_anchors": [criterion["score_anchors"][score]["anchor_id"]],
                "missing_points": [label] if score < 3 else [],
                "status": "hit" if score >= 3 else "missing",
            })
        maximum = rubric["max_points"]
        points = round(sum(row["score"] * criterion["weight"] for row, criterion
                           in zip(criteria, rubric["criteria"], strict=True)) * maximum / 400, 2)
        verdict = "CORRECT" if points == maximum else "PARTIAL" if points >= maximum / 2 else "WRONG"
        feedback = {
            "criterion_scores": criteria,
            "hit_points": [row["title"] for row in criteria if row["status"] == "hit"],
            "missed_points": [row["title"] for row in criteria if row["status"] == "missing"],
            "judge_reasoning": attempt.feedback,
            "rubric_version": rubric["rubric_version"],
            "calibration_status": "LEGACY_UNCALIBRATED",
        }
        snapshot = {
            "question_id": question.id, "canonical_question_id": question.id, "short_code": question.short_code,
            "title": question.title, "qtype": question.qtype, "difficulty": question.difficulty,
            "skill_id": question.skill_id, "skill_key": question.skill.key, "skill_name": question.skill.name,
            "answer_html": question.answer_html, "rubric": rubric, "assessment_mode": "PRACTICE",
        }
        exam.questions.append(ExamQuestion(
            question=question, order_no=order, creation_snapshot=snapshot,
            frozen_snapshot={**snapshot, "submitted_answer": {
                "text_answer": attempt.answer, "choice_index": None, "transcript": None,
            }},
            answer=ExamAnswer(text_answer=attempt.answer, points=points, max_points=maximum,
                              verdict=verdict, feedback=feedback, judge_version=REVIEW_VERSION, updated_at=completed),
        ))

    session.add(exam)
    await session.flush()
    evidence = await _record_exam_evidence(session, exam, mode="PRACTICE")
    return exam, evidence


async def _record_exam_evidence(session, exam, *, mode, origin=None):
    exam.total_points = sum(item.answer.max_points for item in exam.questions)
    exam.gained_points = round(sum(item.answer.points for item in exam.questions), 2)
    exam.score = round(100 * exam.gained_points / exam.total_points, 1)
    evidence = []
    observations = []
    for item in exam.questions:
        answer = item.answer
        question = item.question
        ref = f"EV-X{exam.id}-{item.order_no}"
        meta = {
            "assessed": True, "assessment_mode": mode, "exam_id": exam.id, "exam_question_id": item.id,
            "question_id": question.id, "canonical_question_id": question.id, "qtype": question.qtype,
            "score": answer.points, "max_points": answer.max_points, "judge_version": answer.judge_version,
            "rubric_version": answer.feedback["rubric_version"],
            "criteria": answer.feedback["criterion_scores"], "missed_criteria": answer.feedback["missed_points"],
            "answer_excerpt": answer.text_answer, "reasoning": answer.feedback["judge_reasoning"],
        }
        if origin:
            frozen = item.frozen_snapshot
            meta.update(
                origin=origin, independent=frozen["independence"]["eligible_for_independent_verification"],
                assistance=frozen["assistance"], validation_status=answer.feedback["validation_status"],
                calibration_status=answer.feedback["calibration_status"],
                question_version=frozen["question_version"], answer_revision_id=frozen["answer_revision_id"],
                answer_excerpt=question.options[answer.choice_index], blank_answer=False,
                evidence_interpretation="合成工作空间内的独立选择题观察，用于形成练习能力估计。",
            )
        completed = exam.completed_at
        row = Evidence(
            short_code=ref, workspace_id=exam.workspace_id, skill_id=question.skill_id, kind="EXAM_ANSWER",
            title=f"{exam.title} Q{item.order_no} · {question.title}",
            detail=answer.feedback["judge_reasoning"], grade="OBSERVED_UNCALIBRATED", support=0,
            source_ref=f"exam:{exam.id}#q{item.order_no}", occurred_at=completed, created_at=completed, meta=meta,
        )
        session.add(row)
        evidence.append(row)
        answer.evidence_refs = [ref]
        observations.append({**meta, "evidence_ref": ref, "order_no": item.order_no,
                             "skill_key": question.skill.key, "skill_name": question.skill.name})
    exam.summary = {
        "mode": mode, "mastery_updated": mode == "OFFICIAL", "score": exam.score, "observations": observations,
        "verdicts": [{"order_no": item.order_no, "title": item.question.title, "verdict": item.answer.verdict}
                     for item in exam.questions],
    }
    if origin:
        exam.summary = {**exam.summary, "origin": origin, "learning_tasks": []}
    await session.flush()
    return evidence


def _task(exam, evidence, title, criterion, why, steps, acceptance, due, *, completed_at=None):
    return LearningTask(
        workspace_id=exam.workspace_id, skill_id=evidence.skill_id, title=title,
        kind=LearningTask.KIND_PRACTICE, source_exam_id=exam.id,
        source_exam_question_id=evidence.meta["exam_question_id"], source_evidence_id=evidence.id,
        source_criterion=criterion, why_ref=evidence.short_code, why=why, est_minutes=25,
        content={"steps": steps, "acceptance": acceptance}, acceptance=acceptance, due_date=due,
        status=LearningTask.STATUS_DONE if completed_at else LearningTask.STATUS_PENDING,
        created_at=exam.completed_at, completed_at=completed_at,
    )
