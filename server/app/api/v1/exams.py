"""考试中心 API: 状态机 CREATED→ACTIVE→SUBMITTED→TRANSCRIPT_CONFIRMED→FROZEN→SCORING→COMPLETED。

提交原子保存并冻结后返回任务；独立 Worker 逐题评分并持久化，最后原子发布报告、证据、画像和学习任务。
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json as _json
import re as _re
import time
from copy import deepcopy
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import (
    Evidence, Exam, ExamAnswer, ExamQuestion, LearningTask, Question, Skill, SkillState, Workspace,
)
from app.services.competency import recompute_skill_states
from app.services.followup import suggest_followups
from app.services.llm import adapter as llm_adapter
from app.services.llm_judge import judge_open_answer
from app.services.planner import plan_tasks_for_exam
from app.services.learning_loop import complete_retest_for_exam
from app.services.scorer import build_feedback, score_question
from app.services.retest_targets import load_retest_plan
from app.services.selection import select_questions_for_exam
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()


class CreateExamReq(BaseModel):
    kind: str = Exam.KIND_DIAGNOSIS
    question_count: int = Field(default=3, ge=1, le=20)
    duration_minutes: int = Field(default=30, ge=1, le=240)
    skill_key: str | None = None
    exclude_question_ids: list[int] = Field(default_factory=list)
    retest_task_id: int | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    question_ids: list[int] = Field(default_factory=list, max_length=20)
    project_generation_id: int | None = None


class SaveAnswerReq(BaseModel):
    order_no: int
    choice_index: int | None = Field(default=None,strict=True,ge=0)
    text_answer: str | None = Field(default=None,max_length=30000)
    transcript: str | None = Field(default=None,max_length=30000)
    expected_revision: int | None = Field(default=None,ge=0)


class SubmitExamReq(BaseModel):
    answers: list[SaveAnswerReq] = Field(default_factory=list,max_length=20)


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
            select(Exam).where(Exam.workspace_id == ws.id, Exam.kind != 'practice').order_by(Exam.id.desc()).limit(20)
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
    return await issue_exam(req, ws, session)


async def issue_exam(req, ws, session, *, study_practice=None, interview_set=None):
    if req.kind not in EXAM_KINDS:
        raise HTTPException(status_code=400, detail=f"unknown exam kind '{req.kind}'")
    if req.question_ids and (req.retest_task_id or req.kind != Exam.KIND_BANK):
        raise HTTPException(422, "指定题目仅用于面经练习，延迟复测必须使用系统新题选择")
    if req.project_generation_id is not None and (not req.question_ids or req.kind != Exam.KIND_BANK or req.retest_task_id):
        raise HTTPException(422, '项目批次考核需要选择该批次已审核题目')
    # Serialize retries and concurrent creates in the workspace transaction.
    await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
    if req.idempotency_key:
        existing = (await session.execute(select(Exam).where(
            Exam.workspace_id == ws.id, Exam.idempotency_key == req.idempotency_key,
        ))).scalar_one_or_none()
        if existing:
            await session.commit()
            return await _exam_detail(session, ws, existing.id)
    skill_key = req.skill_key
    excluded = list(req.exclude_question_ids)
    task = None
    retest_plan = None
    study_topic = None
    if study_practice is not None:
        from app.services.study_practice import assert_retest_ready, resolve_retest_skill, skill_snapshot, taught_question_ids
        if study_practice.retest_exam_id:
            previous = await _get_own_exam(session, ws, study_practice.retest_exam_id, lock=True)
            _maybe_expire(previous)
            if previous.status not in ('ABORTED', 'EXPIRED'):
                return await _exam_detail(session, ws, previous.id)
            prior = (study_practice.source or {}).get('closed_retests', [])
            study_practice.source = {**study_practice.source, 'closed_retests':
                [*prior, {'exam_id': previous.id, 'status': previous.status}]}
        skill = await resolve_retest_skill(session, study_practice)
        if skill is None:
            raise HTTPException(409, '练习尚未关联明确的能力域，不能据此安排正式复测')
        await assert_retest_ready(session, study_practice, skill)
        skill_key = skill.key
        study_topic = study_practice.knowledge_point
        if study_practice.source.get('question_id'):
            excluded.append(study_practice.source['question_id'])
        excluded.extend(await taught_question_ids(session, study_practice))
        attempted = await session.scalars(select(ExamQuestion.question_id).join(Exam).where(Exam.workspace_id == ws.id))
        excluded.extend(attempted)
        study_practice.source = {**study_practice.source,
            'skill_state_before': await skill_snapshot(session, ws.id, skill.id)}
    if req.retest_task_id:
        task = (await session.execute(select(LearningTask).where(
            LearningTask.id == req.retest_task_id, LearningTask.workspace_id == ws.id,
        ))).scalar_one_or_none()
        if task is None:
            raise HTTPException(status_code=404, detail="retest task not found")
        if not task.skill_id:
            raise HTTPException(status_code=409, detail="retest task has no assessed skill")
        if task.kind != "RETEST":
            raise HTTPException(status_code=409, detail="retest_task_id must reference a retest task")
        if task.status == "DONE":
            raise HTTPException(status_code=409, detail="this retest is already scored; open its existing report")
        if task.kind == "RETEST" and task.due_date and task.due_date > dt.date.today():
            raise HTTPException(status_code=409, detail="scheduled retest is not due")
        skill = await session.get(Skill, task.skill_id)
        if skill_key and skill_key != skill.key:
            raise HTTPException(status_code=400, detail="retest must assess the source skill")
        skill_key = skill.key
        attempted = (await session.execute(select(ExamQuestion.question_id).join(Exam).where(Exam.workspace_id == ws.id))).scalars().all()
        excluded.extend(attempted)
        retest_plan = await load_retest_plan(session, ws, task)
    selected = None
    if req.question_ids:
        rows = (await session.scalars(select(Question).where(Question.id.in_(req.question_ids), (Question.workspace_id.is_(None)) | (Question.workspace_id == ws.id)))).all()
        by_id = {q.id: q for q in rows}
        if set(by_id) != set(req.question_ids):
            raise HTTPException(404, "指定题目不存在或不属于当前工作空间")
        selected, seen = [], set()
        for qid in req.question_ids:
            q = by_id[qid]
            if (q.generation_meta or {}).get('assessment_use') == 'PRACTICE_ONLY' and (
                not interview_set or q.generation_meta.get('interview_set_id') != interview_set['id']
            ):
                raise HTTPException(409, '学习练习题已曝光，不能作为正式独立考题')
            if q.qtype not in ("open", "mcq") or (skill_key and (not q.skill or q.skill.key != skill_key)):
                raise HTTPException(422, "指定题目不符合本次练习的技能或题型")
            root, visited = q, set()
            while root.canonical_question_id and root.id not in visited:
                visited.add(root.id)
                root = await session.get(Question, root.canonical_question_id)
                if not root or root.workspace_id not in (None, ws.id):
                    raise HTTPException(409, "题目来源关系不可用")
            if qid in excluded or root.id in excluded:
                raise HTTPException(422, "指定题目已被本次练习排除")
            if root.id not in seen:
                selected.append(q)
                seen.add(root.id)
    meta = EXAM_KINDS[req.kind]

    exam = Exam(
        workspace_id=ws.id,
        title=interview_set['title'] if interview_set else meta["title"],
        kind=req.kind,
        status=Exam.STATUS_ACTIVE,
        duration_minutes=req.duration_minutes or meta["default_minutes"],
        started_at=dt.datetime.now(dt.timezone.utc),
        retest_task_id=req.retest_task_id,
        idempotency_key=req.idempotency_key,
    )
    session.add(exam)
    await session.flush()

    # 选题 + trace
    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "exam_creation", f"创建考试: {meta['title']}", short_code=f"TR-E{exam.id}")
    if selected is not None:
        questions, reason = selected, "用户选择已入库题目进行真实作答练习；同源变体只计一道独立题，阅读答案不构成能力证明。"
    else:
        questions, reason = await select_questions_for_exam(session, ws.id, exam, req.question_count,
                                                          skill_key=skill_key, exclude_question_ids=excluded,
                                                          retest_plan=retest_plan,
                                                          exclude_exposed=bool(task or study_practice),
                                                          study_topic=(study_practice.source.get('retest_topic') or study_topic)
                                                          if study_practice else study_topic)
    if not questions:
        await session.rollback()
        raise HTTPException(status_code=409, detail="当前能力域没有可用的新题，请先补充题库后再验证")
    from app.services.project_assessment import freeze_assessment
    exam.project_assessment = await freeze_assessment(session, ws.id, questions, req.project_generation_id)
    from app.services.assessment_versions import digest, question_snapshot
    from app.models import JobStandard, JobStandardTarget
    standard_role = 'AI Agent 应用开发工程师'
    standard=await session.scalar(select(JobStandard).where(
        JobStandard.role == standard_role, JobStandard.is_seed.is_(True))
        .order_by(JobStandard.id.desc()).limit(1))
    if standard:
        targets=(await session.scalars(select(JobStandardTarget).where(
            JobStandardTarget.standard_id == standard.id).order_by(JobStandardTarget.skill_id, JobStandardTarget.id))).all()
        standard_snapshot={
            'id':standard.id,'role':standard.role,'level':standard.level,'version':str(standard.version),
            'status':'CONFIGURED' if targets else 'INCOMPLETE',
            'is_seed':standard.is_seed,'calibration_status':'SEED_ASSUMPTION',
            'scope_note':'冻结的岗位目标是种子假设，尚未人工校准，不能据此证明达到招聘要求。',
            'targets':[{'skill_id':target.skill_id,'skill_key':target.skill.key,
                'skill_name':target.skill.name,'target_level':target.target_level,
                'weight_pct':target.weight_pct} for target in targets],
        }
    else:
        standard_snapshot={'id':None,'role':standard_role,'level':None,'version':None,
            'status':'NOT_CONFIGURED','calibration_status':'UNCALIBRATED','targets':[],
            'scope_note':'未配置岗位目标标准；本次评分仅记录作答表现，不能判断是否达到岗位要求。'}
    snapshots = [question_snapshot(q, standard_snapshot) for q in questions]
    if interview_set:
        reason = f"来自「{interview_set['project_name']}」的独立套题；仅用于项目面试练习。"
        for snapshot, question in zip(snapshots, questions):
            snapshot['interview_set'] = question.generation_meta
            snapshot.pop('question_version', None)
            snapshot['question_version'] = digest(snapshot)
    if study_practice is not None:
        from app.services.study_practice import card_event_through
        study_practice.retest_exam_id = exam.id
        snapshots[0].update(study_practice_id=study_practice.id, study_session_id=study_practice.session_id,
                            study_knowledge_point=study_topic,
                            study_card_event_through=await card_event_through(session, study_practice))
        reason += f" 核对「{study_topic}」相关的新情境，画像只使用独立作答的有效评价。"
    if task and retest_plan:
        covered = retest_plan.freeze(snapshots)
        scope = ("本次出卷已冻结原失分评分项的审核映射；结果仅验证这次作答。" if covered
                 else "本次可以练习同能力域新题，原失分评分项缺少审核映射，待验证。")
        task.content = {**(task.content or {}), "source_criterion_covered": covered, "assessment_scope": scope}
        reason += " " + scope
    add_span(session, trace, "select_questions", int((time.perf_counter() - t0) * 1000), detail={"count": len(questions), "reason": reason})
    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000))
    exam.selection_reason = reason
    for i, (q, snapshot) in enumerate(zip(questions, snapshots), start=1):
        session.add(ExamQuestion(exam_id=exam.id, question_id=q.id, order_no=i,
            creation_snapshot=snapshot))
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
    exam = await _get_own_exam(session, ws, exam_id, lock=True)
    if exam.status != Exam.STATUS_ACTIVE:
        raise HTTPException(status_code=409, detail=f"exam status is {exam.status}, cannot save answers")
    eq = next((q for q in exam.questions if q.order_no == req.order_no), None)
    if eq is None:
        raise HTTPException(status_code=404, detail="question not found in exam")

    if _maybe_expire(exam):
        await session.commit()
        raise HTTPException(409,'考试已超时，已保存的答案保留')
    from app.services.assessment_versions import save_revision
    answer=await save_revision(session,eq,req)
    await session.commit()
    return {"ok": True,"revision":answer.revision,"saved_at":dt.datetime.now(dt.timezone.utc).isoformat()}



def _maybe_expire(exam: Exam) -> bool:
    """超时未交卷 → EXPIRED(2 分钟宽限)。返回是否刚好置为 EXPIRED。"""
    if exam.status == Exam.STATUS_ACTIVE and exam.started_at and exam.duration_minutes:
        deadline = exam.started_at + dt.timedelta(minutes=exam.duration_minutes + 2)
        if dt.datetime.now(dt.timezone.utc) > deadline:
            exam.status = Exam.STATUS_EXPIRED
            return True
    return False


def _assessment_question(eq: ExamQuestion):
    """Frozen assessment material, with a legacy fallback for old records."""
    q, snap = eq.question, eq.frozen_snapshot or getattr(eq,'creation_snapshot',None) or {}
    skill = q.skill
    return SimpleNamespace(
        id=snap.get("question_id", q.id),
        short_code=snap.get("short_code", q.short_code),
        canonical_question_id=snap.get("canonical_question_id", q.canonical_question_id or q.id),
        title=snap.get("title", q.title), qtype=snap.get("qtype", q.qtype),
        options=snap.get("options", q.options), rubric=deepcopy(snap.get("rubric", q.rubric) or {}),
        answer_html=snap.get("answer_html", q.answer_html),
        skill_id=snap.get("skill_id", q.skill_id),
        skill=SimpleNamespace(key=snap.get("skill_key", skill.key if skill else "general"),
                              name=snap.get("skill_name", skill.name if skill else "综合")),
        difficulty=snap.get("difficulty", q.difficulty),
    )


def _submitted_answer(eq: ExamQuestion) -> dict:
    a = eq.answer
    return (eq.frozen_snapshot or {}).get("submitted_answer") or {
        "choice_index": a.choice_index if a else None,
        "text_answer": a.text_answer if a else None,
        "transcript": a.transcript if a else None,
    }


async def _score_questions(session: AsyncSession, trace, exam: Exam) -> list[int]:
    """Score frozen input. Nonblank open answers require a working model."""
    await llm_adapter._effective()  # DB settings must be loaded before active check.
    failed: list[int] = []
    gained = 0.0
    total = 0.0
    for eq in exam.questions:
        q = _assessment_question(eq)
        answer = eq.answer
        submitted = _submitted_answer(eq)
        max_p = (q.rubric or {}).get("max_points", 5.0)
        total += max_p
        if answer is None:
            answer = ExamAnswer(exam_question_id=eq.id, max_points=max_p)
            session.add(answer)
            await session.flush()
            eq.answer = answer
        answer.max_points = max_p

        if q.qtype == "open" and (submitted.get("text_answer") or "").strip():
            tq = time.perf_counter()
            jr = await judge_open_answer(
                llm_adapter,
                title=q.title,
                skill_name=q.skill.name if q.skill else None,
                rubric={**(q.rubric or {}), "answer_html": q.answer_html},
                text=submitted.get("text_answer"),
                trace=trace, session=session, span_name=f"llm:judge_q{eq.order_no}",
            ) if llm_adapter.active else None
            qms = int((time.perf_counter() - tq) * 1000)
            if jr is not None:
                answer.points = jr["points"]
                answer.verdict = jr["verdict"]
                answer.judge_version = "judge-v1"
                answer.feedback = {
                    "hit_points": jr["hit"],
                    "missed_points": jr["missing"],
                    "advice": jr["reasoning"],
                    "judge_reasoning": jr["reasoning"],
                    "followups": suggest_followups(q.title, jr["verdict"], jr["missing"]),
                }
                gained += jr["points"]
                add_span(session, trace, f"judge_q{eq.order_no}", qms, detail={"judge": "judge-v1", "verdict": jr["verdict"], "points": jr["points"]})
                continue
            # judge 失败: 该题显式 SCORING_FAILED, 不给任何分数
            failed.append(eq.order_no)
            answer.points = None
            answer.verdict = "SCORING_FAILED"
            answer.judge_version = None
            answer.feedback = {"error": "AI 评分失败，未生成分数（不会用假分顶替；可点「重新评分」重试）"}
            add_span(session, trace, f"judge_q{eq.order_no}", qms, status="ERROR", detail={"reason": "judge_failed"})
            continue

        # Rules are valid only for MCQ or a literally empty answer.
        result = score_question(q, choice_index=submitted.get("choice_index"), text=submitted.get("text_answer"))
        answer.points = result["points"]
        answer.verdict = result["verdict"]
        answer.judge_version = "rule-v1"
        fb = build_feedback(q, result)
        fb["followups"] = suggest_followups(
            q.title, result["verdict"],
            [p.get("title", "") if isinstance(p, dict) else str(p) for p in result.get("misses", [])],
        )
        answer.feedback = fb
        gained += result["points"]

    exam.total_points = total
    if failed:
        exam.gained_points = None
        exam.score = None
    else:
        exam.gained_points = round(gained, 1)
        exam.score = round(100 * gained / total, 1) if total else 0.0
    return failed


async def _failed_summary(exam: Exam, failed: list[int]) -> dict:
    return {
        "verdicts": [
            {"order_no": eq.order_no, "title": _assessment_question(eq).title[:40], "verdict": eq.answer.verdict if eq.answer else "SCORING_FAILED"}
            for eq in exam.questions
        ],
        "score": None,
        "scoring_failed": failed,
    }


async def _finalize_exam(session: AsyncSession, ws: Workspace, exam: Exam, trace, t0: float) -> None:
    """全部题评分成功后的收尾: COMPLETED + 报告小结 + 证据 + 重算能力 + 学习任务。"""
    exam.status = Exam.STATUS_COMPLETED
    exam.completed_at = dt.datetime.now(dt.timezone.utc)
    exam.summary = {
        "verdicts": [
            {"order_no": eq.order_no, "title": _assessment_question(eq).title[:40], "verdict": eq.answer.verdict if eq.answer else "WRONG"}
            for eq in exam.questions
        ],
        "score": exam.score,
        "judges": {
            "judge_v1": sum(1 for eq in exam.questions if eq.answer and eq.answer.judge_version == "judge-v1"),
            "rule_v1": sum(1 for eq in exam.questions if eq.answer and eq.answer.judge_version == "rule-v1"),
            "judge_v2": sum(1 for eq in exam.questions if eq.answer and eq.answer.judge_version == "judge-v2"),
            "rule_v2": sum(1 for eq in exam.questions if eq.answer and eq.answer.judge_version == "rule-v2"),
        },
    }

    if exam.questions and all((eq.frozen_snapshot or {}).get('interview_set') for eq in exam.questions):
        exam.summary = {**exam.summary, "observations": [], "learning_tasks": []}
        await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000))
        await emit_event(session, ws.id, "exam.completed", {"exam_id": exam.id, "score": exam.score}, "exam", exam.id)
        return

    # 6) 写证据(完成后才有资格作为能力证据)
    evidence_t0 = time.perf_counter()
    observations = []
    for eq in exam.questions:
        q = _assessment_question(eq)
        answer = eq.answer
        submitted = _submitted_answer(eq)
        fb = (answer.feedback if answer else None) or {}
        normalize = lambda values: [str(p.get("title", "") if isinstance(p, dict) else p) for p in values]
        missed = normalize(fb.get("missed_points") or [])
        hits = normalize(fb.get("hit_points") or [])
        criteria = [{"criterion_id": str(i), "title": title,
                     "status": "missing" if title in missed else ("hit" if title in hits else "unassessed")}
                    for i, title in enumerate(normalize((q.rubric or {}).get("points") or []), 1)]
        if fb.get('criterion_scores'):
            names={c['criterion_id']:c['title'] for c in (q.rubric or {}).get('criteria',[])}
            criteria=[{**row,'title':names.get(row['criterion_id'],row.get('title',row['criterion_id'])),
                'status':'hit' if row['score']>=3 else 'missing'} for row in fb['criterion_scores']]
        meta = {
            "assessed": True, "exam_id": exam.id, "exam_question_id": eq.id,
            "question_id": q.id, "canonical_question_id": ((eq.frozen_snapshot or {}).get('independence') or {}).get(
                'canonical_question_id', q.canonical_question_id),
            "score": answer.points, "max_points": answer.max_points, "judge_version": answer.judge_version,
            "qtype": q.qtype, "blank_answer": not bool((submitted.get("text_answer") or "").strip()),
            "criteria": criteria, "missed_criteria": missed,
            "answer_excerpt": (submitted.get("text_answer") or (f"选项 {submitted['choice_index']}" if submitted.get("choice_index") is not None else "未作答"))[:1000],
            "reasoning": fb.get("judge_reasoning") or fb.get("advice"),
            "answer_revision_id":(eq.frozen_snapshot or {}).get('answer_revision_id'),
            "question_version":(eq.frozen_snapshot or {}).get('question_version'),
            "rubric_version":(q.rubric or {}).get('rubric_version','legacy-unversioned'),
            "scoring_run_id":getattr(exam,'active_scoring_run_id',None),
            "calibration_status":fb.get('calibration_status','LEGACY_UNCALIBRATED'),
            "validation_status":fb.get('validation_status','LEGACY_NOT_REVALIDATED'),
            "critical_errors":fb.get('critical_errors',[]),
            "assessment_mode":(eq.frozen_snapshot or {}).get('assessment_mode','OFFICIAL'),
            "independent":((eq.frozen_snapshot or {}).get('independence') or {}).get('eligible_for_independent_verification'),
            "assistance":(eq.frozen_snapshot or {}).get('assistance') or {},
        }
        verdict = answer.verdict if answer else "WRONG"
        support = {"CORRECT": 0.6, "PARTIAL": 0.15, "WRONG": -0.4}.get(verdict, 0.0)
        grade = "VERIFIED" if verdict == "CORRECT" else ("MEDIUM" if verdict == "PARTIAL" else "LOW")
        if answer.judge_version in ('judge-v2', 'rule-v2') and fb.get('validation_status') == 'VALID' \
                and fb.get('calibration_status') not in ('CALIBRATED', 'HUMAN_CALIBRATED'):
            # Structural validation and a high answer score do not establish
            # calibrated reliability. Retain the scored observation for the
            # independent-question coverage heuristic without a trust label.
            grade, support = 'OBSERVED_UNCALIBRATED', 0.0
            meta['evidence_interpretation'] = '本次作答观察；评分尚未校准，不表示能力已验证或招聘判断可靠。'
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
            meta=meta,
        )
        session.add(ev)
        observations.append({**meta, "evidence_ref": ev.short_code, "order_no": eq.order_no,
                             "skill_key": q.skill.key, "skill_name": q.skill.name})
        if answer is not None:
            answer.evidence_refs = [ev.short_code]
    await session.flush()  # SessionLocal disables autoflush; queries must see this evidence.
    add_span(session, trace, "write_evidence", int((time.perf_counter() - evidence_t0) * 1000), detail={"evidence": len(exam.questions)})

    # 7) 重算能力 + 生成学习任务
    plan_t0 = time.perf_counter()
    await recompute_skill_states(session, ws.id)
    tasks = await plan_tasks_for_exam(session, ws.id, exam)
    await complete_retest_for_exam(session, exam)
    exam.summary = {**exam.summary, "observations": observations,
                    "learning_tasks": [{"id": task.id, "title": task.title, "source_criterion": task.source_criterion,
                                        "source_exam_question_id":task.source_exam_question_id,
                                        "criterion_id":(task.content or {}).get('observation',{}).get('criterion_id')}
                                       for task in tasks]}
    add_span(session, trace, "recompute_and_plan", int((time.perf_counter() - plan_t0) * 1000), detail={"tasks": len(tasks)})

    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000))
    await emit_event(session, ws.id, "exam.completed", {"exam_id": exam.id, "score": exam.score}, "exam", exam.id)


@router.post("/exams/{exam_id}/submit", status_code=202)
async def submit_exam(
    exam_id: int, ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session), req: SubmitExamReq | None = None,
):
    """Atomically save the last edits and freeze; independent Worker grades later."""
    from app.services import assessment_jobs as jobs
    from app.services.assessment_versions import save_revision
    exam=await _get_own_exam(session,ws,exam_id,lock=True)
    if exam.status=='COMPLETED':
        await session.commit()
        return await _exam_report(session,ws,exam.id)
    if exam.active_scoring_run_id:
        from app.models import ScoringRun
        run=await session.get(ScoringRun,exam.active_scoring_run_id)
        await session.commit()
        return jobs.ack(run)
    if _maybe_expire(exam):
        await session.commit()
        raise HTTPException(409,'考试已超时，已保存答案保留')
    if exam.status not in ('ACTIVE','SUBMITTED','SCORING_FAILED'):
        raise HTTPException(409,'当前考试已冻结，不能重新提交答案')
    if req and req.answers:
        if exam.status!='ACTIVE':raise HTTPException(409,'已冻结的答案不能改写')
        by_order={eq.order_no:eq for eq in exam.questions}
        if len({a.order_no for a in req.answers})!=len(req.answers):
            raise HTTPException(422,'提交批次包含重复题号')
        for item in req.answers:
            if item.order_no not in by_order:raise HTTPException(404,'题号不属于本场考试')
            await save_revision(session,by_order[item.order_no],item)
    run=await jobs.enqueue(session,ws,exam)
    await session.commit()
    return jobs.ack(run)


@router.post("/exams/{exam_id}/abort")
async def abort_exam(
    exam_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """放弃考试: CREATED/ACTIVE/SUBMITTED → ABORTED(不可恢复)。"""
    exam = await _get_own_exam(session, ws, exam_id, lock=True)
    if exam.active_scoring_run_id and exam.status in ('SCORING','SCORING_RETRY_WAIT','SCORING_FAILED','NEEDS_REVIEW'):
        from app.services.assessment_jobs import cancel
        await cancel(session,ws,exam)
        await session.commit()
        return {'id':exam.id,'status':exam.status}
    if exam.status not in (Exam.STATUS_CREATED, Exam.STATUS_ACTIVE, Exam.STATUS_SUBMITTED):
        raise HTTPException(status_code=409, detail=f"exam status is {exam.status}, cannot abort")
    exam.status = Exam.STATUS_ABORTED
    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "exam_abort", f"放弃考试: {exam.title}", trigger="user", trigger_ref=f"exam:{exam.id}")
    add_span(session, trace, "abort", 5, detail={"status": "ABORTED"})
    await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000))
    await emit_event(session, ws.id, "exam.aborted", {"exam_id": exam.id}, "exam", exam.id)
    await session.commit()
    return {"id": exam.id, "status": Exam.STATUS_ABORTED}


@router.post("/exams/{exam_id}/rescore", status_code=202)
async def rescore_exam(
    exam_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session),
):
    """Retry only failed frozen inputs; completed historical grades are immutable."""
    from app.services import assessment_jobs as jobs
    exam=await _get_own_exam(session,ws,exam_id,lock=True)
    if exam.status=='COMPLETED':
        await session.commit()
        return await _exam_report(session,ws,exam.id)
    if exam.status not in ('SCORING_FAILED','NEEDS_REVIEW','SCORING','SCORING_RETRY_WAIT'):
        raise HTTPException(409,'当前状态没有可恢复的评分任务')
    try:run=await jobs.resume(session,ws,exam)
    except ValueError as exc:raise HTTPException(409,str(exc)) from None
    await session.commit()
    return jobs.ack(run)


@router.get("/exams/{exam_id}/report")
async def exam_report(
    exam_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    return await _exam_report(session, ws, exam_id)


async def _get_own_exam(session: AsyncSession, ws: Workspace, exam_id: int, *, lock: bool = False) -> Exam:
    if lock:
        await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
    query = select(Exam).where(Exam.id == exam_id, Exam.workspace_id == ws.id)
    if lock:
        query = query.with_for_update(of=Exam).execution_options(populate_existing=True)
    exam = (
        await session.execute(query)
    ).scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="exam not found")
    return exam


async def _exam_detail(session: AsyncSession, ws: Workspace, exam_id: int) -> dict:
    # Expiration mutates state: serialize with submit, then refresh any stale
    # ACTIVE identity-map value before deciding whether the exam is overdue.
    exam = await _get_own_exam(session, ws, exam_id, lock=True)
    if _maybe_expire(exam):
        await session.commit()  # 懒惰置为 EXPIRED, 让考试中心如实显示
    return {
        "id": exam.id,
        "title": exam.title,
        "kind": exam.kind,
        "status": exam.status,
        "duration_minutes": exam.duration_minutes,
        "selection_reason": exam.selection_reason,
        "interview_set_id": next(((eq.creation_snapshot or {}).get('interview_set', {}).get('interview_set_id')
                                  for eq in exam.questions if (eq.creation_snapshot or {}).get('interview_set')), None),
        "started_at": exam.started_at.isoformat() if exam.started_at else None,
        "questions": [
            {
                "order_no": eq.order_no,
                "short_code": q.short_code,
                "title": q.title,
                "qtype": q.qtype,
                "options": q.options,
                "difficulty": q.difficulty,
                "skill": q.skill.name if q.skill else None,
                "rubric_visible": False,  # 答题中不暴露 rubric
                "answer": (
                    {
                        "choice_index": eq.answer.choice_index,
                        "text_answer": eq.answer.text_answer,
                        "transcript": eq.answer.transcript,
                        "revision":eq.answer.revision,
                    }
                    if eq.answer
                    else None
                ),
            }
            for eq in exam.questions
            for q in [_assessment_question(eq)]
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
        q = _assessment_question(eq)
        key = q.skill.key if q.skill else "general"
        name = q.skill.name if q.skill else "综合"
        a = eq.answer
        pts = a.points if a is not None and a.points is not None else 0.0
        mp = (q.rubric or {}).get("max_points", 5.0)
        g = groups.setdefault(key, {
            "skill_key": key, "skill_name": name, "q_count": 0,
            "earned": 0.0, "max": 0.0, "scoring_complete": True,
            "verdicts": {"CORRECT": 0, "PARTIAL": 0, "WRONG": 0},
            "order_nos": [],
        })
        g["q_count"] += 1
        g["earned"] += pts
        if a is None or a.points is None:
            g["scoring_complete"] = False
        g["max"] += mp
        if a is not None and a.verdict in g["verdicts"]:
            g["verdicts"][a.verdict] += 1
        g["order_nos"].append(eq.order_no)
    out = []
    for g in groups.values():
        g["earned"] = round(g["earned"], 1) if g["scoring_complete"] else None
        g["max"] = round(g["max"], 1)
        g["ratio"] = (round(g["earned"] / g["max"], 2) if g["max"] else 0.0) if g["scoring_complete"] else None
        out.append(g)
    out.sort(key=lambda x: x["ratio"] if x["ratio"] is not None else -1)
    return out


def _overall_diagnosis(breakdown: list[dict], exam: Exam, states_map: dict[str, SkillState]) -> dict:
    """确定性诊断结论(无 LLM 也成立): 覆盖域/强项/薄弱/优先行动。"""
    assessed = [g for g in breakdown if g["ratio"] is not None]
    strong = [g for g in assessed if g["ratio"] >= 0.8]
    mid = [g for g in assessed if 0.6 <= g["ratio"] < 0.8]
    weak = [g for g in assessed if g["ratio"] < 0.6]
    weakest = weak[0] if weak else (mid[0] if mid else (assessed[0] if assessed else None))

    parts: list[str] = []
    if breakdown:
        parts.append(f"本次诊断覆盖了 {len(breakdown)} 个能力域：{'、'.join(g['skill_name'] for g in breakdown)}。")
    if strong:
        parts.append(f"本次题目中，{'、'.join(g['skill_name'] for g in strong)} 得分率较高。")
    if mid:
        parts.append(f"本次题目中，{'、'.join(g['skill_name'] for g in mid)} 仍有缺失评分项。")
    if weak:
        parts.append(f"本次题目中，{'、'.join(g['skill_name'] for g in weak)} 得分率较低，需针对缺失评分项练习。")
    else:
        parts.append("这些结果仅描述本次作答；整体能力需要多道独立题目的有效证据。")
    if any(not states_map.get(g["skill_key"]) or states_map[g["skill_key"]].estimated_level is None for g in breakdown):
        parts.append("独立证据不足的能力域仍为 UNKNOWN，不把单题分数视为整个能力域水平。")

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
                (f"优先补「{weakest['skill_name']}」：" if weakest['ratio'] < 1 else
                 f"继续验证「{weakest['skill_name']}」：本次没有记录缺失评分项。")
                + f"本次得分率 {int(weakest['ratio'] * 100)}%，"
                + (f"当前启发式估计 {cur:.1f}/5（{status}）。" if cur is not None else "整体能力仍未知；当前观察已记录，需更多独立题目验证。")
                + (" 已关联本次缺失评分项的学习任务。" if (exam.summary or {}).get("learning_tasks") else "")
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


def _sanitize_pq(pq, expect: set[str]) -> dict[str, dict[str, str]]:
    """只保留指定题号及包含非空 why 和 how 的讲解。"""
    if not isinstance(pq, dict):
        return {}
    out = {}
    for k, v in pq.items():
        nk = _norm_pq_key(k)
        if nk not in expect or not isinstance(v, dict):
            continue
        why, how = v.get("why"), v.get("how")
        if isinstance(why, str) and why.strip() and isinstance(how, str) and how.strip():
            out[nk] = {"why": why, "how": how}
    return out


def _deep_coverage(expect: set[str], per_question: dict[str, dict[str, str]]) -> dict:
    missing = sorted(int(no) for no in expect.difference(per_question))
    return {"total": len(expect), "explained": len(expect) - len(missing), "missing_order_nos": missing}


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

    await llm_adapter._effective()
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
        q = _assessment_question(eq)
        a = eq.answer
        fb = (a.feedback if a else None) or {}
        hits = "、".join((p.get("title", p) if isinstance(p, dict) else str(p)) for p in (fb.get("hit_points") or [])) or "无"
        misses = "、".join((p.get("title", p) if isinstance(p, dict) else str(p)) for p in (fb.get("missed_points") or [])) or "无"
        submitted = _submitted_answer(eq)
        yours = submitted.get("text_answer") or (f"选择题，选了 {submitted['choice_index']}" if submitted.get("choice_index") is not None else "未作答")
        block = "\n".join([
            f"【Q{eq.order_no}】（{q.skill.name if q.skill else '综合'} · {q.qtype} · {a.points if a else 0}/{(q.rubric or {}).get('max_points', 5)} 分 · {a.verdict if a else 'WRONG'}）",
            f"题目：{q.title}",
            f"考生作答：{_strip_html(yours, 500)}",
            f"命中要点：{hits}；缺失要点：{misses}",
            f"参考答案（摘要）：{_strip_html(q.answer_html, 800)}",
        ])
        q_material[eq.order_no] = block
        lines.append(block)
        lines.append("")
    lines.append(f"确定性结论（供参考）：{deterministic['conclusion']}")

    resp = await llm_adapter.chat(DEEP_DIAG_SYSTEM, "\n".join(lines), json_mode=True, timeout=120, trace=trace, session=session, span_name="llm:deep_diagnosis")
    if resp is None:
        await finish_trace(session, trace, "FAILED", int((time.perf_counter() - t0) * 1000), fail_reason="llm call failed")
        await session.commit()
        raise HTTPException(status_code=502, detail="LLM 调用失败，请稍后重试（确定性结论已在报告中给出，不受影响）")

    try:
        deep_raw = _json.loads(resp["content"])
        if not isinstance(deep_raw, dict):
            raise ValueError("deep diagnosis must be a JSON object")
    except Exception:
        await finish_trace(session, trace, "FAILED", int((time.perf_counter() - t0) * 1000), fail_reason="llm bad json")
        await session.commit()
        raise HTTPException(status_code=502, detail="LLM 返回格式异常，请重试一次")

    # 确定性校验: 只收合法条目; 缺题时用最小化材料补齐一次(修复 deepseek 偶发漏题/占位键问题)。
    expect = {str(eq.order_no) for eq in exam.questions}
    per_q = _sanitize_pq(deep_raw.get("per_question"), expect)
    retried = False
    missing = expect.difference(per_q)
    if missing:
        missing_in_order = sorted(missing, key=int)
        fix = [f"第一次输出缺少题目 {'、'.join(missing_in_order)} 的 per_question 条目。请为这些题目补齐讲解。", ""]
        fix.extend(q_material[int(no)] for no in missing_in_order)
        resp2 = await llm_adapter.chat(DEEP_DIAG_FIX, "\n".join(fix), json_mode=True, timeout=120, trace=trace, session=session, span_name="llm:deep_diagnosis_fix")
        if resp2:
            try:
                per_q.update(_sanitize_pq(_json.loads(resp2["content"]).get("per_question"), missing))
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
    coverage = _deep_coverage(expect, per_q)
    missing_order_nos = coverage["missing_order_nos"]
    await finish_trace(
        session, trace, "PARTIAL" if missing_order_nos else "SUCCESS",
        int((time.perf_counter() - t0) * 1000), 0.02,
        fail_reason="缺少逐题讲解：" + "、".join(f"Q{no}" for no in missing_order_nos) if missing_order_nos else None,
    )
    await emit_event(session, ws.id, "exam.deep_diagnosis", {"exam_id": exam.id}, "exam", exam.id)
    await session.commit()
    return {**deep, "coverage": coverage}


async def _exam_report(session: AsyncSession, ws: Workspace, exam_id: int) -> dict:
    exam = await _get_own_exam(session, ws, exam_id)
    summary = exam.summary or {}
    deep = summary.get("diagnosis_deep") if exam.status == Exam.STATUS_COMPLETED else None
    if deep:
        expect = {str(eq.order_no) for eq in exam.questions}
        per_q = _sanitize_pq(deep.get("per_question"), expect)
        deep = {**deep, "per_question": per_q, "coverage": _deep_coverage(expect, per_q)}
    if exam.status=='COMPLETED' and getattr(exam,'active_scoring_run_id',None):
        from app.models import ScoringRun
        stored=await session.get(ScoringRun,exam.active_scoring_run_id)
        if stored and stored.workspace_id==ws.id and stored.status=='COMPLETED' and stored.report:
            result=deepcopy(stored.report)
            if deep:result.update(diagnosis_deep=deep,has_deep=True)
            return result
    if exam.status not in (Exam.STATUS_COMPLETED, Exam.STATUS_SCORING_FAILED,'SCORING','SCORING_RETRY_WAIT','NEEDS_REVIEW','FROZEN'):
        raise HTTPException(status_code=409, detail="exam not completed yet")
    degraded = exam.status != Exam.STATUS_COMPLETED
    from app.services.assessment_jobs import view as scoring_view
    scoring_task=await scoring_view(session,exam.active_scoring_run_id)
    breakdown = _skill_breakdown(exam.questions)
    states_map = await _states_map(session, ws, breakdown)
    return {
        "id": exam.id,
        "title": exam.title,
        "kind": exam.kind,
        "status": exam.status,
        "scoring_failed": ([q['order_no'] for q in scoring_task['questions'] if q['status'] in ('FAILED','RETRY_WAIT')] if scoring_task else summary.get("scoring_failed")) if degraded else None,
        "scoring_task":scoring_task,
        "score": None if degraded else exam.score,
        "total_points": exam.total_points,
        "gained_points": None if degraded else exam.gained_points,
        "selection_reason": exam.selection_reason,
        "summary": summary,
        "learning_tasks": summary.get("learning_tasks", []),
        "observations": summary.get("observations", []),
        "skill_breakdown": breakdown,
        "overall": None if degraded else _overall_diagnosis(breakdown, exam, states_map),
        "diagnosis_deep": deep,
        "has_deep": bool(deep),
        "completed_at": exam.completed_at.isoformat() if exam.completed_at else None,
        "questions": [
            {
                "order_no": eq.order_no,
                "exam_question_id":eq.id,
                "short_code": q.short_code,
                "title": q.title,
                "qtype": q.qtype,
                "options": q.options,
                "skill": q.skill.name if q.skill else None,
                "reference_answer": q.answer_html,
                "frozen": eq.frozen_snapshot,
                "answer": (
                    {
                        "choice_index": _submitted_answer(eq).get("choice_index"),
                        "text_answer": _submitted_answer(eq).get("text_answer"),
                        "points": eq.answer.points,
                        "max_points": eq.answer.max_points,
                        "verdict": eq.answer.verdict,
                        "judge_version": eq.answer.judge_version,
                        "feedback": eq.answer.feedback,
                        "evidence_refs": eq.answer.evidence_refs,
                    }
                    if eq.answer
                    else None
                ),
            }
            for eq in exam.questions
            for q in [_assessment_question(eq)]
        ],
    }
