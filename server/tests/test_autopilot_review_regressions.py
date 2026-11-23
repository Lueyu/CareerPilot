"""Independent review regressions; explicit fixtures, never model acceptance."""
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_project_candidates import prepared, proposal
from test_project_jobs import setup_run

from app.models import MemoryItem, ProjectAnalysis
from app.services.project_memory import active_project_memories, issue_targets


@pytest.mark.asyncio
async def test_unchanged_pending_fact_is_still_reviewable_after_new_report(db_session, test_workspace):
    service, project, first = await prepared(db_session, test_workspace)
    await service.publish_candidates(db_session, first, [proposal()])
    await db_session.commit()
    newer = ProjectAnalysis(project_id=project.id, workspace_id=test_workspace.id,
        request_key='newer-same-evidence', pipeline_version='autopilot-v1', status='COMPLETED',
        report_status='VALID', coverage_status='COMPLETE', source_revision='b' * 40,
        report=deepcopy(first.report), snapshot=deepcopy(first.snapshot))
    db_session.add(newer)
    await db_session.flush()
    project.latest_valid_analysis_id = newer.id
    await service.publish_candidates(db_session, newer, [proposal()])
    await db_session.commit()
    pending = list(await db_session.scalars(select(MemoryItem).where(
        MemoryItem.workspace_id == test_workspace.id, MemoryItem.status == 'PENDING_REVIEW')))
    assert pending, 'A newer identical report must leave a reviewable candidate'
    reviewed = await service.review_candidates(db_session, test_workspace.id, [
        {'candidate_id': row.id, 'expected_revision': row.revision, 'action': 'confirm'} for row in pending])
    assert any(row['status'] == 'CONFIRMED' for row in reviewed), 'Unchanged pending facts became permanently conflicting'
    assert await active_project_memories(db_session, test_workspace.id, project.id)


@pytest.mark.asyncio
async def test_confirmed_completed_report_issue_can_feed_existing_question_generation(db_session, test_workspace):
    service, project, run = await prepared(db_session, test_workspace)
    rows = await service.publish_candidates(db_session, run, [proposal()])
    await db_session.commit()
    reviewed = await service.review_candidates(db_session, test_workspace.id,
        [{'candidate_id': rows[0].id, 'expected_revision': 1, 'action': 'confirm'}])
    assert reviewed[0]['status'] == 'CONFIRMED'
    targets = await issue_targets(db_session, test_workspace.id, [project.id], run.id, ['i1'])
    assert len(targets) == 1 and targets[0]['memory_id'] == rows[0].id


@pytest.mark.asyncio
async def test_real_candidate_model_stage_respects_call_budget(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace, budget={'max_model_calls': 0})
    resolve = await jobs.claim_step(db_session, 'fixture-budget-review')
    assert resolve and resolve.stage == 'resolve'
    await jobs.complete_step(db_session, resolve, {'revision': 'fixture'})
    await jobs.ensure_steps(db_session, run, [{'key': 'candidate-part:0', 'stage': 'extract_candidates',
        'requires': ['resolve'], 'payload': {'claim_ids': ['fixture']}}])
    await db_session.commit()
    assert await jobs.claim_step(db_session, 'fixture-budget-review') is None, 'Candidate model calls bypassed the explicit zero-call budget'
    await db_session.refresh(run)
    assert run.status == 'PAUSED_BUDGET'


@pytest.mark.asyncio
async def test_editing_invalidated_memory_does_not_revalidate_old_source(db_session, test_workspace):
    from app.api.v1.memory import CorrectReq, correct_memory

    service, project, run = await prepared(db_session, test_workspace)
    rows = await service.publish_candidates(db_session, run, [proposal()])
    await db_session.commit()
    await service.review_candidates(db_session, test_workspace.id,
        [{'candidate_id': rows[0].id, 'expected_revision': 1, 'action': 'confirm'}])
    await service.invalidate_changed_sources(db_session, project, {'app.py'}, 'b' * 40)
    await db_session.commit()
    try:
        result = await correct_memory(rows[0].id, CorrectReq(content='User wording correction'), ws=test_workspace, session=db_session)
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        corrected = await db_session.get(MemoryItem, result['id'])
        assert corrected.freshness == 'NEEDS_REVIEW', 'Text editing must not certify invalidated repository evidence'
    assert not await active_project_memories(db_session, test_workspace.id, project.id)


@pytest.mark.asyncio
async def test_old_pending_candidate_cannot_become_current_after_source_changed(db_session, test_workspace):
    service, project, run = await prepared(db_session, test_workspace)
    rows = await service.publish_candidates(db_session, run, [proposal()])
    await db_session.commit()
    expected_revision = rows[0].revision
    # A newer manifest is known, while the previous report is still the latest
    # valid one (the new model analysis can subsequently fail).
    await service.invalidate_changed_sources(db_session, project, {'app.py'}, 'b' * 40)
    await db_session.commit()
    await service.review_candidates(db_session, test_workspace.id,
        [{'candidate_id': rows[0].id, 'expected_revision': expected_revision, 'action': 'confirm'}])
    assert not await active_project_memories(db_session, test_workspace.id, project.id), (
        'Confirming an old pending candidate must not restore known-invalidated source facts')


@pytest.mark.asyncio
async def test_repeated_expired_source_leases_eventually_require_user_recovery(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace)
    start = jobs.utcnow()
    stopped = False
    for attempt in range(12):
        claim = await jobs.claim_step(db_session, f'dead-worker-{attempt}', lease_seconds=1,
            now=start + timedelta(seconds=attempt * 2))
        if claim is None:
            stopped = True
            break
        assert claim.stage == 'resolve'
    assert stopped, 'A non-model source stage with repeatedly unknown outcomes must not auto-retry forever'
    await db_session.refresh(run)
    assert run.status in ('BLOCKED', 'PAUSED_BUDGET', 'FAILED_FINAL')


@pytest.mark.asyncio
async def test_new_manual_full_after_cancel_does_not_leave_an_extra_pending_full(db_session, test_workspace, monkeypatch):
    from app.services import project_autopilot_engine as engine
    from app.services import project_scheduler as scheduler
    from app.services.project_autopilot_config import model_fingerprint

    jobs, project, old = await setup_run(db_session, test_workspace)
    await jobs.enqueue(db_session, project, 'old-pending-manual-full', mode='full')
    await db_session.commit()
    assert project.pending_update and project.pending_update['mode'] == 'full'
    await jobs.control_run(db_session, old.id, test_workspace.id, 'cancel')
    config = {'provider': 'fixture', 'model': 'fixture', 'base_url': 'https://fixture.invalid', 'api_key': 'fixture'}
    pipeline, prompt = scheduler.versions()
    newer = await jobs.enqueue(db_session, project, 'new-manual-full', mode='full',
        pipeline_version=pipeline, prompt_bundle_version=prompt, model_config_fingerprint=model_fingerprint(config))
    newer.status = 'COMPLETED'
    newer.report_status = 'VALID'
    newer.coverage_status = 'COMPLETE'
    newer.source_revision = 'a' * 40
    newer.snapshot = {'provenance': engine._provenance({'workspace_id': test_workspace.id, 'project_id': project.id})}
    project.latest_valid_analysis_id = newer.id
    project.last_seen_sha = newer.source_revision
    await db_session.commit()

    async def fixture_config(*args, **kwargs):
        return config, {}, {'budget': {'max_model_calls': 32}, 'check_interval_hours': 6}

    async def fixture_head(*args, **kwargs):
        return {'target_sha': 'a' * 40, 'branch': 'main'}

    monkeypatch.setattr(scheduler, 'runtime_config', fixture_config)
    monkeypatch.setattr(scheduler, 'probe_repository', fixture_head)
    checked = await scheduler.check_project(db_session, project.id, test_workspace.id)
    assert checked['status'] == 'UNCHANGED', 'A completed replacement full run must consume the earlier pending full intent'
    count = await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(ProjectAnalysis.project_id == project.id))
    assert count == 2


@pytest.mark.asyncio
async def test_unchanged_confirmed_issue_remains_usable_in_new_report_without_rewriting_history(db_session, test_workspace):
    service, project, first = await prepared(db_session, test_workspace)
    rows = await service.publish_candidates(db_session, first, [proposal()])
    await db_session.commit()
    await service.review_candidates(db_session, test_workspace.id, [{'candidate_id': rows[0].id,
        'expected_revision': 1, 'action': 'confirm', 'edited_content': 'User maintained confirmed explanation'}])
    newer_report = deepcopy(first.report)
    newer_report['issues'][0]['id'] = 'i2'
    newer = ProjectAnalysis(project_id=project.id, workspace_id=test_workspace.id,
        request_key='newer-unchanged-confirmed', pipeline_version='autopilot-v1', status='COMPLETED',
        report_status='VALID', coverage_status='COMPLETE', source_revision='b' * 40,
        report=newer_report, snapshot=deepcopy(first.snapshot))
    db_session.add(newer)
    await db_session.flush()
    project.latest_valid_analysis_id = newer.id
    await service.invalidate_changed_sources(db_session, project, {'unrelated.py'}, newer.source_revision)
    created = await service.publish_candidates(db_session, newer, [{**proposal(), 'issue_id': 'i2'}])
    await db_session.commit()
    assert not created, 'Unchanged confirmed evidence should not require redundant approval'
    targets = await issue_targets(db_session, test_workspace.id, [project.id], newer.id, ['i2'])
    assert len(targets) == 1 and targets[0]['memory_id'] == rows[0].id
    assert targets[0]['content'] == 'User maintained confirmed explanation'
    await db_session.refresh(rows[0])
    assert rows[0].meta['analysis_id'] == first.id and rows[0].meta['source_revision'] == first.source_revision


@pytest.mark.asyncio
async def test_claim_cancelled_before_execution_cannot_start_a_new_external_stage(db_session, test_workspace, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app import project_worker as worker

    jobs, project, run = await setup_run(db_session, test_workspace,
        pipeline_version=worker.analysis.PIPELINE_VERSION, prompt_bundle_version=worker.analysis.PROMPT_BUNDLE_VERSION)
    claim = await jobs.claim_step(db_session, 'cancel-before-execute')
    await jobs.control_run(db_session, run.id, test_workspace.id, 'cancel')
    calls = []

    async def fixture_config(*args, **kwargs):
        return {}, {}, {}

    async def stage_must_not_start(*args, **kwargs):
        calls.append('unexpected external stage')
        return {'artifact': {'fixture': True}}

    monkeypatch.setattr(worker, 'runtime_config', fixture_config)
    monkeypatch.setattr(worker.analysis, 'execute_stage', stage_must_not_start)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    await worker.execute_claim(claim, 'cancel-before-execute', factory=factory, lease_seconds=3, heartbeat_seconds=.2)
    assert calls == [], 'A cancelled queued claim must not start a new source/model request'


@pytest.mark.asyncio
async def test_claim_loop_does_not_resurrect_blocked_run_with_another_ready_step(db_session, test_workspace):
    jobs, project, run = await setup_run(db_session, test_workspace)
    await jobs.ensure_steps(db_session, run, [{'key': 'independent-ready', 'stage': 'read_source',
        'requires': [], 'payload': {'path': 'fixture.py'}}])
    await db_session.commit()
    start = jobs.utcnow()
    for attempt in range(3):
        claim = await jobs.claim_step(db_session, f'lost-{attempt}', lease_seconds=1,
            now=start + timedelta(seconds=attempt * 2))
        assert claim and claim.step_key == 'resolve'
    next_claim = await jobs.claim_step(db_session, 'after-limit', lease_seconds=1, now=start + timedelta(seconds=6))
    assert next_claim is None, 'Another ready step must not revive a run blocked earlier in this claim loop'
    await db_session.refresh(run)
    assert run.status == 'BLOCKED'
