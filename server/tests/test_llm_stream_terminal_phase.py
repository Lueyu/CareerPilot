"""Real HTTPcore cleanup must not overwrite an accepted SSE terminal state."""
import asyncio

import pytest

from app.config import get_settings
from app.services import model_outbound
from app.services.llm import AgentAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['complete','disconnect','missing_done'])
async def test_real_loopback_stream_terminal_phase_distinguishes_cleanup_from_failure(monkeypatch,outcome):
    requests=[]
    finished=asyncio.Event()

    async def local_sse(reader,writer):
        try:
            headers=await reader.readuntil(b'\r\n\r\n')
            length=next((int(line.split(b':',1)[1].strip()) for line in headers.split(b'\r\n')
                         if line.lower().startswith(b'content-length:')),0)
            if length:await reader.readexactly(length)
            requests.append(headers.split(b' ',1)[0])
            body=(b'data: {"choices":[{"index":0,"delta":{"content":"{\\"ok\\":true}"},"finish_reason":null}]}\n\n'
                  b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
            if outcome=='complete':body+=b'data: [DONE]\n\n'
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n')
            writer.write(f'{len(body):x}\r\n'.encode()+body+b'\r\n')
            if outcome!='disconnect':writer.write(b'0\r\n\r\n')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()

    server=await asyncio.start_server(local_sse,'127.0.0.1',0)
    port=server.sockets[0].getsockname()[1]
    # Construct TLS machinery before the short fixture deadline; no request yet.
    origin=f"http://127.0.0.1:{port}"
    monkeypatch.setattr(get_settings(), "model_local_origins", [origin])
    prepared=model_outbound.model_client(origin,timeout=2)
    def client(**kwargs):
        prepared.event_hooks=kwargs.get('event_hooks',{})
        return prepared
    monkeypatch.setattr('app.services.llm.model_client',lambda value, **kw: client(**kw))
    try:
        adapter=AgentAdapter()
        result=await adapter.chat_with({'provider':'fixture','model':'fixture','base_url':f'http://127.0.0.1:{port}/v1',
                                       'api_key':'local-fixture'},'fixture','fixture',timeout=2,stream=True)
        await asyncio.wait_for(finished.wait(),1)
        # HTTPcore may finish generator cleanup after client.stream.__aexit__.
        await asyncio.sleep(.02)
        meta=adapter.last_response_meta
        assert requests==[b'POST']
        if outcome=='complete':
            assert result=={'content':'{"ok":true}'} and adapter.last_error is None
            assert meta['finish_reason']=='stop' and meta['stream_done'] and meta['response_body_complete']
            # Preserve original diagnostics even when generator closure is marked failed.
            assert any(row['event']=='http11.receive_response_body.failed' for row in meta['transport_events'])
            assert meta['transport_phase']=='stream_complete'
        else:
            assert result is None and adapter.last_error=={'kind':'network'}
            assert not meta['stream_done'] and not meta['response_body_complete']
            if outcome=='disconnect':
                assert meta['transport_phase']=='target_post:http11.receive_response_body.failed'
            else:
                assert meta['stream_error']=='missing_done'
                # HTTP framing completed; the SSE protocol did not. Do not invent stream success.
                assert meta['transport_phase']=='target_post:http11.receive_response_body.complete'
    finally:
        server.close()
        await server.wait_closed()
        await prepared.aclose()

pytestmark = pytest.mark.usefixtures("mock_private_billing")
