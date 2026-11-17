"""Automatic retry limits distinguish content repair from transport failures."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import AnalysisStep
from app.services import project_jobs as jobs
from test_project_jobs import setup_run


async def model_step(session, workspace):
    _, project, run = await setup_run(session, workspace)
    at = datetime.now(timezone.utc)
    resolve = await jobs.claim_step(session, 'fixture', now=at)
    assert await jobs.complete_step(session, resolve, {}, now=at + timedelta(milliseconds=1))
    await jobs.ensure_steps(session, run, [{'key': 'analyze:fixture', 'stage': 'analyze_module',
        'requires': ['resolve'], 'payload': {}, 'uses_model': True}])
    await session.commit()
    step = await session.scalar(select(AnalysisStep).where(AnalysisStep.run_id == run.id,
        AnalysisStep.step_key == 'analyze:fixture'))
    return run, step, at + timedelta(seconds=1)


async def fail_next(session, step, at, category):
    claim = await jobs.claim_step(session, 'fixture', now=at)
    assert claim and claim.step_id == step.id
    assert await jobs.fail_step(session, claim, category, 'Controlled fixture failure',
        now=at + timedelta(milliseconds=1))
    await session.refresh(step)
    return step.next_retry_at + timedelta(milliseconds=1) if step.next_retry_at else at + timedelta(seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize('categories', [
    ['REQUEST_TIMEOUT', 'OUTPUT_TRUNCATED'],
    ['OUTPUT_TRUNCATED', 'REQUEST_TIMEOUT'],
])
async def test_transport_and_first_content_failure_allow_one_remaining_attempt(db_session, test_workspace, categories):
    run, step, at = await model_step(db_session, test_workspace)
    for category in categories:
        at = await fail_next(db_session, step, at, category)
    history = deepcopy(step.attempts)
    assert step.status == 'RETRY_WAIT' and step.retry_count == 2
    third = await jobs.claim_step(db_session, 'fixture', now=at)
    assert third and third.step_id == step.id
    assert await jobs.complete_step(db_session, third, {'fixture_result': True}, now=at + timedelta(milliseconds=1))
    await db_session.refresh(step)
    await db_session.refresh(run)
    assert step.status == 'SUCCEEDED' and step.attempt_count == 3
    assert step.attempts[:-1] == history and run.usage['model_calls'] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('categories', [
    ['INVALID_CITATION', 'INVALID_SCHEMA'],
    ['REQUEST_TIMEOUT', 'REQUEST_TIMEOUT', 'OUTPUT_TRUNCATED'],
    ['OUTPUT_TRUNCATED', 'REQUEST_TIMEOUT', 'NETWORK_TRANSIENT'],
    ['REQUEST_TIMEOUT', 'OUTPUT_TRUNCATED', 'INVALID_SCHEMA'],
])
async def test_content_limit_two_and_total_limit_three_remain_bounded(db_session, test_workspace, categories):
    run, step, at = await model_step(db_session, test_workspace)
    for category in categories:
        at = await fail_next(db_session, step, at, category)
    await db_session.refresh(run)
    assert step.status == run.status == 'BLOCKED'
    assert step.retry_count == step.attempt_count == len(categories)
    assert [record['outcome'] for record in step.attempts] == categories
    assert await jobs.claim_step(db_session, 'never-fourth', now=at + timedelta(hours=1)) is None
    assert run.usage['model_calls'] == len(categories)


@pytest.mark.asyncio
async def test_manual_resume_starts_new_finite_window_without_erasing_attempts(db_session, test_workspace):
    run, step, at = await model_step(db_session, test_workspace)
    for category in ('INVALID_CITATION', 'INVALID_SCHEMA'):
        at = await fail_next(db_session, step, at, category)
    assert step.status == 'BLOCKED'
    frozen = deepcopy(step.attempts)
    await jobs.control_run(db_session, run.id, test_workspace.id, 'resume')
    await db_session.refresh(step)
    assert step.retry_count == 0 and step.attempts == frozen
    at = await fail_next(db_session, step, at, 'OUTPUT_TRUNCATED')
    assert step.status == 'RETRY_WAIT' and step.retry_count == 1
    at = await fail_next(db_session, step, at, 'INVALID_CITATION')
    assert step.status == 'BLOCKED' and step.retry_count == 2
    assert step.attempts[:len(frozen)] == frozen and step.attempt_count == 4


@pytest.mark.asyncio
async def test_expired_worker_consumes_total_window_without_preventing_first_repair(db_session, test_workspace):
    run, step, at = await model_step(db_session, test_workspace)
    abandoned = await jobs.claim_step(db_session, 'lost', now=at, lease_seconds=5)
    assert abandoned and abandoned.step_id == step.id
    takeover = await jobs.claim_step(db_session, 'takeover', now=at + timedelta(seconds=6), lease_seconds=5)
    assert takeover and takeover.step_id == step.id
    assert await jobs.fail_step(db_session, takeover, 'OUTPUT_TRUNCATED', 'Controlled fixture truncation',
        now=at + timedelta(seconds=7))
    await db_session.refresh(step)
    assert step.retry_count == 2 and step.status == 'RETRY_WAIT'
    third_at = step.next_retry_at + timedelta(milliseconds=1)
    third = await jobs.claim_step(db_session, 'lost-again', now=third_at, lease_seconds=5)
    assert third and third.step_id == step.id
    assert await jobs.claim_step(db_session, 'never-fourth', now=third_at + timedelta(seconds=6)) is None
    await db_session.refresh(step)
    await db_session.refresh(run)
    assert step.status == run.status == 'BLOCKED' and step.retry_count == 3
    assert step.error_category == 'WORKER_LOST' and run.usage['model_calls'] == 3
    assert [a['outcome'] for a in step.attempts] == ['OUTCOME_UNKNOWN', 'OUTPUT_TRUNCATED', 'OUTCOME_UNKNOWN']


@pytest.mark.parametrize('action', ['pause', 'resume', 'cancel'])
def test_control_unknown_does_not_displace_an_earlier_content_failure(action):
    step = SimpleNamespace(retry_count=2, attempts=[
        {'outcome': 'INVALID_CITATION'}, {'outcome': 'OUTCOME_UNKNOWN', 'reason': action},
        {'outcome': 'INVALID_SCHEMA'}])
    frozen = deepcopy(step.__dict__)
    assert jobs._retry_allowed(step, 'INVALID_SCHEMA') is False
    assert step.__dict__ == frozen


@pytest.mark.parametrize('history', [[], None, [None], [{'outcome': 'OUTCOME_UNKNOWN'},
    {'outcome': 'OUTPUT_TRUNCATED'}], [{'outcome': ''}, {'outcome': 'OUTPUT_TRUNCATED'}]])
def test_missing_or_ambiguous_counted_history_does_not_grant_extra_retries(history):
    assert jobs._retry_allowed(SimpleNamespace(retry_count=2, attempts=history), 'OUTPUT_TRUNCATED') is False


def test_retry_window_stops_at_latest_success_and_ignores_old_resume_history():
    history = [{'outcome': 'INVALID_CITATION'}, {'outcome': 'INVALID_SCHEMA'},
               {'outcome': 'SUCCEEDED'}, {'outcome': 'OUTPUT_TRUNCATED'}]
    assert jobs._retry_allowed(SimpleNamespace(retry_count=2, attempts=history), 'OUTPUT_TRUNCATED') is True
    assert jobs._retry_allowed(SimpleNamespace(retry_count=1, attempts=history[:-2] + history[-1:]), 'OUTPUT_TRUNCATED') is True
