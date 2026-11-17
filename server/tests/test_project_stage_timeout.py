"""Per-run streaming total budget stays separate from the HTTP I/O deadline."""
import asyncio
from copy import deepcopy
from dataclasses import fields
import json
from types import SimpleNamespace

import pytest

from app import project_worker as worker
from app.services import project_autopilot_engine as api
from test_project_autopilot_engine import runtime

REPLY = {'content': json.dumps({'observations': [], 'conflicts': [], 'unknowns': []})}


def test_runtime_total_budget_defaults_to_none():
    assert 'stage_timeout' in {field.name for field in fields(api.RuntimeContext)}
    assert runtime(api).stage_timeout is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['json', 'sse-v1'])
async def test_default_adapter_arguments_remain_without_explicit_total(monkeypatch, mode):
    rt = runtime(api);rt.transport_mode = mode
    calls = []
    async def chat(self, cfg, system, user, **kwargs):
        calls.append(kwargs)
        return REPLY
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    _, _, meta = await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert calls == [{'json_mode': True, 'timeout': 180, **({'stream': True} if mode == 'sse-v1' else {})}]
    assert meta['io_timeout_seconds'] == meta['stage_timeout_seconds'] == 180


@pytest.mark.asyncio
@pytest.mark.parametrize('total', [180, 300, 600])
async def test_explicit_total_changes_execution_only_not_model_or_prompt(monkeypatch, total):
    rt = runtime(api);rt.transport_mode = 'sse-v1'
    original_config = deepcopy(rt.llm_config)
    calls = []
    async def chat(self, cfg, system, user, **kwargs):
        calls.append((deepcopy(cfg), system, user, kwargs))
        return REPLY
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    rt.stage_timeout = total
    _, _, meta = await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert calls[0][:3] == calls[1][:3]
    assert calls[1][3] == {'json_mode': True, 'timeout': 180, 'stream': True, 'total_timeout': total}
    assert rt.llm_config == original_config and rt.request_timeout == 180 and rt.input_chars == 40000
    assert meta['io_timeout_seconds'] == 180 and meta['stage_timeout_seconds'] == total
    assert meta['prompt_version'] == api.PROMPT_BUNDLE_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,total,io', [
    ('json', 300, 180), ('sse-v1', 0, 180), ('sse-v1', -1, 180),
    ('sse-v1', 600.01, 180), ('sse-v1', float('inf'), 180), ('sse-v1', float('nan'), 180),
    ('sse-v1', '300', 180), ('sse-v1', True, 180), ('sse-v1', {}, 180),
    ('sse-v1', 179.99, 180), ('sse-v1', 300, '180'), ('sse-v1', 300, float('inf')),
])
async def test_invalid_explicit_total_is_rejected_before_adapter_call(monkeypatch, mode, total, io):
    rt = runtime(api);rt.transport_mode = mode;rt.stage_timeout = total;rt.request_timeout = io
    calls = []
    async def chat(*args, **kwargs):
        calls.append(True)
        return REPLY
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    with pytest.raises(api.StageError) as caught:
        await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert caught.value.category == 'CONFIG_CHANGED' and calls == []


@pytest.mark.asyncio
async def test_explicit_total_allows_completion_after_io_duration(monkeypatch):
    # The adapter fixture represents an active stream, so elapsed duration can
    # exceed the idle timeout. Real HTTP I/O enforcement is the adapter's job.
    rt = runtime(api);rt.transport_mode = 'sse-v1';rt.request_timeout = .01;rt.stage_timeout = .2
    calls = []
    async def chat(self, *args, **kwargs):
        calls.append(kwargs)
        await asyncio.sleep(.04)
        return REPLY
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    value, _, meta = await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert value['observations'] == [] and len(calls) == 1
    assert calls[0]['timeout'] == .01 and calls[0]['total_timeout'] == .2
    assert meta['io_timeout_seconds'] == .01 and meta['stage_timeout_seconds'] == .2


@pytest.mark.asyncio
async def test_explicit_total_still_cancels_incomplete_stream_once(monkeypatch):
    rt = runtime(api);rt.transport_mode = 'sse-v1';rt.request_timeout = .01;rt.stage_timeout = .04
    calls, cancelled = [], []
    async def chat(self, *args, **kwargs):
        calls.append(kwargs)
        self.last_response_meta = {'stream_done': False, 'sse_events': 7, 'response_body_complete': False}
        self.last_usage = None
        try: await asyncio.sleep(1)
        finally: cancelled.append(True)
        return REPLY
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    with pytest.raises(api.StageError) as caught:
        await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert caught.value.category == 'REQUEST_TIMEOUT' and len(calls) == len(cancelled) == 1
    assert caught.value.request_meta['stream_done'] is False
    assert caught.value.request_meta['io_timeout_seconds'] == .01
    assert caught.value.request_meta['stage_timeout_seconds'] == .04


@pytest.mark.asyncio
async def test_explicit_policy_reaches_stream_adapter_without_changing_wire_payload(monkeypatch):
    import httpx
    from test_llm_stream_transport import Body, DONE, frame, install
    rt = runtime(api);rt.transport_mode = 'sse-v1';rt.stage_timeout = 300
    body = Body([frame(REPLY['content']) + frame(finish='stop') + DONE])
    io_limits = []
    async def response(request):
        io_limits.append(dict(request.extensions['timeout']))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=body)
    requests = install(monkeypatch, body, handler=response)
    value, _, meta = await api._ask('verify_cross_module', {'claims': []}, api.CrossResponse, rt)
    assert value['observations'] == [] and meta['stream_done'] and body.closed
    assert io_limits == [{'connect': 180, 'read': 180, 'write': 180, 'pool': 180}]
    assert len(requests) == 1 and requests[0]['stream'] is True
    assert 'timeout' not in requests[0] and 'total_timeout' not in requests[0]
    assert requests[0]['model'] == rt.llm_config['model']
    assert meta['io_timeout_seconds'] == 180 and meta['stage_timeout_seconds'] == 300


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit', [None, 300])
async def test_worker_total_budget_reads_only_run_metadata(monkeypatch, explicit):
    step = SimpleNamespace(error_category=None, attempts=[])
    metadata = {'transport_mode': 'sse-v1'}
    if explicit is not None: metadata['stage_timeout_seconds'] = explicit
    run = SimpleNamespace(model_config_fingerprint='', budget={'max_model_calls': 1000,
        'request_timeout': 180, 'input_chars': 40000, 'stage_timeout_seconds': 555}, result_meta=metadata)
    project = SimpleNamespace(id=2)
    claim = SimpleNamespace(run_id=1, project_id=2, workspace_id=1, step_id=10, stage='cross_module', payload={})
    seen, completed = [], []
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, model, identity):
            return {worker.ProjectAnalysis: run, worker.Project: project, worker.AnalysisStep: step}[model]
    async def heartbeat(*args): return True
    async def config(*args): return {'stage_timeout_seconds': 444}, {}, {}
    async def context(*args): return {}, {}
    async def execute(stage_name, inputs, rt):
        seen.append((getattr(rt, 'stage_timeout', None), rt.request_timeout, rt.input_chars, rt.transport_mode))
        return {'artifact': {}, 'usage': {'known': False}, 'request_meta': {}}
    async def complete(*args): completed.append(True)
    monkeypatch.setattr(worker.jobs, 'heartbeat', heartbeat)
    monkeypatch.setattr(worker.jobs, 'complete_step', complete)
    monkeypatch.setattr(worker, 'runtime_config', config)
    monkeypatch.setattr(worker, 'context_for', context)
    monkeypatch.setattr(worker, 'assert_compatible', lambda *args: None)
    monkeypatch.setattr(worker.analysis, 'execute_stage', execute)
    frozen = deepcopy(run.__dict__)
    await worker.execute_claim(claim, 'fixture-worker', factory=Session)
    assert seen == [(explicit, 180, 40000, 'sse-v1')] and completed == [True]
    assert run.__dict__ == frozen
