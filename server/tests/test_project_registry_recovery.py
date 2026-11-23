"""Durable downstream contracts must be checked before publication, not just dispatch."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_project_autopilot_engine import context, drive, fixture_pipeline, runtime
from test_project_jobs import setup_run

from app.services.project_run_compatibility import assert_compatible

CONTRACT = 'source-registry-v1'


def run_for(api, contract=CONTRACT):
    return SimpleNamespace(pipeline_version=api.PIPELINE_VERSION,
                           prompt_bundle_version=api.PROMPT_BUNDLE_VERSION,
                           result_meta={'claim_input_contract': contract})


@pytest.mark.asyncio
async def test_unknown_contract_is_rejected_at_compatibility_entry(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    with pytest.raises(api.StageError) as caught:
        assert_compatible(run_for(api, 'unregistered-contract'), {})
    assert caught.value.category == 'CONFIG_CHANGED'


@pytest.mark.asyncio
@pytest.mark.parametrize('key_prefix', ['cross:', 'section:', 'candidate-part:'])
@pytest.mark.parametrize('mutation', ['missing', 'changed'])
async def test_successful_registry_checkpoint_must_bind_original_claims(monkeypatch, key_prefix, mutation):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    artifacts, _ = await drive(api, {**context(), 'claim_input_contract': CONTRACT}, runtime(api))
    frozen = deepcopy(artifacts)
    key = next(k for k in artifacts if k.startswith(key_prefix))
    if mutation == 'missing':
        artifacts[key].pop('claim_input_fingerprint')
    else:
        artifacts[key]['claim_input_fingerprint'] = '0' * 64
    with pytest.raises(api.StageError) as caught:
        assert_compatible(run_for(api), artifacts)
    assert caught.value.category == 'CONFIG_CHANGED'
    assert artifacts[key] != frozen[key]


@pytest.mark.asyncio
async def test_new_aggregates_are_self_describing_and_bind_all_source_stages(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    ctx = {**context(), 'claim_input_contract': CONTRACT}
    artifacts, _ = await drive(api, ctx, runtime(api))
    report = artifacts['report']
    expected_report = {k: api.fingerprint(v) for k, v in artifacts.items()
                       if k in ('resolve', 'manifest') or k.startswith(('module:', 'cross:', 'section:'))}
    expected_candidates = {k: api.fingerprint(v) for k, v in artifacts.items() if k.startswith('candidate-part:')}
    for output in (report, report['report'], report['snapshot']):
        assert output['input_contract_version'] == CONTRACT
        assert output['source_stage_fingerprints'] == expected_report
    assert artifacts['candidates']['input_contract_version'] == CONTRACT
    assert artifacts['candidates']['source_stage_fingerprints'] == expected_candidates
    assert_compatible(run_for(api), artifacts)
    cross_key = next(k for k in artifacts if k.startswith('cross:'))
    artifacts[cross_key]['unknowns'].append('Changed after report assembly')
    with pytest.raises(api.StageError) as caught:
        assert_compatible(run_for(api), artifacts)
    assert caught.value.category == 'CONFIG_CHANGED'


@pytest.mark.asyncio
async def test_legacy_aggregate_contract_remains_absent_and_cannot_be_relabelled(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    artifacts, _ = await drive(api, context(), runtime(api))
    frozen = deepcopy(artifacts)
    assert_compatible(run_for(api, 'legacy'), artifacts)
    assert all('input_contract_version' not in a and 'source_stage_fingerprints' not in a for a in artifacts.values())
    with pytest.raises(api.StageError) as caught:
        assert_compatible(run_for(api), artifacts)
    assert caught.value.category == 'CONFIG_CHANGED' and artifacts == frozen


@pytest.mark.asyncio
async def test_incompatible_completed_artifacts_cannot_publish_before_planning(monkeypatch, db_session, test_workspace):
    from app import project_worker as worker
    api, _ = await fixture_pipeline(monkeypatch, 1)
    legacy, _ = await drive(api, context(), runtime(api))
    _, project, run = await setup_run(db_session, test_workspace)
    run.result_meta = {**run.result_meta, 'claim_input_contract': CONTRACT, 'memory_sources_checked': True}
    await db_session.commit()
    published = []
    async def frozen_context(*args):
        return {**context(), 'claim_input_contract': CONTRACT}, legacy
    async def forbidden_publication(*args):
        published.append(True)
    monkeypatch.setattr(worker, 'context_for', frozen_context)
    monkeypatch.setattr(worker, 'publish_candidates', forbidden_publication)
    await worker.reconcile(async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False))
    await db_session.refresh(run)
    await db_session.refresh(project)
    assert run.status == 'BLOCKED' and run.result_meta['error_category'] == 'CONFIG_CHANGED'
    assert run.report is None and run.report_published_at is None
    assert project.latest_valid_analysis_id is None and published == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unknown_dependencies', [False, True])
async def test_incremental_registry_report_binds_raw_checkpoints_and_resumes(monkeypatch, unknown_dependencies):
    api, _ = await fixture_pipeline(monkeypatch, 2)
    original_chat = api.AgentAdapter.chat_with
    impact_requests = []
    async def module_fixture(self, cfg, system, user, **kwargs):
        request = json.loads(user)
        if request['task'] == 'plan_modules':
            return {'content': json.dumps({'modules': [
                {'name': entry['path'], 'purpose': 'Inspect the supplied module', 'paths': [entry['path']]}
                for entry in request['entries']]})}
        if request['task'] == 'analyze_change_impact':
            impact_requests.append(request)
            return {'content': json.dumps({'affected_paths': ['src/f0.py'],
                'unknown_dependencies': unknown_dependencies, 'reasons': []})}
        return await original_chat(self, cfg, system, user, **kwargs)
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', module_fixture)
    ctx = {**context(), 'claim_input_contract': CONTRACT}
    baseline, _ = await drive(api, ctx, runtime(api))
    frozen_baseline = deepcopy(baseline)
    previous_sha, target = baseline['resolve']['target_sha'], 'b' * 40
    old_probe, old_manifest, old_read = api.sources.probe_repository, api.sources.fetch_manifest_page, api.sources.read_source
    async def probe(project, credentials):
        return {**await old_probe(project, credentials), 'target_sha': target, 'parents': [previous_sha]}
    async def manifest(*args):
        value = await old_manifest(*args)
        value['entries'][0]['blob_oid'] = 'f' * 40
        return value
    async def read(project, snapshot, entry, credentials, **kwargs):
        return {**await old_read(project, snapshot, entry, credentials, **kwargs), 'source_revision': target}
    async def ancestry(*args): return True
    monkeypatch.setattr(api.sources, 'probe_repository', probe)
    monkeypatch.setattr(api.sources, 'fetch_manifest_page', manifest)
    monkeypatch.setattr(api.sources, 'read_source', read)
    monkeypatch.setattr(api.sources, 'check_ancestry', ancestry)
    incremental = {**ctx, 'mode': 'incremental', 'base_sha': previous_sha, 'target_sha': target,
                   'baseline': {**baseline['report'], 'artifacts': baseline}}
    execute = api.execute_stage
    report_checkpoint = {}
    async def capture_report(stage, inputs, rt):
        frozen = deepcopy(inputs['artifacts'])
        result = await execute(stage, inputs, rt)
        assert inputs['artifacts'] == frozen, 'Derived manifest must not replace a persisted source checkpoint'
        if stage == 'report':
            raw_manifest = inputs['artifacts']['manifest']
            assert 'impact' not in raw_manifest
            for output in (result['artifact'], result['artifact']['report'], result['artifact']['snapshot']):
                assert output['source_stage_fingerprints']['manifest'] == api.fingerprint(raw_manifest)
            report_checkpoint.update({**deepcopy(inputs['artifacts']), 'report': deepcopy(result['artifact'])})
        return result
    monkeypatch.setattr(api, 'execute_stage', capture_report)
    result, _ = await drive(api, incremental, runtime(api))
    assert impact_requests and result['impact']['unknown_dependencies'] is unknown_dependencies
    assert result['report']['coverage_status'] == 'COMPLETE'
    effective = result['report']['snapshot']['manifest']
    assert effective['impact'] == result['impact']
    assert effective['diff']['requires_full'] is unknown_dependencies
    assert result['manifest']['diff']['requires_full'] is False
    reused = [m for m in result['report']['snapshot']['modules'] if m.get('compatibility')]
    assert len(reused) == (0 if unknown_dependencies else 1)
    assert_compatible(run_for(api), result)
    # JSON persistence/reload preserves the actual checkpoint mapping. Resume
    # extraction after report publication, without redoing completed stages.
    restored = json.loads(json.dumps(report_checkpoint))
    assert_compatible(run_for(api), restored)
    resumed, executed = await drive(api, incremental, runtime(api), restored)
    assert executed and all(k.startswith('candidate-part:') or k == 'candidates' for k in executed)
    assert resumed == result and baseline == frozen_baseline
