"""Replaced analysis failures remain history, never the current run diagnosis."""
from uuid import uuid4

import pytest

from app.models import AnalysisStep, ProjectAnalysis
from test_project_autopilot_api import api, project


async def split_fixture(session, workspace, *, status='QUEUED', error=None):
    item = await project(session, workspace)
    run = ProjectAnalysis(
        workspace_id=workspace.id, project_id=item.id, request_key=uuid4().hex,
        status=status, error=error, pipeline_version='autopilot-v1',
    )
    session.add(run)
    await session.flush()
    parent = AnalysisStep(
        run_id=run.id, workspace_id=workspace.id, project_id=item.id,
        step_key='analyze:fixture:original', stage='analyze_module',
        input_hash='frozen-original-input', status='SUPERSEDED',
        error_category='OUTPUT_TRUNCATED', attempt_count=2, retry_count=2,
        attempts=[{'attempt_no': 1, 'outcome': 'OUTPUT_TRUNCATED'},
                  {'attempt_no': 2, 'outcome': 'OUTPUT_TRUNCATED'}],
        input_json={'private_source': 'fixture-source-not-public'},
        output_json={'private_response': 'fixture-response-not-public'},
    )
    session.add(parent)
    await session.flush()
    return run, parent


@pytest.mark.asyncio
async def test_pending_split_merge_is_current_and_superseded_error_remains_history(db_session, test_workspace):
    run, parent = await split_fixture(db_session, test_workspace)
    merge = AnalysisStep(
        run_id=run.id, workspace_id=test_workspace.id, project_id=run.project_id,
        step_key='merge:source-split-v1:fixture', stage='merge_analysis_split',
        input_hash='frozen-child-outputs', status='QUEUED',
    )
    db_session.add(merge)
    await db_session.commit()

    async with api(db_session, test_workspace) as client:
        response = await client.get(f'/analysis-runs/{run.id}')
    assert response.status_code == 200
    value = response.json()
    assert value['stage'] == merge.stage
    assert value['error'] is None
    historical = next(step for step in value['steps'] if step['id'] == parent.id)
    assert historical['status'] == 'SUPERSEDED'
    assert historical['error_category'] == 'OUTPUT_TRUNCATED'
    assert historical['attempt_count'] == historical['retry_count'] == 2
    assert historical['attempts'] == parent.attempts
    assert historical['input_hash'] == parent.input_hash
    assert 'fixture-source-not-public' not in response.text
    assert 'fixture-response-not-public' not in response.text


@pytest.mark.asyncio
async def test_current_child_failure_is_not_replaced_by_superseded_parent_error(db_session, test_workspace):
    run, _ = await split_fixture(
        db_session, test_workspace, status='BLOCKED', error='当前子阶段的引用未通过校验',
    )
    db_session.add(AnalysisStep(
        run_id=run.id, workspace_id=test_workspace.id, project_id=run.project_id,
        step_key='analyze:source-split-v1:fixture:0', stage='analyze_module',
        input_hash='frozen-child-input', status='BLOCKED',
        error_category='INVALID_CITATION', attempt_count=1, retry_count=1,
    ))
    await db_session.commit()

    async with api(db_session, test_workspace) as client:
        response = await client.get(f'/analysis-runs/{run.id}')
    assert response.status_code == 200
    assert response.json()['error'] == {
        'code': 'INVALID_CITATION', 'message': '当前子阶段的引用未通过校验',
        'recoverable': True,
    }


@pytest.mark.asyncio
async def test_completed_run_has_no_current_stage_from_superseded_history(db_session, test_workspace):
    run, _ = await split_fixture(db_session, test_workspace, status='COMPLETED')
    db_session.add(AnalysisStep(
        run_id=run.id, workspace_id=test_workspace.id, project_id=run.project_id,
        step_key='merge:source-split-v1:fixture', stage='merge_analysis_split',
        input_hash='frozen-child-outputs', status='SUCCEEDED',
    ))
    await db_session.commit()

    async with api(db_session, test_workspace) as client:
        response = await client.get(f'/analysis-runs/{run.id}')
    assert response.status_code == 200
    assert response.json()['stage'] is None
    assert response.json()['error'] is None
    assert len(response.json()['steps']) == 2
