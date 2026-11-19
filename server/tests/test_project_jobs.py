"""Durable project jobs: the DB owns progress, not API/browser lifetime."""
import importlib.util
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Project, ProjectAnalysis


def queue():
    assert importlib.util.find_spec('app.services.project_jobs'), 'persistent stage queue is missing'
    from app.services import project_jobs
    return project_jobs


async def setup_run(session, ws, request_key='one', **kwargs):
    jobs = queue()
    project = Project(workspace_id=ws.id, name='Queue fixture', owner='fixture', repo='repo', branch='main',
                      repo_url='https://github.com/fixture/repo', meta={'kind':'github'})
    session.add(project)
    await session.commit()
    run = await jobs.enqueue(session, project, request_key=request_key, **kwargs)
    await jobs.ensure_steps(session, run, [dict(key='resolve', stage='resolve', requires=[], payload={})])
    await session.commit()
    return jobs, project, run


@pytest.mark.asyncio
async def test_enqueue_idempotency_and_separate_project_runs(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace)
    again = await jobs.enqueue(db_session, project, request_key='one')
    assert again.id == run.id
    other = Project(workspace_id=test_workspace.id, name='Other', owner='fixture', repo='other', branch='main', repo_url='https://github.com/fixture/other')
    db_session.add(other)
    await db_session.commit()
    independent = await jobs.enqueue(db_session, other, request_key='two')
    await db_session.commit()
    assert independent.id != run.id and independent.project_id != run.project_id


@pytest.mark.asyncio
async def test_expired_worker_resumes_only_unfinished_step_and_rejects_late_result(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace)
    started = datetime.now(timezone.utc)
    first = await jobs.claim_step(db_session, 'worker-a', lease_seconds=5, now=started)
    assert first and first.step_key == 'resolve'
    assert await jobs.complete_step(db_session, first, {'revision':'a'*40}, now=started+timedelta(seconds=1))
    await jobs.ensure_steps(db_session, run, [dict(key='module:one', stage='analyze_module', requires=['resolve'], payload={})])
    await db_session.commit()
    second = await jobs.claim_step(db_session, 'worker-a', lease_seconds=5, now=started+timedelta(seconds=2))
    assert second.step_key == 'module:one'
    stolen = await jobs.claim_step(db_session, 'worker-b', lease_seconds=5, now=started+timedelta(seconds=8))
    assert stolen.step_id == second.step_id and stolen.fencing_token > second.fencing_token
    assert not await jobs.complete_step(db_session, second, {'bad':'late'}, now=started+timedelta(seconds=9))
    assert await jobs.complete_step(db_session, stolen, {'good':'resumed'}, now=started+timedelta(seconds=9))
    from app.models.project_analysis import AnalysisStep
    rows = (await db_session.scalars(select(AnalysisStep).where(AnalysisStep.run_id==run.id).order_by(AnalysisStep.id))).all()
    assert rows[0].attempt_count == 1 and rows[0].output_json == {'revision':'a'*40}
    assert rows[1].attempt_count == 2 and rows[1].output_json == {'good':'resumed'}


@pytest.mark.asyncio
async def test_cancellation_fences_inflight_completion_and_future_claims(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace)
    claim = await jobs.claim_step(db_session, 'worker-a')
    await jobs.control_run(db_session, run.id, test_workspace.id, 'cancel')
    assert not await jobs.complete_step(db_session, claim, {'report':'must not publish'})
    assert await jobs.claim_step(db_session, 'worker-b') is None
    await db_session.refresh(run)
    assert run.status == 'CANCELLED' and run.report is None


@pytest.mark.asyncio
async def test_temporary_error_is_bounded_stage_retry_and_auth_blocks(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace)
    now = datetime.now(timezone.utc)
    for number in range(3):
        claim = await jobs.claim_step(db_session, 'worker-a', now=now+timedelta(minutes=number*10))
        assert claim
        assert await jobs.fail_step(db_session, claim, 'RATE_LIMITED', 'limited', retry_after=3, now=now+timedelta(minutes=number*10))
        if number < 2:
            assert await jobs.claim_step(db_session, 'worker-b', now=now+timedelta(minutes=number*10,seconds=1)) is None
    await db_session.refresh(run)
    assert run.status == 'BLOCKED'
    assert await jobs.claim_step(db_session, 'worker-a', now=now+timedelta(days=1)) is None
    await jobs.control_run(db_session, run.id, test_workspace.id, 'resume')
    claim = await jobs.claim_step(db_session, 'worker-a', now=now+timedelta(days=2))
    assert claim
    await jobs.fail_step(db_session, claim, 'AUTH_BLOCKED', 'credentials rejected', now=now+timedelta(days=2))
    await db_session.refresh(run)
    assert run.status == 'BLOCKED'


@pytest.mark.asyncio
async def test_dependencies_pause_and_default_global_concurrency(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace)
    await jobs.ensure_steps(db_session, run, [dict(key='later', stage='report', requires=['resolve'], payload={})])
    await db_session.commit()
    first = await jobs.claim_step(db_session, 'one')
    assert first.step_key == 'resolve'
    assert await jobs.claim_step(db_session, 'two') is None
    await jobs.control_run(db_session, run.id, test_workspace.id, 'pause')
    assert not await jobs.complete_step(db_session, first, {'discarded':True})
    assert await jobs.claim_step(db_session, 'two') is None
    await jobs.control_run(db_session, run.id, test_workspace.id, 'resume')
    resumed = await jobs.claim_step(db_session, 'two')
    assert resumed.step_key == 'resolve'


@pytest.mark.asyncio
async def test_budget_pause_does_not_erase_completed_stages(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace, budget={'max_model_calls':1})
    first = await jobs.claim_step(db_session, 'one')
    await jobs.complete_step(db_session, first, {'manifest':'saved'})
    await jobs.ensure_steps(db_session, run, [dict(key='model:a', stage='analyze_module', requires=['resolve'], payload={}, uses_model=True),
                                            dict(key='model:b', stage='analyze_module', requires=['resolve'], payload={}, uses_model=True)])
    await db_session.commit()
    second = await jobs.claim_step(db_session, 'one')
    await jobs.complete_step(db_session, second, {'module':'saved'}, usage={'total_tokens':10})
    assert await jobs.claim_step(db_session, 'one') is None
    await db_session.refresh(run)
    assert run.status == 'PAUSED_BUDGET'
    from app.models.project_analysis import AnalysisStep
    assert await db_session.scalar(select(AnalysisStep.status).where(AnalysisStep.run_id==run.id,AnalysisStep.step_key=='model:a')) == 'SUCCEEDED'


@pytest.mark.asyncio
async def test_lease_heartbeats_and_elapsed_task_time_are_independent(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace)
    start=datetime.now(timezone.utc)
    claim=await jobs.claim_step(db_session,'long-worker',lease_seconds=30,now=start)
    # Controlled virtual clock represents an hour-long stage/session lifecycle;
    # it is not a claim of a real one-hour provider response.
    for seconds in range(20,3620,20):
        assert await jobs.heartbeat(db_session,claim,lease_seconds=30,now=start+timedelta(seconds=seconds))
    assert await jobs.complete_step(db_session,claim,{'long_session':'checkpoint'},now=start+timedelta(seconds=3601))


@pytest.mark.asyncio
async def test_default_budget_allows_model_calls_beyond_old_limit(db_session, test_workspace):
    jobs, _, run = await setup_run(db_session, test_workspace)
    first = await jobs.claim_step(db_session, 'worker')
    await jobs.complete_step(db_session, first, {'manifest': 'saved'})
    run.usage = {'model_calls': 100}
    await jobs.ensure_steps(db_session, run, [dict(key='more', stage='analyze_module', requires=['resolve'])])
    await db_session.commit()
    claimed = await jobs.claim_step(db_session, 'worker')
    assert claimed.run_id == run.id and claimed.step_key == 'more'
    await db_session.refresh(run)
    assert run.usage['model_calls'] == 101
