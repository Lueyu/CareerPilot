"""Run-specific protocol/encoding opt-ins preserve model and checkpoint identity."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app import project_worker as worker
from app.services import project_autopilot_engine as api
from test_project_autopilot_engine import runtime
from test_project_jobs import setup_run


@pytest.mark.asyncio
async def test_new_ai_scans_freeze_stream_deadline_and_legacy_jobs_keep_transport(db_session, test_workspace):
    from app.config import get_settings
    _, _, current = await setup_run(db_session, test_workspace)
    await db_session.refresh(current)
    assert current.result_meta['transport_mode'] == 'sse-v1'
    assert current.result_meta['stage_timeout_seconds'] == get_settings().project_scan_stage_timeout_seconds
    _, _, earlier = await setup_run(db_session, test_workspace, request_key='earlier',
                                    prompt_bundle_version='project-autopilot-prompts-v2')
    await db_session.refresh(earlier)
    assert 'transport_mode' not in earlier.result_meta


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['json', 'sse-v1'])
async def test_project_transport_is_explicit_and_preserves_model_and_deadline(monkeypatch, mode):
    rt = runtime(api)
    rt.transport_mode = mode
    frozen = deepcopy(rt.llm_config)
    calls = []
    async def chat(self, cfg, system, user, **kwargs):
        calls.append((deepcopy(cfg), kwargs))
        return {'content': json.dumps({'observations': [], 'conflicts': [], 'unknowns': []})}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    _, _, meta = await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert calls[0][0] == frozen == rt.llm_config
    assert calls[0][1]['timeout'] == 180
    assert 'reasoning_effort' not in calls[0][1]
    if mode == 'sse-v1':
        assert calls[0][1]['stream'] is True
    else:
        assert 'stream' not in calls[0][1], 'Legacy callers retain the identical adapter arguments'
    assert meta['transport_mode'] == mode


@pytest.mark.asyncio
async def test_unknown_transport_blocks_before_any_provider_request(monkeypatch):
    rt = runtime(api)
    rt.transport_mode = 'unregistered-mode'
    calls = []
    async def chat(*args, **kwargs):
        calls.append(True)
        return {'content': '{}'}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    with pytest.raises(api.StageError) as caught:
        await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert caught.value.category == 'CONFIG_CHANGED' and calls == []


@pytest.mark.asyncio
async def test_project_total_timeout_keeps_stream_progress_without_accepting_partial_result(monkeypatch):
    rt = runtime(api)
    rt.transport_mode = 'sse-v1'
    rt.request_timeout = .02
    calls = []
    async def chat(self, *args, **kwargs):
        calls.append(kwargs)
        self.last_response_meta = {'response_headers_received': True, 'sse_events': 7,
                                   'response_body_complete': False}
        self.last_usage = None
        await asyncio.sleep(1)
        raise AssertionError('The overall deadline must cancel this incomplete response')
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    with pytest.raises(api.StageError) as caught:
        await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert caught.value.category == 'REQUEST_TIMEOUT'
    assert caught.value.request_meta['sse_events'] == 7
    assert caught.value.request_meta['response_body_complete'] is False
    assert caught.value.usage == {'known': False}
    assert len(calls) == 1 and calls[0]['stream'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['success', 'truncated', 'missing_done', 'invalid_schema'])
async def test_actual_stream_adapter_and_project_validation_share_failure_contract(monkeypatch, outcome):
    from test_llm_stream_transport import Body, DONE, frame, install
    rt = runtime(api)
    rt.transport_mode = 'sse-v1'
    content = json.dumps({'observations': [], 'conflicts': [], 'unknowns': []})
    if outcome == 'invalid_schema': content = '{}'
    raw = frame(content) + frame(finish='length' if outcome == 'truncated' else 'stop')
    if outcome != 'missing_done': raw += DONE
    requests = install(monkeypatch, Body([raw]))
    if outcome == 'success':
        value, usage, meta = await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
        assert value['observations'] == [] and usage == {'known': False}
        assert meta['stream_done'] and meta['response_body_complete']
    else:
        with pytest.raises(api.StageError) as caught:
            await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
        assert caught.value.category == {'truncated': 'OUTPUT_TRUNCATED',
            'missing_done': 'NETWORK_TRANSIENT', 'invalid_schema': 'INVALID_SCHEMA'}[outcome]
        if outcome != 'invalid_schema':
            assert caught.value.request_meta['response_body_complete'] is False
    assert len(requests) == 1 and requests[0]['stream'] is True


@pytest.mark.asyncio
async def test_run_encoding_opt_in_reaches_context_without_rewriting_steps(db_session, test_workspace):
    _, project, run = await setup_run(db_session, test_workspace)
    ctx, _ = await worker.context_for(db_session, run, project)
    assert ctx.get('claim_input_contract', 'legacy') == 'legacy'
    run.result_meta = {**run.result_meta, 'claim_input_contract': 'source-registry-v1', 'transport_mode': 'sse-v1'}
    await db_session.commit()
    from sqlalchemy import select
    rows = (await db_session.scalars(select(worker.AnalysisStep).where(worker.AnalysisStep.run_id == run.id))).all()
    frozen = [(r.id, r.input_hash, deepcopy(r.input_json), r.attempt_count, r.retry_count) for r in rows]
    cfg_fingerprint, budget = run.model_config_fingerprint, deepcopy(run.budget)
    ctx, _ = await worker.context_for(db_session, run, project)
    assert ctx['claim_input_contract'] == 'source-registry-v1'
    assert cfg_fingerprint == run.model_config_fingerprint and budget == run.budget
    assert frozen == [(r.id, r.input_hash, r.input_json, r.attempt_count, r.retry_count) for r in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', [None, 'sse-v1'])
async def test_worker_reads_transport_only_from_explicit_run_metadata(monkeypatch, mode):
    step = SimpleNamespace(error_category=None, attempts=[])
    run = SimpleNamespace(model_config_fingerprint='', budget={}, result_meta={'transport_mode': mode} if mode else {})
    project = SimpleNamespace(id=2)
    claim = SimpleNamespace(run_id=1, project_id=2, workspace_id=1, step_id=10,
                            stage='cross_module', payload={})
    seen, completed = [], []
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, model, identity):
            return {worker.ProjectAnalysis: run, worker.Project: project, worker.AnalysisStep: step}[model]
    async def heartbeat(*args): return True
    async def config(*args): return {}, {}, {}
    async def context(*args): return {}, {}
    async def execute(stage_name, inputs, rt):
        seen.append(getattr(rt, 'transport_mode', 'json'))
        return {'artifact': {}, 'usage': {'known': False}, 'request_meta': {}}
    async def complete(*args): completed.append(True)
    monkeypatch.setattr(worker.jobs, 'heartbeat', heartbeat)
    monkeypatch.setattr(worker.jobs, 'complete_step', complete)
    monkeypatch.setattr(worker, 'runtime_config', config)
    monkeypatch.setattr(worker, 'context_for', context)
    monkeypatch.setattr(worker, 'assert_compatible', lambda *args: None)
    monkeypatch.setattr(worker.analysis, 'execute_stage', execute)
    before = deepcopy(run.__dict__)
    await worker.execute_claim(claim, 'fixture-worker', factory=Session)
    assert seen == [mode or 'json'] and completed == [True]
    assert run.__dict__ == before
