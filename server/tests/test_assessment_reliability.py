"""Independent assessment faults: real isolated PostgreSQL, explicit judge fixtures.

These tests never call the live model or touch the original exam 25.
"""
import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Exam, ExamAnswer, ExamQuestion, Question, ScoringRun, ScoringStep, Skill
from app.services import assessment_jobs as jobs
from app.services.assessment_rubric import JUDGE_VERSION, AssessmentJudgeError, validate_judgement
from app.services.assessment_versions import question_snapshot


async def prepared(session, workspace, monkeypatch, count=1):
    config = {'provider': 'fixture', 'model': 'assessment-fixture',
        'base_url': 'https://fixture.invalid', 'api_key': 'fixture-not-a-live-key'}

    async def fixture_config(*args, **kwargs):
        return dict(config), {}, {}

    monkeypatch.setattr(jobs, 'runtime_config', fixture_config)
    skill = Skill(key='fault-' + uuid4().hex[:12], name='Explicit assessment fault fixture')
    session.add(skill)
    await session.flush()
    exam = Exam(workspace_id=workspace.id, title='Explicit reliability fixture', kind='diagnosis',
        status='ACTIVE', questions=[], duration_minutes=30)
    session.add(exam)
    await session.flush()
    for number in range(1, count + 1):
        question = Question(short_code='AF-' + uuid4().hex[:12], workspace_id=workspace.id,
            title=f'Explain mechanism {number}', qtype='open', topic='rag', skill_id=skill.id, skill=skill,
            rubric={'max_points': 5, 'points': [{'title': 'Mechanism', 'detail': 'Explain the actual mechanism.'}]})
        session.add(question)
        await session.flush()
        eq = ExamQuestion(exam_id=exam.id, question_id=question.id, question=question,
            order_no=number, creation_snapshot=question_snapshot(question),
            answer=ExamAnswer(text_answer=f'Explicit original answer {number}', revision=0, max_points=5))
        exam.questions.append(eq)
    await session.flush()
    run = await jobs.enqueue(session, workspace, exam)
    await session.commit()
    return exam, run, config


def result_for(claim, points=None, criterion_score=3):
    rubric = claim.payload['rubric']
    text = claim.payload['submitted_answer']['text_answer']
    criteria = rubric['criteria']
    fields = {'question_id': claim.payload['question_id'], 'answer_revision_id': claim.payload['answer_revision_id'],
        'rubric_version': rubric['rubric_version'], 'judge_version': JUDGE_VERSION,
        'criterion_scores': [{'criterion_id': c['criterion_id'], 'score': criterion_score, 'max_score': 4,
            'answer_quotes': [text], 'satisfied_anchors': [a['anchor_id'] for a in c['score_anchors'] if a['score'] == criterion_score],
            'missing_points': ['Fixture missing detail'], 'reasoning_summary': 'Explicit fixture explanation',
            'why_not_higher': 'Fixture missing detail'} for c in criteria],
        'critical_errors': [], 'strengths': ['Fixture'], 'improvements': ['Fixture gap']}
    validated = validate_judgement(fields, rubric=rubric, text=text,
        question_id=claim.payload['question_id'], answer_revision_id=claim.payload['answer_revision_id'])
    return {**validated, 'points': validated['points'] if points is None else points, 'raw_response': json.dumps(fields),
        'usage': {'known': False}, 'request_meta': {'adapter': 'fixture-no-network'}}


@pytest.mark.asyncio
async def test_duplicate_enqueue_retains_one_frozen_run_and_step_set(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch, count=3)
    original = [(s.id, deepcopy(s.input_json), s.input_hash) for s in await db_session.scalars(
        select(ScoringStep).where(ScoringStep.run_id == run.id).order_by(ScoringStep.id))]
    again = await jobs.enqueue(db_session, test_workspace, exam)
    await db_session.commit()
    assert again.id == run.id
    assert await db_session.scalar(select(func.count()).select_from(ScoringRun).where(ScoringRun.exam_id == exam.id)) == 1
    current = [(s.id, s.input_json, s.input_hash) for s in await db_session.scalars(
        select(ScoringStep).where(ScoringStep.run_id == run.id).order_by(ScoringStep.id))]
    assert current == original and len(current) == 3


@pytest.mark.asyncio
async def test_retry_preserves_successful_answer_and_reclaims_only_failure(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch, count=2)
    first = await jobs.claim_step(db_session, 'first')
    assert await jobs.complete_step(db_session, first, result_for(first))
    saved = await db_session.scalar(select(ExamAnswer).where(ExamAnswer.exam_question_id == first.payload.get('exam_question_id', exam.questions[0].id)))
    saved_before = (saved.points, deepcopy(saved.feedback), saved.updated_at)
    second = await jobs.claim_step(db_session, 'second')
    assert second and second.step_id != first.step_id
    assert await jobs.fail_step(db_session, second, AssessmentJudgeError('AUTH_BLOCKED', 'Fixture authentication failure'))
    # Model the terminal reconciliation performed by the Worker; this queue test
    # verifies the subsequent manual retry, not the reconciliation implementation.
    run.status = 'FAILED'; exam.status = 'SCORING_FAILED'
    await db_session.commit()
    retried = await jobs.resume(db_session, test_workspace, exam)
    await db_session.commit()
    repeated = await jobs.resume(db_session, test_workspace, exam)
    await db_session.commit()
    assert repeated.id == retried.id == run.id
    claim = await jobs.claim_step(db_session, 'retry')
    assert claim.step_id == second.step_id
    assert await jobs.complete_step(db_session, claim, result_for(claim))
    await db_session.refresh(saved)
    assert (saved.points, saved.feedback, saved.updated_at) == saved_before
    first_step = await db_session.get(ScoringStep, first.step_id)
    assert first_step.attempt_count == 1 and first_step.status == 'SUCCEEDED'
    assert await jobs.claim_step(db_session, 'none-left') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('category,max_attempts,delay', [
    ('RATE_LIMITED', 3, 90), ('PROVIDER_5XX', 3, 0), ('NETWORK_TRANSIENT', 3, 0),
    ('REQUEST_TIMEOUT', 3, 0), ('AUTH_BLOCKED', 1, 0), ('INVALID_SCHEMA', 2, 0), ('INVALID_JSON', 2, 0),
])
async def test_error_retries_are_bounded_and_retry_after_is_obeyed(db_session, test_workspace, monkeypatch, category, max_attempts, delay):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    clock = [jobs.now()]
    monkeypatch.setattr(jobs, 'now', lambda: clock[0])
    for attempt in range(1, max_attempts + 1):
        claim = await jobs.claim_step(db_session, 'bounded', lease_seconds=20)
        assert claim is not None
        error = AssessmentJudgeError(category, 'Safe fixture failure', raw_response='PRIVATE_FAILURE_FIXTURE',
            usage={'known': False}, request_meta={'retry_after': delay})
        assert await jobs.fail_step(db_session, claim, error)
        step = await db_session.get(ScoringStep, claim.step_id)
        await db_session.refresh(step)
        assert step.attempt_count == attempt
        assert step.attempts[-1]['outcome'] == category
        assert 'PRIVATE_FAILURE_FIXTURE' not in json.dumps(await jobs.view(db_session, run.id))
        if attempt < max_attempts:
            assert step.status == 'RETRY_WAIT' and step.next_retry_at is not None
            assert step.next_retry_at >= clock[0] + timedelta(seconds=delay)
            assert await jobs.claim_step(db_session, 'too-early') is None
            clock[0] = step.next_retry_at + timedelta(milliseconds=1)
        else:
            assert step.status == 'FAILED' and step.next_retry_at is None
            assert await jobs.claim_step(db_session, 'beyond-bound') is None


@pytest.mark.asyncio
async def test_expired_lease_fences_late_result_and_keeps_original_input(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    clock = [jobs.now()]
    monkeypatch.setattr(jobs, 'now', lambda: clock[0])
    old = await jobs.claim_step(db_session, 'old-worker', lease_seconds=1)
    clock[0] += timedelta(seconds=2)
    fresh = await jobs.claim_step(db_session, 'fresh-worker', lease_seconds=10)
    assert fresh.step_id == old.step_id and fresh.fencing_token > old.fencing_token
    assert fresh.input_hash == old.input_hash and fresh.payload == old.payload
    assert await jobs.complete_step(db_session, old, result_for(old, 5)) is False
    assert await jobs.complete_step(db_session, fresh, result_for(fresh)) is True
    step = await db_session.get(ScoringStep, fresh.step_id)
    assert step.result_json['points'] == 3.75 and step.attempt_count == 2
    assert any(a['outcome'] == 'OUTCOME_UNKNOWN' for a in step.attempts)


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['workspace_id', 'exam_id', 'input_hash'])
async def test_claim_cannot_cross_scope_or_mismatch_frozen_input(db_session, test_workspace, monkeypatch, field):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    valid = await jobs.claim_step(db_session, 'scope-worker')
    wrong = '0' * 64 if field == 'input_hash' else getattr(valid, field) + 100000
    forged = replace(valid, **{field: wrong})
    assert await jobs.complete_step(db_session, forged, result_for(valid)) is False, (
        f'Claim {field} must match its durable run/input before accepting a result')
    assert await jobs.complete_step(db_session, valid, result_for(valid)) is True


@pytest.mark.asyncio
async def test_enqueue_rejects_another_workspace_even_for_existing_run(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    with pytest.raises((PermissionError, ValueError, HTTPException)):
        await jobs.enqueue(db_session, SimpleNamespace(id=test_workspace.id + 100000), exam)


@pytest.mark.asyncio
async def test_changed_model_cannot_mix_with_previous_success_on_retry(db_session, test_workspace, monkeypatch):
    exam, run, config = await prepared(db_session, test_workspace, monkeypatch, count=2)
    first = await jobs.claim_step(db_session, 'model-original')
    await jobs.complete_step(db_session, first, result_for(first))
    second = await jobs.claim_step(db_session, 'failure')
    await jobs.fail_step(db_session, second, AssessmentJudgeError('AUTH_BLOCKED', 'Fixture failure'))
    run.status = 'FAILED'; exam.status = 'SCORING_FAILED'
    await db_session.commit()
    config['model'] = 'different-fixture-model'
    with pytest.raises(ValueError):
        await jobs.resume(db_session, test_workspace, exam)
    await db_session.rollback()
    success = await db_session.get(ScoringStep, first.step_id)
    assert success.status == 'SUCCEEDED' and success.attempt_count == 1


@pytest.mark.asyncio
async def test_cancel_rejects_inflight_result_and_future_claim(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch, count=2)
    claim = await jobs.claim_step(db_session, 'cancelled-worker')
    await jobs.cancel(db_session, test_workspace, exam)
    await db_session.commit()
    assert await jobs.complete_step(db_session, claim, result_for(claim)) is False
    assert await jobs.claim_step(db_session, 'after-cancel') is None
    with pytest.raises(ValueError):
        await jobs.resume(db_session, test_workspace, exam)


@pytest.mark.asyncio
@pytest.mark.parametrize('first_queue', ['assessment', 'project'])
async def test_project_and_assessment_share_the_global_model_slot(db_session, test_workspace, monkeypatch, first_queue):
    from test_project_jobs import setup_run

    project_jobs, project, project_run = await setup_run(db_session, test_workspace, request_key=uuid4().hex)
    resolve = await project_jobs.claim_step(db_session, 'prepare-source')
    assert resolve is not None
    await project_jobs.complete_step(db_session, resolve, {'fixture': 'source metadata only'})
    await project_jobs.ensure_steps(db_session, project_run, [{'key': 'model-fixture', 'stage': 'analyze_module',
        'requires': ['resolve'], 'uses_model': True, 'payload': {}}])
    await db_session.commit()
    await prepared(db_session, test_workspace, monkeypatch)
    first, second = (jobs, project_jobs) if first_queue == 'assessment' else (project_jobs, jobs)
    active = await first.claim_step(db_session, 'global-model-owner')
    assert active is not None
    assert await second.claim_step(db_session, 'other-worker') is None, (
        'Two Worker processes must not each claim a model slot from a different queue')


@pytest.mark.asyncio
async def test_model_result_survives_database_publication_failure_without_second_model_call(db_session, test_workspace, monkeypatch, tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services import assessment_worker as worker

    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, 'runtime_config', jobs.runtime_config)
    monkeypatch.setattr(worker, 'OUTBOX_ROOT', tmp_path / 'private-outbox')
    calls = []

    async def evaluate(claim, config, repair=None):
        calls.append(claim.step_id)
        return result_for(claim)

    original_complete = jobs.complete_step

    async def unavailable_database(*args, **kwargs):
        raise RuntimeError('Explicit database publication outage after validated fixture response')

    monkeypatch.setattr(worker, 'evaluate', evaluate)
    monkeypatch.setattr(jobs, 'complete_step', unavailable_database)
    old = await jobs.claim_step(db_session, 'publication-old')
    await worker.execute_claim(old, old.owner, factory, lease_seconds=10, heartbeat_seconds=.1)
    assert calls == [old.step_id]
    files = list((tmp_path / 'private-outbox').rglob('*.json'))
    assert len(files) == 1, 'Validated response must be durable before attempting the database write'
    step = await db_session.get(ScoringStep, old.step_id)
    await db_session.refresh(step)
    assert step.status == 'RUNNING' and step.result_json is None
    step.lease_expires_at = jobs.now() - timedelta(seconds=1)
    await db_session.commit()
    monkeypatch.setattr(jobs, 'complete_step', original_complete)
    fresh = await jobs.claim_step(db_session, 'publication-recovery')
    await worker.execute_claim(fresh, fresh.owner, factory, lease_seconds=10, heartbeat_seconds=.1)
    await db_session.refresh(step)
    assert step.status == 'SUCCEEDED'
    assert calls == [old.step_id], 'Takeover must validate the outbox instead of paying for a second model call'
    assert step.attempts[-1]['request_meta']['recovered_from_local_outbox'] is True
    assert step.attempts[-1]['request_meta']['request_started'] is False


@pytest.mark.asyncio
async def test_failed_finalize_rolls_back_all_side_effects_and_retries_once(db_session, test_workspace, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.v1 import exams
    from app.models import CompetencySnapshot, Evidence, LearningTask, SkillState
    from app.services import assessment_worker as worker

    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch, count=2)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    for number in range(2):
        claim = await jobs.claim_step(db_session, f'complete-{number}')
        assert await jobs.complete_step(db_session, claim, result_for(claim, criterion_score=2))
    original_report = exams._exam_report

    async def fail_report(*args, **kwargs):
        raise RuntimeError('Controlled failure after evidence, state and snapshot flush')

    monkeypatch.setattr(exams, '_exam_report', fail_report)
    await worker.reconcile(factory)
    await db_session.refresh(run); await db_session.refresh(exam)
    assert run.status == 'RETRY_WAIT' and exam.status == 'SCORING_RETRY_WAIT'
    publication = run.meta['publication']
    assert publication['attempt_count'] == 1 and publication['next_retry_at']
    for model in (Evidence, SkillState, CompetencySnapshot, LearningTask):
        assert await db_session.scalar(select(func.count()).select_from(model).where(model.workspace_id == test_workspace.id)) == 0
    await db_session.refresh(run)
    assert run.status != 'COMPLETED'
    steps = list(await db_session.scalars(select(ScoringStep).where(ScoringStep.run_id == run.id)))
    assert all(s.status == 'SUCCEEDED' and s.attempt_count == 1 for s in steps)
    await db_session.commit()
    monkeypatch.setattr(exams, '_exam_report', original_report)
    await worker.reconcile(factory)
    await db_session.refresh(run)
    assert run.status == 'RETRY_WAIT', 'Publication must respect the durable retry deadline'
    await db_session.commit()
    retry_at = datetime.fromisoformat(publication['next_retry_at']) + timedelta(milliseconds=1)
    monkeypatch.setattr(jobs, 'now', lambda: retry_at)
    await worker.reconcile(factory)
    await worker.reconcile(factory)
    await db_session.refresh(run); await db_session.refresh(exam)
    assert run.status == exam.status == 'COMPLETED'
    assert await db_session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == test_workspace.id)) == 2
    assert await db_session.scalar(select(func.count()).select_from(CompetencySnapshot).where(CompetencySnapshot.workspace_id == test_workspace.id)) == 1
    states = list(await db_session.scalars(select(SkillState).where(SkillState.workspace_id == test_workspace.id)))
    assert sum(s.evidence_coverage for s in states) == 2, 'New validated judge results must participate once in observations'
    assert all(s.status == 'UNKNOWN' for s in states), 'Two fixture questions do not establish domain mastery'
    assert all(s.attempt_count == 1 for s in steps), 'Final report failure must never replay successful model work'
    tasks = list(await db_session.scalars(select(LearningTask).where(LearningTask.source_exam_id == exam.id)))
    assert len(tasks) == 2, 'Each observed v2 criterion gap must reach the existing learning planner'
    assert all(t.source_criterion == 'Mechanism' and t.source_evidence_id and t.source_exam_question_id for t in tasks)
