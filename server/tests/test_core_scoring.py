"""Core-loop scoring regressions. Unit fixtures never connect to the personal DB."""
import asyncio
import datetime as dt
import time
import uuid
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import exams
from app.models import Evidence, Exam, ExamAnswer, ExamQuestion, LearningTask, Question, Skill, SkillState, Workspace
from app.services import assessment_jobs, assessment_worker
from app.services.competency import recompute_skill_states


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None


class QueueSession:
    def __init__(self, *rows):
        self.rows = iter(rows)
        self.added = []
        self.flush = AsyncMock()

    async def execute(self, _):
        return Result(next(self.rows))

    def add(self, value):
        self.added.append(value)


def evidence(n, canonical=1, score=0, kind="EXAM_ANSWER", assessed=True):
    return Evidence(
        id=n, workspace_id=1, skill_id=1, short_code=f"EV-{n}", kind=kind,
        grade="VERIFIED", support=0.6, title="Fixture assessment",
        occurred_at=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=n),
        created_at=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=n),
        meta={"assessed": assessed, "canonical_question_id": canonical, "question_id": canonical,
              "exam_id": n, "exam_question_id": n, "score": score, "max_points": 5,
              "judge_version": "judge-v1", "qtype": "open"},
    )


async def state_for(evs):
    session = QueueSession([Skill(id=1, key="rag", name="RAG")], [], evs, [])
    return (await recompute_skill_states(session, 1))[0]


@pytest.mark.asyncio
async def test_one_zero_score_is_visible_observation_but_domain_stays_unknown():
    state = await state_for([evidence(1)])
    assert state.evidence_coverage == 1
    assert state.estimated_level is None
    assert state.status == "UNKNOWN"
    assert state.last_assessed_at is not None


@pytest.mark.asyncio
async def test_repeated_canonical_question_does_not_inflate_coverage_or_confidence():
    first = await state_for([evidence(1, score=5)])
    repeated = await state_for([evidence(i, score=5) for i in range(1, 40)])
    assert repeated.evidence_coverage == first.evidence_coverage == 1
    assert repeated.confidence == first.confidence
    assert repeated.estimated_level is None


@pytest.mark.asyncio
async def test_same_question_after_canonical_merges_still_counts_as_one_observation():
    evs = []
    for n, canonical in enumerate((10, 20, 30), 1):
        ev = evidence(n, canonical=canonical, score=5)
        ev.meta = {**ev.meta, "question_id": 10}
        evs.append(ev)
    state = await state_for(evs)
    assert state.evidence_coverage == 1
    assert state.estimated_level is None
    assert state.status == "UNKNOWN"
    assert state.confidence == 0.15


@pytest.mark.asyncio
async def test_historical_canonical_links_merge_transitively_and_keep_latest_score():
    evs = []
    for n, (question, canonical, score) in enumerate(((10, 10, 5), (20, 10, 5), (20, 30, 0), (40, 40, 5), (50, 50, 5)), 1):
        ev = evidence(n, canonical=canonical, score=score)
        ev.meta = {**ev.meta, "question_id": question}
        evs.append(ev)
    state = await state_for(evs)
    assert state.evidence_coverage == 3
    assert state.estimated_level == pytest.approx(3.33, abs=0.01)
    assert state.confidence == 0.45
    # Deduplication is a derived view; historical provenance stays unchanged.
    assert [ev.meta["canonical_question_id"] for ev in evs] == [10, 10, 30, 40, 50]


@pytest.mark.asyncio
async def test_legacy_claim_import_project_are_not_assessed_mastery():
    evs = [evidence(1, kind="MEMORY_NOTE"), evidence(2, kind="IMPORTED_QA"),
           evidence(3, kind="PROJECT_COMMIT"), evidence(4, assessed=False)]
    state = await state_for(evs)
    assert state.evidence_coverage == 0
    assert state.estimated_level is None
    assert state.confidence == 0


@pytest.mark.asyncio
async def test_three_independent_questions_use_latest_performance_not_sum_of_attempts():
    evs = [evidence(1, 1, 5), evidence(2, 2, 5), evidence(3, 3, 5), evidence(4, 1, 0)]
    state = await state_for(evs)
    assert state.evidence_coverage == 3
    assert state.estimated_level == pytest.approx(3.33, abs=0.01)
    assert state.confidence <= 0.7
    assert state.dimension_scores is None  # no invented dimensions


def exam_fixture(qtype="mcq"):
    skill = Skill(id=1, key="rag", name="RAG")
    q = Question(id=1, short_code="Q-fixture", title="Edited live title", qtype=qtype,
                 topic="rag", skill_id=1, skill=skill,
                 rubric={"answer_index": 1, "max_points": 10, "points": [{"title": "changed", "keywords": ["x"]}]},
                 answer_html="changed answer")
    answer = ExamAnswer(id=1, exam_question_id=1, choice_index=1, text_answer="changed", max_points=10)
    eq = ExamQuestion(id=1, exam_id=1, question_id=1, order_no=1, question=q, answer=answer,
                      frozen_snapshot={"title": "Original question", "qtype": qtype, "options": [],
                          "rubric": {"answer_index": 0, "max_points": 5, "points": [{"title": "original", "keywords": ["original"]}]},
                          "answer_html": "original reference", "skill_key": "rag", "skill_name": "RAG", "skill_id": 1,
                          "submitted_answer": {"choice_index": 0, "text_answer": "original answer", "transcript": None}})
    return Exam(id=1, workspace_id=1, title="Fixture exam", kind="diagnosis", questions=[eq])


@pytest.mark.asyncio
async def test_frozen_question_rubric_and_answer_control_scoring(monkeypatch):
    monkeypatch.setattr(exams, "llm_adapter", NS(active=False, _effective=AsyncMock(return_value={})))
    exam = exam_fixture()
    assert await exams._score_questions(QueueSession(), None, exam) == []
    assert exam.questions[0].answer.points == 5
    assert exam.questions[0].answer.max_points == 5
    assert exam.score == 100


@pytest.mark.asyncio
async def test_open_question_fails_closed_without_model_and_warms_configuration(monkeypatch):
    adapter = NS(active=False, _effective=AsyncMock(return_value={}))
    monkeypatch.setattr(exams, "llm_adapter", adapter)
    monkeypatch.setattr(exams, "add_span", lambda *a, **k: None)
    exam = exam_fixture("open")
    assert await exams._score_questions(QueueSession(), None, exam) == [1]
    adapter._effective.assert_awaited()
    assert exam.score is None
    assert exam.questions[0].answer.points is None
    assert exam.questions[0].answer.verdict == "SCORING_FAILED"


def test_retest_request_accepts_scope_and_retry_key():
    req = exams.CreateExamReq(skill_key="rag", exclude_question_ids=[4], retest_task_id=8, idempotency_key="retry-1")
    assert getattr(req, "skill_key", None) == "rag"
    assert getattr(req, "exclude_question_ids", None) == [4]
    assert getattr(req, "retest_task_id", None) == 8
    assert getattr(req, "idempotency_key", None) == "retry-1"


@pytest.mark.asyncio
async def test_finalize_flushes_provenance_before_recompute_and_returns_observations(monkeypatch):
    exam = exam_fixture()
    a = exam.questions[0].answer
    a.points, a.max_points, a.verdict, a.judge_version = 0, 5, "WRONG", "rule-v1"
    a.feedback = {"missed_points": [{"title": "original"}]}
    session = QueueSession()
    async def recompute(s, workspace_id):
        assert s.flush.await_count > 0, "Pending evidence must be visible before recompute"
        evs = [e for e in s.added if isinstance(e, Evidence)]
        assert evs and evs[0].meta["assessed"] is True
        assert evs[0].meta["canonical_question_id"] == 1
        assert evs[0].meta["answer_excerpt"] == "original answer"
        return []
    monkeypatch.setattr(exams, "recompute_skill_states", recompute)
    monkeypatch.setattr(exams, "plan_tasks_for_workspace", AsyncMock(return_value=[]), raising=False)
    monkeypatch.setattr(exams, "plan_tasks_for_exam", AsyncMock(return_value=[]), raising=False)
    monkeypatch.setattr(exams, "complete_retest_for_exam", AsyncMock(), raising=False)
    monkeypatch.setattr(exams, "add_span", lambda *a, **k: None)
    monkeypatch.setattr(exams, "finish_trace", AsyncMock())
    monkeypatch.setattr(exams, "emit_event", AsyncMock())
    await exams._finalize_exam(session, Workspace(id=1), exam, None, time.perf_counter())
    assert len(exam.summary["observations"]) == 1
    assert exam.summary["learning_tasks"] == []


async def db_questions(session, ws):
    code = uuid.uuid4().hex[:10]
    skill = Skill(key=f"core-{code}", name="Core scoring fixture")
    session.add(skill)
    await session.flush()
    qs = [Question(short_code=f"Q-core-{code}-{n}", workspace_id=ws.id,
                   title=f"Fixture question {n}", qtype="mcq", topic="rag", skill_id=skill.id,
                   options=["right", "wrong"], rubric={"answer_index": 0, "max_points": 5,
                       "points": [{"title": "explain grounding", "keywords": ["grounding"]}]})
          for n in range(3)]
    session.add_all(qs)
    await session.flush()
    qs[1].canonical_question_id = qs[0].id
    await session.commit()
    return skill, qs


async def finish_queued_fixture(session, ws, ack, monkeypatch, tmp_path):
    """Exercise the durable Worker using only the deterministic fixture answers."""
    monkeypatch.setattr(assessment_worker, "OUTBOX_ROOT", tmp_path / "assessment-outbox")
    factory = async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False)
    async with factory() as worker_session:
        claim = await assessment_jobs.claim_step(worker_session, "core-fixture-worker")
    assert claim is not None and claim.run_id == ack["task_id"]
    assert claim.payload["qtype"] == "mcq"
    await assessment_worker.execute_claim(claim, "core-fixture-worker", factory)
    await assessment_worker.reconcile(factory)
    async with factory() as reporting_session:
        report = await exams._exam_report(reporting_session, ws, ack["exam_id"])
    assert report["status"] == "COMPLETED", report.get("scoring_task")
    return report


@pytest.mark.asyncio
async def test_create_retry_returns_one_exam(db_session, test_workspace):
    skill, _ = await db_questions(db_session, test_workspace)
    req = exams.CreateExamReq(question_count=1, skill_key=skill.key, idempotency_key="fixture-create")
    one = await exams.create_exam(req, test_workspace, db_session)
    two = await exams.create_exam(req, test_workspace, db_session)
    assert one["id"] == two["id"]


@pytest.mark.asyncio
async def test_selection_excludes_canonical_variants_and_other_skills(db_session, test_workspace):
    skill, qs = await db_questions(db_session, test_workspace)
    req = exams.CreateExamReq(kind="focused", question_count=1, skill_key=skill.key,
                              exclude_question_ids=[qs[0].id])
    result = await exams.create_exam(req, test_workspace, db_session)
    eqs = (await db_session.execute(select(ExamQuestion).where(ExamQuestion.exam_id == result["id"]))).scalars().all()
    assert [eq.question_id for eq in eqs] == [qs[2].id]


@pytest.mark.asyncio
async def test_submit_updates_observation_same_transaction_and_duplicate_is_idempotent(db_session, test_workspace, monkeypatch, tmp_path):
    skill, _ = await db_questions(db_session, test_workspace)
    monkeypatch.setattr(exams, "llm_adapter", NS(active=False, _effective=AsyncMock(return_value={})))
    detail = await exams.create_exam(exams.CreateExamReq(question_count=1, skill_key=skill.key), test_workspace, db_session)
    await exams.save_answer(detail["id"], exams.SaveAnswerReq(order_no=1, choice_index=1), test_workspace, db_session)
    ack = await exams.submit_exam(detail["id"], test_workspace, db_session)
    assert ack["status"] == "SCORING" and ack["exam_id"] == detail["id"]
    assert not (await db_session.scalars(select(Evidence).where(Evidence.workspace_id == test_workspace.id))).all()
    duplicate = await exams.submit_exam(detail["id"], test_workspace, db_session)
    assert duplicate["task_id"] == ack["task_id"]
    report = await finish_queued_fixture(db_session, test_workspace, ack, monkeypatch, tmp_path)
    state = (await db_session.execute(select(SkillState).where(SkillState.workspace_id == test_workspace.id, SkillState.skill_id == skill.id))).scalar_one()
    assert state.evidence_coverage == 1
    assert state.estimated_level is None
    assert report["summary"]["observations"]
    assert report["summary"]["learning_tasks"]
    again = await exams.submit_exam(detail["id"], test_workspace, db_session)
    assert again["id"] == report["id"]
    evs = (await db_session.execute(select(Evidence).where(Evidence.workspace_id == test_workspace.id))).scalars().all()
    assert len(evs) == 1


@pytest.mark.asyncio
async def test_profile_does_not_turn_unknown_observation_into_zero_level(monkeypatch):
    from app.api.v1 import profile as profile_api
    skill = NS(key="rag", name="RAG")
    state = NS(skill=skill, estimated_level=None, evidence_coverage=1, confidence=0.15,
               status="UNKNOWN", dimension_scores=None, last_assessed_at=None)
    monkeypatch.setattr(profile_api, "recompute_skill_states", AsyncMock(return_value=[state]))
    result = await profile_api.profile(Workspace(id=1), QueueSession([]))
    assert result["overall"]["level"] is None
    assert result["overall"]["evidence_total"] == 1


@pytest.mark.asyncio
async def test_report_uses_frozen_title_and_answer_after_question_changes(monkeypatch):
    exam = exam_fixture()
    exam.status, exam.score, exam.summary = "COMPLETED", 100, {}
    monkeypatch.setattr(exams, "_get_own_exam", AsyncMock(return_value=exam))
    monkeypatch.setattr(exams, "_states_map", AsyncMock(return_value={}))
    result = await exams._exam_report(QueueSession(), Workspace(id=1), 1)
    assert result["questions"][0]["title"] == "Original question"
    assert result["questions"][0]["answer"]["text_answer"] == "original answer"
    assert result["skill_breakdown"][0]["max"] == 5


def test_failed_question_breakdown_does_not_invent_zero_score():
    exam = exam_fixture()
    exam.questions[0].answer.verdict = "SCORING_FAILED"
    breakdown = exams._skill_breakdown(exam.questions)
    assert breakdown[0]["earned"] is None
    assert breakdown[0]["ratio"] is None


@pytest.mark.asyncio
async def test_judge_rejects_nonfinite_score_and_unmapped_missing_criterion():
    from app.services.llm_judge import judge_open_answer
    for payload in [
        {"points": float("nan"), "reasoning": "Fixture quoted answer", "hit": [], "missing": ["grounding"]},
        {"points": 0, "reasoning": "Fixture quoted answer", "hit": [], "missing": ["invented unrelated criterion"]},
    ]:
        adapter = NS(chat=AsyncMock(return_value={"content": payload}))
        result = await judge_open_answer(adapter, title="Fixture", skill_name="RAG",
                                        rubric={"max_points": 5, "points": [{"title": "grounding"}]}, text="my answer")
        assert result is None


@pytest.mark.asyncio
async def test_concurrent_create_and_submit_share_single_assessment(db_session, test_workspace, monkeypatch, tmp_path):
    skill, _ = await db_questions(db_session, test_workspace)
    monkeypatch.setattr(exams, "llm_adapter", NS(active=False, _effective=AsyncMock(return_value={})))
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    async def create():
        async with factory() as session:
            ws = await session.get(Workspace, test_workspace.id)
            return await exams.create_exam(exams.CreateExamReq(question_count=1, skill_key=skill.key,
                                                               idempotency_key="concurrent-fixture"), ws, session)
    one, two = await asyncio.gather(create(), create())
    assert one["id"] == two["id"]
    await exams.save_answer(one["id"], exams.SaveAnswerReq(order_no=1, choice_index=1), test_workspace, db_session)
    async def submit():
        async with factory() as session:
            ws = await session.get(Workspace, test_workspace.id)
            return await exams.submit_exam(one["id"], ws, session)
    ack1, ack2 = await asyncio.gather(submit(), submit())
    assert ack1["exam_id"] == ack2["exam_id"] == one["id"]
    assert ack1["task_id"] == ack2["task_id"]
    report = await finish_queued_fixture(db_session, test_workspace, ack1, monkeypatch, tmp_path)
    assert report["id"] == one["id"]
    async with factory() as session:
        assert await assessment_jobs.claim_step(session, "duplicate-fixture-worker") is None
    evs = (await db_session.execute(select(Evidence).where(Evidence.workspace_id == test_workspace.id))).scalars().all()
    assert len(evs) == 1


@pytest.mark.asyncio
async def test_exam_retest_link_must_reference_actual_retest_task(db_session, test_workspace):
    skill, _ = await db_questions(db_session, test_workspace)
    task = LearningTask(workspace_id=test_workspace.id, skill_id=skill.id,
                        title="Fixture completed lesson", kind="UNDERSTAND", status="DONE")
    db_session.add(task)
    await db_session.commit()
    with pytest.raises(HTTPException) as err:
        await exams.create_exam(exams.CreateExamReq(question_count=1, retest_task_id=task.id), test_workspace, db_session)
    assert err.value.status_code == 409


@pytest.mark.asyncio
async def test_same_skill_check_records_when_original_criterion_is_not_covered(db_session, test_workspace):
    skill, _ = await db_questions(db_session, test_workspace)
    task = LearningTask(workspace_id=test_workspace.id, skill_id=skill.id,
                        title="Fixture retest", kind="RETEST", status="PENDING",
                        source_criterion="unrelated criterion", content={"verification_mode": "immediate"})
    db_session.add(task)
    await db_session.commit()
    await exams.create_exam(exams.CreateExamReq(question_count=1, retest_task_id=task.id), test_workspace, db_session)
    assert task.content.get("source_criterion_covered") is False
    assert "同能力域" in task.content.get("assessment_scope", "")


@pytest.mark.asyncio
async def test_retest_does_not_count_unmapped_same_title_as_coverage(db_session, test_workspace):
    skill, qs = await db_questions(db_session, test_workspace)
    task = LearningTask(workspace_id=test_workspace.id, skill_id=skill.id,
                        title="Fixture comparable retest", kind="RETEST", status="PENDING",
                        source_criterion="explain grounding", content={"verification_mode": "immediate"})
    db_session.add(task)
    await db_session.commit()
    created = await exams.create_exam(exams.CreateExamReq(question_count=1, retest_task_id=task.id,
        exclude_question_ids=[qs[0].id, qs[1].id]), test_workspace, db_session)
    eqs = (await db_session.execute(select(ExamQuestion).where(ExamQuestion.exam_id == created["id"]))).scalars().all()
    assert eqs[0].question_id == qs[2].id
    assert task.content["source_criterion_covered"] is False


@pytest.mark.asyncio
async def test_detail_expiry_waits_for_scoring_and_cannot_overwrite_completed(db_session, test_workspace, monkeypatch, tmp_path):
    """Late reads remain prompt and cannot expire a frozen or completed submission."""
    skill, _ = await db_questions(db_session, test_workspace)
    monkeypatch.setattr(exams, "llm_adapter", NS(active=False, _effective=AsyncMock(return_value={})))
    detail = await exams.create_exam(exams.CreateExamReq(question_count=1, skill_key=skill.key,
                                                        duration_minutes=1), test_workspace, db_session)
    await exams.save_answer(detail["id"], exams.SaveAnswerReq(order_no=1, choice_index=0), test_workspace, db_session)
    original = (await db_session.execute(select(Exam).where(Exam.id == detail["id"]))).scalar_one()
    started = original.started_at
    await db_session.commit()

    class Clock:
        current = started + dt.timedelta(minutes=2)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(exams, "dt", NS(datetime=Clock, timezone=dt.timezone, timedelta=dt.timedelta, date=dt.date))
    ack = await exams.submit_exam(detail["id"], test_workspace, db_session)
    assert ack["status"] == "SCORING"
    scoring_started, finish_scoring = asyncio.Event(), asyncio.Event()
    original_evaluate = assessment_worker.evaluate

    async def held_score(*args, **kwargs):
        Clock.current = started + dt.timedelta(minutes=4)
        scoring_started.set()
        await finish_scoring.wait()
        return await original_evaluate(*args, **kwargs)

    monkeypatch.setattr(assessment_worker, "evaluate", held_score)
    monkeypatch.setattr(assessment_worker, "OUTBOX_ROOT", tmp_path / "assessment-outbox")
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    async with factory() as scoring_session:
        claim = await assessment_jobs.claim_step(scoring_session, "expiry-fixture-worker")
    assert claim is not None and claim.run_id == ack["task_id"]
    assert claim.payload["qtype"] == "mcq"
    scoring = asyncio.create_task(assessment_worker.execute_claim(claim, "expiry-fixture-worker", factory))
    try:
        await asyncio.wait_for(scoring_started.wait(), 5)
        async with factory() as reading_session:
            fetched = await asyncio.wait_for(exams._exam_detail(reading_session, test_workspace, detail["id"]), 2)
            assert fetched["status"] == "SCORING"
            assert fetched["questions"][0]["answer"]["choice_index"] == 0
    finally:
        finish_scoring.set()
        await scoring
    await assessment_worker.reconcile(factory)
    async with factory() as reading_session:
        fetched = await exams._exam_detail(reading_session, test_workspace, detail["id"])
        report = await exams._exam_report(reading_session, test_workspace, detail["id"])
        assert fetched["status"] == report["status"] == "COMPLETED"
        assert report["score"] == 100
    persisted = (await db_session.execute(select(Exam.status).where(Exam.id == detail["id"]))).scalar_one()
    assert persisted == "COMPLETED"


@pytest.mark.asyncio
async def test_fresh_profile_in_new_session_can_read_new_state_skill(db_session, test_workspace):
    from app.api.v1 import profile as profile_api
    skill, _ = await db_questions(db_session, test_workspace)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    async with factory() as fresh_session:
        ws = await fresh_session.get(Workspace, test_workspace.id)
        result = await profile_api.profile(ws, fresh_session)
        domain = next(d for d in result["domains"] if d["key"] == skill.key)
        assert domain["status"] == "UNKNOWN"
        assert domain["level"] is None
        assert domain["evidence"] == 0
