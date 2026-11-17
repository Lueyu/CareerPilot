"""Real PostgreSQL lock/retry regressions; only explicit model-result fixtures."""
import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Exam, ScoringRun, ScoringStep, Workspace, Evidence, CompetencySnapshot
from app.services import assessment_jobs as jobs, assessment_worker as worker
from app.services.llm_judge import JUDGE_PROMPT_VERSION
from test_assessment_reliability import prepared, result_for


@pytest.mark.asyncio
async def test_claim_does_not_hold_a_step_while_api_holds_its_parent_locks(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    async with factory() as api_session, factory() as claimant:
        await api_session.execute(select(Workspace.id).where(Workspace.id == test_workspace.id).with_for_update())
        await api_session.execute(select(Exam.id).where(Exam.id == exam.id).with_for_update())
        await api_session.execute(select(ScoringRun.id).where(ScoringRun.id == run.id).with_for_update())
        pid = await claimant.scalar(text('SELECT pg_backend_pid()'))
        task = asyncio.create_task(jobs.claim_step(claimant, 'concurrent-claim'))
        try:
            async def settled():
                while not task.done():
                    blocked = await db_session.scalar(text('SELECT cardinality(pg_blocking_pids(:pid)) > 0'), {'pid': pid})
                    await db_session.commit()
                    if blocked: return
                    await asyncio.sleep(.01)
            await asyncio.wait_for(settled(), 2)
            # API cancellation must still be able to lock its child after its
            # parent. The former claim path locked Step before waiting on Run.
            await api_session.execute(select(ScoringStep.id).where(ScoringStep.run_id == run.id).with_for_update(nowait=True))
            assert await asyncio.wait_for(task, 1) is None
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError): await task
            await api_session.rollback()
    claim = await jobs.claim_step(db_session, 'after-api')
    assert claim is not None


@pytest.mark.asyncio
async def test_enqueue_metadata_names_actual_prompt_and_two_distinct_version_scopes(db_session, test_workspace, monkeypatch):
    _, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    assert run.meta['prompt_version'] == JUDGE_PROMPT_VERSION
    semantics = run.meta['version_semantics']
    assert 'question_version' in semantics and 'rubric.question_version' in semantics


@pytest.mark.asyncio
async def test_publication_failures_are_bounded_and_manual_resume_never_rescores(db_session, test_workspace, monkeypatch):
    from app.api.v1 import exams
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    claim = await jobs.claim_step(db_session, 'finish-scoring')
    await jobs.complete_step(db_session, claim, result_for(claim, criterion_score=2))
    original_report = exams._exam_report
    failed_calls = []
    async def fail_report(*args, **kwargs):
        failed_calls.append(1)
        raise RuntimeError('PRIVATE controlled SQL/body detail must not become public')
    async def forbidden_model(*args, **kwargs):
        raise AssertionError('Successful questions must never be scored again during report publication')
    monkeypatch.setattr(exams, '_exam_report', fail_report)
    monkeypatch.setattr(worker, 'evaluate', forbidden_model)
    clock = [jobs.now()]
    monkeypatch.setattr(jobs, 'now', lambda: clock[0])
    for attempt in range(1, 4):
        await worker.reconcile(factory)
        await db_session.refresh(run); await db_session.refresh(exam)
        publication = run.meta['publication']
        assert publication['attempt_count'] == attempt
        assert run.error_category == 'PUBLICATION_FAILED' and 'PRIVATE' not in run.error
        assert exam.score is None and exam.gained_points is None
        for model in (Evidence, CompetencySnapshot):
            assert await db_session.scalar(select(func.count()).select_from(model).where(model.workspace_id == test_workspace.id)) == 0
        await db_session.commit()
        await worker.reconcile(factory)
        assert len(failed_calls) == attempt, 'Backoff or terminal state must prevent an immediate new publication'
        if attempt < 3:
            assert run.status == 'RETRY_WAIT'
            clock[0] = datetime.fromisoformat(publication['next_retry_at']) + timedelta(milliseconds=1)
        else:
            assert run.status == 'FAILED' and exam.status == 'SCORING_FAILED'
    step = await db_session.get(ScoringStep, claim.step_id)
    assert step.status == 'SUCCEEDED' and step.attempt_count == 1
    await jobs.resume(db_session, test_workspace, exam)
    await db_session.commit()
    monkeypatch.setattr(exams, '_exam_report', original_report)
    await worker.reconcile(factory)
    await worker.reconcile(factory)
    await db_session.refresh(run)
    assert run.status == 'COMPLETED'
    assert await db_session.scalar(select(func.count()).select_from(CompetencySnapshot).where(CompetencySnapshot.workspace_id == test_workspace.id)) == 1
    await db_session.refresh(step)
    assert step.attempt_count == 1


@pytest.mark.asyncio
async def test_concurrent_reconcile_publishes_one_report_and_snapshot(db_session, test_workspace, monkeypatch):
    _, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    claim = await jobs.claim_step(db_session, 'complete')
    await jobs.complete_step(db_session, claim, result_for(claim))
    await asyncio.wait_for(asyncio.gather(worker.reconcile(factory), worker.reconcile(factory)), 10)
    await db_session.refresh(run)
    assert run.status == 'COMPLETED'
    assert await db_session.scalar(select(func.count()).select_from(CompetencySnapshot).where(CompetencySnapshot.workspace_id == test_workspace.id)) == 1


@pytest.mark.asyncio
async def test_nonofficial_run_cannot_publish_or_mutate_official_exam(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    claim = await jobs.claim_step(db_session, 'finish-shadow')
    await jobs.complete_step(db_session, claim, result_for(claim))
    run.mode = 'SHADOW'
    await db_session.commit()
    before = (exam.status, exam.score, exam.gained_points)
    await worker.reconcile(factory)
    await db_session.refresh(run); await db_session.refresh(exam)
    assert run.status == 'NEEDS_REVIEW' and run.error_category == 'NON_OFFICIAL_PUBLICATION'
    assert run.report is None and (exam.status, exam.score, exam.gained_points) == before
    for model in (Evidence, CompetencySnapshot):
        assert await db_session.scalar(select(func.count()).select_from(model).where(model.workspace_id == test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_dispatch_rechecks_a_lease_renewed_after_its_initial_scan(db_session, test_workspace, monkeypatch):
    _, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    old = await jobs.claim_step(db_session, 'still-working', lease_seconds=1)
    at = jobs.now() + timedelta(seconds=2)
    original_lock = jobs.lock_run
    async def renewed(session, run_id, **kwargs):
        result = await original_lock(session, run_id, **kwargs)
        step = await session.get(ScoringStep, old.step_id)
        step.lease_expires_at = at + timedelta(seconds=45)
        await session.flush()
        return result
    monkeypatch.setattr(jobs, 'lock_run', renewed)
    assert await jobs.claim_step(db_session, 'must-not-steal', at=at) is None
    step = await db_session.get(ScoringStep, old.step_id)
    await db_session.refresh(step)
    assert step.attempt_count == 1 and step.lease_owner == 'still-working'


@pytest.mark.asyncio
async def test_crashes_after_publication_reservation_obey_the_same_attempt_bound(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    claim = await jobs.claim_step(db_session, 'ready')
    await jobs.complete_step(db_session, claim, result_for(claim))
    clock = [jobs.now()]
    monkeypatch.setattr(jobs, 'now', lambda: clock[0])
    calls = []
    async def crash(*args, **kwargs):
        calls.append(1)
        raise asyncio.CancelledError()
    monkeypatch.setattr(worker, '_publish', crash)
    for attempt in range(1, 4):
        with pytest.raises(asyncio.CancelledError): await worker.reconcile(factory)
        await db_session.refresh(run)
        publication = run.meta['publication']
        assert publication['attempt_count'] == attempt
        await db_session.commit()
        await worker.reconcile(factory)
        assert len(calls) == attempt
        clock[0] = datetime.fromisoformat(publication['lease_expires_at']) + timedelta(milliseconds=1)
    await worker.reconcile(factory)
    await db_session.refresh(run); await db_session.refresh(exam)
    assert len(calls) == 3 and run.status == 'FAILED' and exam.status == 'SCORING_FAILED'
    assert all(a['outcome'] == 'OUTCOME_UNKNOWN' for a in run.meta['publication']['attempts'])
    assert await db_session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_cancel_after_publication_reservation_fences_all_side_effects(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    claim = await jobs.claim_step(db_session, 'ready')
    await jobs.complete_step(db_session, claim, result_for(claim))
    original_publish = worker._publish
    async def cancel_before_publish(*args):
        async with factory() as session:
            current_exam = await session.get(Exam, exam.id)
            workspace = await session.get(Workspace, test_workspace.id)
            await jobs.cancel(session, workspace, current_exam)
            await session.commit()
        await original_publish(*args)
    monkeypatch.setattr(worker, '_publish', cancel_before_publish)
    await asyncio.wait_for(worker.reconcile(factory), 10)
    await db_session.refresh(run); await db_session.refresh(exam)
    assert run.status == 'CANCELLED' and exam.status == 'ABORTED'
    assert run.report is None
    for model in (Evidence, CompetencySnapshot):
        assert await db_session.scalar(select(func.count()).select_from(model).where(model.workspace_id == test_workspace.id)) == 0
