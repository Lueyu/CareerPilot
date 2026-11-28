"""Every failed model call is persisted with an actionable reason."""
import asyncio

import httpx
import pytest
from model_transport_fixtures import install_model_transport

from app.services.llm import AgentAdapter

CONFIG = {'provider': 'fixture', 'model': 'fixture-model',
          'base_url': 'https://fixture.invalid/v1', 'api_key': 'SECRET_KEY'}


def transport(monkeypatch, handler):
    install_model_transport(monkeypatch, httpx.MockTransport(handler))


def capture(monkeypatch):
    calls = []

    async def fake_record(self, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(AgentAdapter, '_record_metric', fake_record)
    return calls


@pytest.mark.asyncio
async def test_stream_failure_persists_concrete_code(monkeypatch):
    body = (b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: [DONE]\n\n')

    def handler(request):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)

    transport(monkeypatch, handler)
    calls = capture(monkeypatch)
    adapter = AgentAdapter()
    assert await adapter.chat_with(CONFIG, 's', 'u', stream=True) is None
    # Caller-facing contract stays coarse for failover decisions.
    assert adapter.last_error == {'kind': 'invalid_response'}
    assert adapter.last_response_meta['stream_error'] == 'empty_content'
    assert calls[-1]['ok'] is False
    assert calls[-1]['error_kind'] == 'invalid_response:empty_content'


@pytest.mark.asyncio
async def test_non_stream_invalid_body_has_no_stream_code(monkeypatch):
    def handler(request):
        return httpx.Response(200, headers={'content-type': 'application/json'}, content=b'not json')

    transport(monkeypatch, handler)
    calls = capture(monkeypatch)
    adapter = AgentAdapter()
    assert await adapter.chat_with(CONFIG, 's', 'u') is None
    assert calls[-1]['error_kind'] == 'invalid_response'


@pytest.mark.asyncio
async def test_cancelled_call_is_classified_and_reraised(monkeypatch):
    started = asyncio.Event()

    async def handler(request):
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(200, json={})

    transport(monkeypatch, handler)
    calls = capture(monkeypatch)
    adapter = AgentAdapter()
    task = asyncio.create_task(adapter.chat_with(CONFIG, 's', 'u'))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert adapter.last_error == {'kind': 'cancelled'}
    assert calls[-1]['ok'] is False
    assert calls[-1]['error_kind'] == 'cancelled'


@pytest.mark.asyncio
async def test_success_records_no_error(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
        })

    transport(monkeypatch, handler)
    calls = capture(monkeypatch)
    adapter = AgentAdapter()
    result = await adapter.chat_with(CONFIG, 's', 'u')
    assert result['content'] == 'ok'
    assert calls[-1]['error_kind'] is None
    assert calls[-1]['ok'] is True

@pytest.mark.asyncio
async def test_explicit_transport_fixture_writes_no_metric(monkeypatch):
    """A fixture adapter must not open a session against the production metrics table."""
    import app.db as db_module

    def forbidden():
        raise AssertionError("fixture call must not open a metrics session")

    monkeypatch.setattr(db_module, 'SessionLocal', forbidden)
    adapter = AgentAdapter()
    await adapter._record_metric(
        kind="llm", provider="fixture", model="unchanged-model", duration_ms=10,
        ttft_ms=None, usage={}, ok=True, error_kind=None, finish_reason="stop",
        stream=True, session=None, cost_cny=None, cost_basis="unknown", billing_call_id="fixture", workspace_id=None,
    )


@pytest.mark.asyncio
async def test_stream_metric_records_first_content_not_headers(monkeypatch):
    from app.services.llm_stream import ChatCompletionStream

    body = (b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
            b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":7,"total_tokens":12}}\n\n'
            b'data: [DONE]\n\n')

    def handler(request):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)

    transport(monkeypatch, handler)
    # Force a distinct first-content timestamp; response headers arrive near t0.
    monkeypatch.setattr(ChatCompletionStream, 'elapsed', lambda self: 1234)
    calls = capture(monkeypatch)
    adapter = AgentAdapter()
    result = await adapter.chat_with({**CONFIG, 'report_usage': True}, 's', 'u', stream=True)
    assert result['content'] == 'ok'
    assert adapter.last_response_meta['first_content_ms'] == 1234
    assert calls[-1]['ttft_ms'] == 1234
    assert calls[-1]['ttft_ms'] != adapter.last_response_meta['response_headers_ms']
    assert calls[-1]['usage']['total_tokens'] == 12


@pytest.mark.asyncio
async def test_failed_stream_keeps_reported_usage(monkeypatch):
    body = (b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}\n\n'
            b'data: {broken json\n\n')

    def handler(request):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)

    transport(monkeypatch, handler)
    calls = capture(monkeypatch)
    adapter = AgentAdapter()
    assert await adapter.chat_with(CONFIG, 's', 'u', stream=True) is None
    assert calls[-1]['ok'] is False
    assert calls[-1]['error_kind'] == 'invalid_response:invalid_json'
    assert calls[-1]['usage']['total_tokens'] == 7


@pytest.mark.asyncio
async def test_non_stream_metric_keeps_header_as_ttft(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={
            'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
        })

    transport(monkeypatch, handler)
    calls = capture(monkeypatch)
    adapter = AgentAdapter()
    await adapter.chat_with(CONFIG, 's', 'u')
    assert calls[-1]['ttft_ms'] == adapter.last_response_meta['response_headers_ms']

pytestmark = pytest.mark.usefixtures("mock_private_billing")
