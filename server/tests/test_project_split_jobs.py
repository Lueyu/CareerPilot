"""Durable source splitting preserves failed parents as immutable history."""
from copy import deepcopy
from datetime import timedelta
import hashlib

import pytest
from sqlalchemy import select

from app.models import AnalysisStep
from app.services import project_jobs as jobs
from test_project_jobs import setup_run


def row_value(row):
    return {column.name: deepcopy(getattr(row, column.name)) for column in row.__table__.columns}


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['pause', 'resume', 'cancel'])
async def test_control_does_not_reactivate_or_rewrite_superseded_parent(db_session, test_workspace, action):
    _, project, run = await setup_run(db_session, test_workspace)
    parent = AnalysisStep(run_id=run.id, workspace_id=test_workspace.id, project_id=project.id,
        step_key='analyze:failed-parent', stage='analyze_module', status='SUPERSEDED',
        input_json={'sources': ['frozen fixture']}, input_hash='frozen-parent-hash', output_json=None,
        attempts=[{'attempt': 1, 'outcome': 'OUTPUT_TRUNCATED'}, {'attempt': 2, 'outcome': 'OUTPUT_TRUNCATED'}],
        attempt_count=2, retry_count=2, fencing_token=3, error_category='OUTPUT_TRUNCATED',
        error='Original model truncation', uses_model=True)
    db_session.add(parent)
    await db_session.commit()
    before = row_value(parent)
    await jobs.control_run(db_session, run.id, test_workspace.id, action)
    await db_session.refresh(parent)
    assert row_value(parent) == before
    assert run.status == {'pause': 'PAUSED_USER', 'resume': 'QUEUED', 'cancel': 'CANCELLED'}[action]


async def split_fixture(session, workspace):
    from app.services import project_autopilot_engine as engine
    sha = 'a' * 40
    _, project, run = await setup_run(session, workspace,
        pipeline_version=engine.PIPELINE_VERSION, prompt_bundle_version=engine.PROMPT_BUNDLE_VERSION,
        target_sha=sha, model_config_fingerprint='f' * 64,
        budget={'max_model_calls': 10, 'max_source_bytes': 10000})
    at = jobs.utcnow()
    resolve = await jobs.claim_step(session, 'fixture', now=at)
    await jobs.complete_step(session, resolve, {'target_sha': sha}, now=at)
    refs, read_keys = [], []
    for i, path in enumerate(('a.py', 'b.py')):
        content = f'value = {i}\n'
        source = {'source_id': 's' + str(i), 'path': path, 'source_revision': sha,
            'content': content, 'content_sha256': hashlib.sha256(content.encode()).hexdigest(),
            'line_count': 1, 'category': 'code'}
        chunks = engine._split_source(source)
        key = engine._read_key(path)
        read_keys.append(key)
        refs.append({k: chunks[0][k] for k in ('source_id', 'path', 'start_line', 'end_line')})
        session.add(AnalysisStep(run_id=run.id, workspace_id=workspace.id, project_id=project.id,
            step_key=key, stage='read_source', status='SUCCEEDED', input_json={'path': path},
            input_hash='frozen-read-' + path, output_json={'source': source, 'chunks': chunks}))
    payload = {'module': {'key': 'm-fixture', 'name': 'Module', 'purpose': 'Read code',
        'paths': ['a.py', 'b.py']}, 'sources': refs, 'analysis_key': 'analyze:m-fixture:0:0'}
    await jobs.ensure_steps(session, run, [{'key': payload['analysis_key'], 'stage': 'analyze_module',
        'module_key': 'm-fixture', 'requires': read_keys, 'payload': payload}])
    await session.commit()
    parent = await session.scalar(select(AnalysisStep).where(AnalysisStep.run_id == run.id,
        AnalysisStep.step_key == payload['analysis_key']))
    for attempt in (1, 2):
        claim = await jobs.claim_step(session, 'fixture', now=at + timedelta(seconds=attempt * 20))
        assert claim and claim.step_id == parent.id
        await jobs.fail_step(session, claim, 'OUTPUT_TRUNCATED', 'Fixture length response',
            now=at + timedelta(seconds=attempt * 20 + 1))
    await session.refresh(parent)
    await session.refresh(run)
    assert parent.status == run.status == 'BLOCKED'
    return run, parent, [[refs[0]], [refs[1]]], at


@pytest.mark.asyncio
async def test_split_is_atomic_idempotent_and_preserves_failed_history(db_session, test_workspace):
    run, parent, groups, at = await split_fixture(db_session, test_workspace)
    old_parent = row_value(parent)
    old_budget, old_usage = deepcopy(run.budget), deepcopy(run.usage)
    old_success = {s.id: row_value(s) for s in (await db_session.scalars(select(AnalysisStep).where(
        AnalysisStep.run_id == run.id, AnalysisStep.status == 'SUCCEEDED'))).all()}
    plan = await jobs.split_failed_source_stage(db_session, run.id, test_workspace.id, parent.id, groups)
    await db_session.refresh(parent)
    await db_session.refresh(run)
    assert parent.status == 'SUPERSEDED' and parent.fencing_token == old_parent['fencing_token'] + 1
    for field in ('input_json', 'input_hash', 'output_json', 'attempts', 'retry_count', 'attempt_count', 'error', 'error_category'):
        assert getattr(parent, field) == old_parent[field]
    assert run.budget == old_budget and run.usage == old_usage and run.status == 'QUEUED' and run.error is None
    assert run.result_meta['source_splits'] == [plan]
    rows = (await db_session.scalars(select(AnalysisStep).where(AnalysisStep.run_id == run.id))).all()
    assert all(row_value(s) == old_success[s.id] for s in rows if s.id in old_success)
    from app.services.project_stage_splits import plan_specs
    specs = plan_specs(plan)
    child_keys = [s['key'] for s in specs[:-1]]
    children = [s for s in rows if s.step_key in child_keys]
    merge = next(s for s in rows if s.step_key == specs[-1]['key'])
    assert len(children) == 2 and all(s.uses_model and s.status == 'QUEUED' for s in children)
    assert merge.requires == child_keys and not merge.uses_model
    before_repeat = [row_value(s) for s in sorted(rows, key=lambda x: x.id)]
    assert await jobs.split_failed_source_stage(db_session, run.id, test_workspace.id, parent.id, groups) == plan
    after = (await db_session.scalars(select(AnalysisStep).where(AnalysisStep.run_id == run.id).order_by(AnalysisStep.id))).all()
    assert [row_value(s) for s in after] == before_repeat


@pytest.mark.asyncio
async def test_split_children_resume_independently_and_keep_parent_queue_position(db_session, test_workspace):
    from app.services.project_stage_splits import plan_specs
    run, parent, groups, at = await split_fixture(db_session, test_workspace)
    await jobs.ensure_steps(db_session, run, [{'key': 'analyze:later-original', 'stage': 'analyze_module',
        'requires': [], 'payload': {}, 'uses_model': True}])
    await db_session.commit()
    plan = await jobs.split_failed_source_stage(db_session, run.id, test_workspace.id, parent.id, groups)
    specs = plan_specs(plan)
    parent_history = row_value(parent)
    first = await jobs.claim_step(db_session, 'worker-before-restart', now=at + timedelta(seconds=100))
    assert first.step_key == specs[0]['key']
    assert await jobs.complete_step(db_session, first, {'explicit_queue_fixture': 'child-one'},
        now=at + timedelta(seconds=101))
    first_row = await db_session.get(AnalysisStep, first.step_id)
    first_saved = row_value(first_row)
    second = await jobs.claim_step(db_session, 'worker-lost', lease_seconds=5, now=at + timedelta(seconds=102))
    assert second.step_key == specs[1]['key']
    recovered = await jobs.claim_step(db_session, 'worker-after-restart', now=at + timedelta(seconds=108))
    assert recovered.step_id == second.step_id and recovered.fencing_token > second.fencing_token
    assert not await jobs.complete_step(db_session, second, {'stale': True}, now=at + timedelta(seconds=109))
    assert await jobs.complete_step(db_session, recovered, {'explicit_queue_fixture': 'child-two'},
        now=at + timedelta(seconds=109))
    merge = await jobs.claim_step(db_session, 'worker-after-restart', now=at + timedelta(seconds=110))
    assert merge.step_key == specs[-1]['key']
    await db_session.refresh(run)
    assert run.usage['model_calls'] == 5  # two original failures, child one, child two and its lease recovery
    await db_session.refresh(parent)
    await db_session.refresh(first_row)
    assert row_value(parent) == parent_history and row_value(first_row) == first_saved


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing', 'duplicate', 'reordered'])
async def test_invalid_partition_leaves_queue_and_budget_unchanged(db_session, test_workspace, change):
    run, parent, groups, at = await split_fixture(db_session, test_workspace)
    invalid = {'missing': [groups[0]], 'duplicate': [groups[0], groups[0]],
        'reordered': list(reversed(groups))}[change]
    before_parent, before_run = row_value(parent), row_value(run)
    with pytest.raises(ValueError):
        await jobs.split_failed_source_stage(db_session, run.id, test_workspace.id, parent.id, invalid)
    await db_session.rollback()
    await db_session.refresh(parent)
    await db_session.refresh(run)
    assert row_value(parent) == before_parent and row_value(run) == before_run


@pytest.mark.asyncio
async def test_split_cannot_cross_workspace_or_reopen_after_module_creation(db_session, test_workspace):
    run, parent, groups, at = await split_fixture(db_session, test_workspace)
    with pytest.raises(LookupError):
        await jobs.split_failed_source_stage(db_session, run.id, test_workspace.id + 100000, parent.id, groups)
    await jobs.ensure_steps(db_session, run, [{'key': 'module:already-planned', 'stage': 'module',
        'requires': [], 'payload': {}, 'uses_model': False}])
    await db_session.commit()
    with pytest.raises(ValueError, match='尚未组装'):
        await jobs.split_failed_source_stage(db_session, run.id, test_workspace.id, parent.id, groups)
    assert parent.status == 'BLOCKED' and run.status == 'BLOCKED'


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper', ['input_hash', 'requires', 'uses_model', 'parent_retry'])
async def test_changed_split_nodes_are_rejected_before_model_budget_is_consumed(db_session, test_workspace, tamper):
    from app.services.project_stage_splits import plan_specs
    run, parent, groups, at = await split_fixture(db_session, test_workspace)
    plan = await jobs.split_failed_source_stage(db_session, run.id, test_workspace.id, parent.id, groups)
    child = await db_session.scalar(select(AnalysisStep).where(AnalysisStep.run_id == run.id,
        AnalysisStep.step_key == plan_specs(plan)[0]['key']))
    if tamper == 'input_hash': child.input_hash = 'changed'
    elif tamper == 'requires': child.requires = []
    elif tamper == 'uses_model': child.uses_model = False
    else: parent.retry_count = 0
    await db_session.commit()
    assert await jobs.claim_step(db_session, 'must-not-call', now=at + timedelta(seconds=100)) is None
    await db_session.refresh(run)
    assert run.status == 'BLOCKED' and run.result_meta['error_category'] == 'CONFIG_CHANGED'
    assert run.usage['model_calls'] == 2


@pytest.mark.asyncio
async def test_split_does_not_increase_or_bypass_the_authorized_model_budget(db_session, test_workspace):
    run, parent, groups, at = await split_fixture(db_session, test_workspace)
    run.budget = {**run.budget, 'max_model_calls': 2}
    await db_session.commit()
    await jobs.split_failed_source_stage(db_session, run.id, test_workspace.id, parent.id, groups)
    assert await jobs.claim_step(db_session, 'budget-must-hold', now=at + timedelta(seconds=100)) is None
    await db_session.refresh(run)
    assert run.status == 'PAUSED_BUDGET' and run.usage['model_calls'] == run.budget['max_model_calls'] == 2
