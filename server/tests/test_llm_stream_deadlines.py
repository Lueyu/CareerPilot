"""Explicit SSE total deadlines do not extend HTTP I/O timeouts or alter payloads."""
import asyncio
import json
import time
from contextlib import asynccontextmanager, suppress

import httpx
import pytest
from test_llm_stream_transport import CONFIG, DONE, Body, frame, install

from app.config import MAX_STUDY_TIMEOUT_SECONDS
from app.services.llm import AgentAdapter


@asynccontextmanager
async def loopback(monkeypatch,mode):
    requests=[];io_timeouts=[];finished=asyncio.Event();stop=asyncio.Event()
    async def serve(reader,writer):
        try:
            raw=await reader.readuntil(b'\r\n\r\n')
            size=next(int(line.split(b':',1)[1].strip()) for line in raw.split(b'\r\n') if line.lower().startswith(b'content-length:'))
            requests.append(json.loads(await reader.readexactly(size)))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n')
            async def send(data):
                writer.write(f'{len(data):x}\r\n'.encode()+data+b'\r\n')
                await writer.drain()
            await send(frame(role='assistant'))
            if mode=='idle':
                await reader.read()
            else:
                count=0
                while not stop.is_set() and not reader.at_eof():
                    await asyncio.sleep(.02)
                    await send(b': heartbeat\n\n');count+=1
                    if mode=='complete' and count==8:
                        await send(frame('{"ok":true}')+frame(finish='stop')+DONE)
                        writer.write(b'0\r\n\r\n');await writer.drain()
                        break
        except (ConnectionError,asyncio.IncompleteReadError):
            pass  # Expected when the fixture client cancels or times out.
        finally:
            writer.close()
            with suppress(ConnectionError):await writer.wait_closed()
            finished.set()
    server=await asyncio.start_server(serve,'127.0.0.1',0)
    port=server.sockets[0].getsockname()[1]
    original=httpx.AsyncClient
    # Fixture TLS context preparation occurs before the short test deadline.
    prepared=original(trust_env=False)
    def client(**kwargs):
        io_timeouts.append(kwargs['timeout'])
        prepared.timeout=kwargs['timeout']
        prepared.event_hooks=kwargs.get('event_hooks',{})
        return prepared
    monkeypatch.setattr(httpx,'AsyncClient',client)
    try:
        yield {**CONFIG,'base_url':f'http://127.0.0.1:{port}/v1'},requests,io_timeouts
    finally:
        stop.set()
        await prepared.aclose()
        server.close();await server.wait_closed()
        if requests:await asyncio.wait_for(finished.wait(),1)


@pytest.mark.asyncio
async def test_stream_activity_can_outlast_io_timeout_within_explicit_total(monkeypatch):
    async with loopback(monkeypatch,'complete') as (config,requests,timeouts):
        adapter=AgentAdapter();started=time.monotonic()
        result=await adapter.chat_with(config,'exact system','exact user',json_mode=True,timeout=.08,stream=True,total_timeout=.75)
        elapsed=time.monotonic()-started
        assert result=={'content':'{"ok":true}'} and adapter.last_error is None
        assert elapsed>.08 and elapsed<.75 and timeouts==[.08]
        assert requests==[{'model':'unchanged-model','messages':[{'role':'system','content':'exact system'},{'role':'user','content':'exact user'}],
                           'temperature':.2,'max_tokens':4096,'stream_options':{'include_usage':True},'response_format':{'type':'json_object'},'stream':True}]


@pytest.mark.asyncio
async def test_stream_idle_still_hits_io_timeout_before_larger_total(monkeypatch):
    async with loopback(monkeypatch,'idle') as (config,requests,timeouts):
        adapter=AgentAdapter();started=time.monotonic()
        assert await adapter.chat_with(config,'s','u',timeout=.05,stream=True,total_timeout=.5) is None
        assert time.monotonic()-started<.4 and timeouts==[.05] and len(requests)==1
        assert adapter.last_error=={'kind':'timeout'}
        assert adapter.last_response_meta['response_headers_received'] and not adapter.last_response_meta['response_body_complete']


@pytest.mark.asyncio
@pytest.mark.parametrize('override',[None,.18])
async def test_continuous_stream_activity_cannot_extend_default_or_explicit_total(monkeypatch,override):
    async with loopback(monkeypatch,'endless') as (config,requests,timeouts):
        adapter=AgentAdapter();started=time.monotonic()
        options={} if override is None else {'total_timeout':override}
        assert await adapter.chat_with(config,'s','u',timeout=.08,stream=True,**options) is None
        elapsed=time.monotonic()-started
        assert elapsed>=(override or .08)*.8 and elapsed<(override or .08)+.4
        assert len(requests)==1 and timeouts==[.08] and adapter.last_error=={'kind':'timeout'}
        assert adapter.last_response_meta['sse_event_count']>=2 and not adapter.last_response_meta['stream_done']


@pytest.mark.asyncio
@pytest.mark.parametrize('stream,total',[(True,0),(True,-1),(True,float('inf')),(True,float('-inf')),(True,float('nan')),
    (True,MAX_STUDY_TIMEOUT_SECONDS + 0.01),(True,True),(True,'300'),(True,10**1000),(False,300),(False,.5)],
    ids=['zero','negative','infinity','negative-infinity','nan','over-cap','boolean','string','huge-int','nonstream-long','nonstream-short'])
async def test_invalid_explicit_total_is_rejected_before_any_post(monkeypatch,stream,total):
    requests=install(monkeypatch,Body([frame('ok')+frame(finish='stop')+DONE]));adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'s','u',timeout=180,stream=stream,total_timeout=total) is None
    assert adapter.last_error=={'kind':'configuration'} and requests==[]
    assert adapter.last_usage is None and adapter.last_response_meta=={}


@pytest.mark.asyncio
@pytest.mark.parametrize('total',[None,300,600])
async def test_valid_total_override_never_becomes_a_model_payload_parameter(monkeypatch,total):
    requests=install(monkeypatch,Body([frame('ok')+frame(finish='stop')+DONE]));adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'s','u',json_mode=True,timeout=180,stream=True,total_timeout=total,reasoning_effort='low')=={'content':'ok'}
    assert requests==[{'model':'unchanged-model','messages':[{'role':'system','content':'s'},{'role':'user','content':'u'}],
        'temperature':.2,'max_tokens':4096,'stream_options':{'include_usage':True},'response_format':{'type':'json_object'},'reasoning_effort':'low','stream':True}]


@pytest.mark.asyncio
async def test_explicit_none_preserves_nonstream_default_request(monkeypatch):
    async def handler(request):
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'ok'}}]})
    requests=install(monkeypatch,Body([]),handler=handler);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'s','u',timeout=180,total_timeout=None)=={'content':'ok'}
    assert requests==[{'model':'unchanged-model','messages':[{'role':'system','content':'s'},{'role':'user','content':'u'}],'temperature':.2,'max_tokens':4096}]


@pytest.mark.asyncio
async def test_first_content_guard_aborts_stream_that_never_emits_body(monkeypatch):
    calls = []

    async def fake_record(self, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(AgentAdapter, '_record_metric', fake_record)
    install(monkeypatch, Body([b': heartbeat\r\n\r\n'], stall=True))
    adapter = AgentAdapter()
    assert await adapter.chat_with(CONFIG, 's', 'u', timeout=2, stream=True,
                                   total_timeout=3, first_content_timeout=.05) is None
    assert adapter.last_error == {'kind': 'timeout'}
    assert adapter.last_response_meta['stream_error'] == 'first_content_timeout'
    assert calls[-1]['error_kind'] == 'timeout:first_content_timeout'


@pytest.mark.asyncio
async def test_first_content_guard_allows_stream_with_visible_body(monkeypatch):
    install(monkeypatch, Body([frame('ok') + frame(finish='stop') + DONE]))
    adapter = AgentAdapter()
    assert await adapter.chat_with(CONFIG, 's', 'u', timeout=2, stream=True,
                                   total_timeout=3, first_content_timeout=2) == {'content': 'ok'}


@pytest.mark.asyncio
async def test_first_content_guard_requires_stream(monkeypatch):
    adapter = AgentAdapter()
    assert await adapter.chat_with(CONFIG, 's', 'u', first_content_timeout=1) is None
    assert adapter.last_error == {'kind': 'configuration'}

pytestmark = pytest.mark.usefixtures("mock_private_billing")
