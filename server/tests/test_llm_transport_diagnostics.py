"""Transport observations never alter prompts, results, or retain sensitive headers."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import project_autopilot_engine as engine
from app.services.llm import AgentAdapter

CONFIG = {'provider':'fixture','model':'unchanged-model','base_url':'https://fixture.invalid/v1','api_key':'SECRET_KEY'}


class InterruptedBody(httpx.AsyncByteStream):
    def __init__(self, stall=False): self.stall=stall
    async def __aiter__(self):
        yield b'{"private_body":"SECRET_BODY"'
        if self.stall: await asyncio.Event().wait()
        raise httpx.ReadTimeout('SECRET_EXCEPTION')


def transport(monkeypatch, handler):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))


@pytest.mark.asyncio
@pytest.mark.parametrize('headers_received',[False,True])
async def test_adapter_distinguishes_timeout_phase_without_sensitive_data(monkeypatch,headers_received):
    async def handler(request):
        if not headers_received: raise httpx.ReadTimeout('SECRET_EXCEPTION')
        return httpx.Response(200,headers={'x-request-id':'request-fixture','content-type':'application/json; secret=SECRET_PARAMETER',
            'authorization':'SECRET_AUTH','set-cookie':'SECRET_COOKIE','x-internal':'SECRET_INTERNAL'},stream=InterruptedBody())
    transport(monkeypatch,handler)
    adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user') is None
    assert adapter.last_error=={'kind':'timeout'}
    meta=adapter.last_response_meta
    assert meta['response_headers_received'] is headers_received
    assert meta['response_body_complete'] is False
    assert meta['transport_phase']==('reading_response_body' if headers_received else 'request_not_yet_traced')
    if headers_received:
        assert meta['status']==200 and meta['request_id']=='request-fixture'
        assert meta['content_type']=='application/json' and meta['response_headers_ms']>=0
    else: assert 'status' not in meta and 'response_headers_ms' not in meta
    assert 'SECRET' not in json.dumps({'error':adapter.last_error,'meta':meta})


@pytest.mark.asyncio
@pytest.mark.parametrize('headers_received',[False,True])
async def test_outer_deadline_preserves_already_received_headers(monkeypatch,headers_received):
    async def handler(request):
        if not headers_received: await asyncio.Event().wait()
        return httpx.Response(200,headers={'x-request-id':'outer-deadline','content-type':'application/json',
            'set-cookie':'SECRET_COOKIE'},stream=InterruptedBody(stall=True))
    transport(monkeypatch,handler)
    runtime=engine.RuntimeContext(1,2,CONFIG,{},request_timeout=0.03)
    with pytest.raises(engine.StageError) as error:
        await engine._ask('plan_modules',{'entries':[]},engine.PlanResponse,runtime)
    assert error.value.category=='REQUEST_TIMEOUT'
    assert error.value.request_meta['response_headers_received'] is headers_received
    assert error.value.request_meta['response_body_complete'] is False
    assert error.value.request_meta['transport_phase']==('reading_response_body' if headers_received else 'request_not_yet_traced')
    assert error.value.request_id==('outer-deadline' if headers_received else None)
    assert 'SECRET' not in json.dumps(error.value.request_meta)


@pytest.mark.asyncio
async def test_success_keeps_existing_payload_and_only_safe_header_metadata(monkeypatch):
    captured=[]
    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,headers={'x-request-id':'success-fixture','content-type':'application/json; token=SECRET',
            'set-cookie':'SECRET_COOKIE','authorization':'SECRET_AUTH'},json={
            'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}],
            'usage':{'prompt_tokens':4,'completion_tokens':3,'total_tokens':7}})
    transport(monkeypatch,handler)
    adapter=AgentAdapter()
    result=await adapter.chat_with(CONFIG,'exact system','exact user',json_mode=True,timeout=180)
    assert result=={'content':'{"ok":true}','usage':{'prompt_tokens':4,'completion_tokens':3,'total_tokens':7}}
    assert captured==[{'model':'unchanged-model','messages':[{'role':'system','content':'exact system'},
        {'role':'user','content':'exact user'}],'temperature':0.2,'max_tokens':4096,'response_format':{'type':'json_object'}}]
    meta=adapter.last_response_meta
    assert meta['response_headers_received'] is True and meta['response_body_complete'] is True
    assert meta['transport_phase']=='response_body_received'
    assert meta['response_body_ms']>=meta['response_headers_ms']>=0
    assert meta['content_type']=='application/json' and meta['request_id']=='success-fixture'
    assert 'SECRET' not in json.dumps(meta)


@pytest.mark.asyncio
@pytest.mark.parametrize('event',['connection.connect_tcp','connection.start_tls'])
async def test_connection_timeout_never_claims_response_header_wait(monkeypatch,event):
    async def handler(request):
        trace=request.extensions['trace']
        await trace(event+'.started',{'host':'SECRET_HOST','ssl_context':'SECRET_SSL'})
        await trace(event+'.failed',{'exception':httpx.ConnectTimeout('SECRET_EXCEPTION')})
        raise httpx.ConnectTimeout('SECRET_EXCEPTION')
    transport(monkeypatch,handler)
    adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user') is None
    meta=adapter.last_response_meta
    assert meta['transport_phase']==event+'.failed'
    assert [row['event'] for row in meta['transport_events']]==[event+'.started',event+'.failed']
    assert meta['response_headers_received'] is False
    assert 'SECRET' not in json.dumps(meta)


@pytest.mark.asyncio
@pytest.mark.parametrize('method,scope',[(b'CONNECT','PROXY_CONNECT'),(b'POST','TARGET_POST')])
async def test_proxy_connect_and_target_post_header_waits_are_distinct(monkeypatch,method,scope):
    async def handler(request):
        trace=request.extensions['trace']
        info={'request':SimpleNamespace(method=method,headers={'authorization':'SECRET_AUTH'},url='SECRET_URL'),
            'headers':{'set-cookie':'SECRET_COOKIE'},'body':'SECRET_BODY','return_value':'SECRET_RETURN'}
        for name in ('send_request_headers','send_request_body'):
            await trace('http11.'+name+'.started',info)
            await trace('http11.'+name+'.complete',info)
        await trace('http11.receive_response_headers.started',info)
        await trace('http11.receive_response_headers.failed',{'exception':'SECRET_EXCEPTION'})
        await trace('http11.response_closed.started',info)
        await trace('http11.response_closed.complete',info)
        # Unrecognized names must not turn tracing into an arbitrary text store.
        await trace('SECRET_UNKNOWN_EVENT',info)
        raise httpx.ReadTimeout('SECRET_EXCEPTION')
    transport(monkeypatch,handler)
    adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user') is None
    meta=adapter.last_response_meta
    assert meta['transport_phase']==scope.lower()+':http11.receive_response_headers.failed'
    rows=meta['transport_events']
    assert len(rows)==8 and all(row['request_kind']==scope for row in rows)
    assert all(set(row)=={'event','elapsed_ms','request_kind'} for row in rows)
    assert [row['elapsed_ms'] for row in rows]==sorted(row['elapsed_ms'] for row in rows)
    assert meta['response_headers_received'] is False
    assert 'SECRET' not in json.dumps(meta)


@pytest.mark.asyncio
async def test_trace_scope_switches_after_tunnel_without_storing_connect_info(monkeypatch):
    async def handler(request):
        trace=request.extensions['trace']
        await trace('http11.send_request_headers.started',{'request':SimpleNamespace(method=b'CONNECT')})
        await trace('http11.receive_response_headers.complete',{'return_value':(b'HTTP/1.1',200,b'OK',[(b'cookie',b'SECRET')])})
        await trace('proxy.start_tls.started',{'server_hostname':'SECRET_HOST'})
        await trace('proxy.start_tls.complete',{'return_value':'SECRET_STREAM'})
        await trace('http11.send_request_headers.started',{'request':SimpleNamespace(method=b'POST')})
        await trace('http11.receive_response_headers.started',{'request':SimpleNamespace(method=b'POST')})
        raise httpx.ReadTimeout('SECRET_EXCEPTION')
    transport(monkeypatch,handler)
    adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user') is None
    events=adapter.last_response_meta['transport_events']
    assert events[0]['request_kind']=='PROXY_CONNECT' and events[-1]['request_kind']=='TARGET_POST'
    assert adapter.last_response_meta['transport_phase']=='target_post:http11.receive_response_headers.started'
    assert 'SECRET' not in json.dumps(adapter.last_response_meta)


@pytest.mark.asyncio
@pytest.mark.parametrize('proxy',[False,True])
async def test_real_httpcore_loopback_trace_distinguishes_connect_from_post(monkeypatch,proxy):
    """Exercise HTTPcore's actual trace calls against a local stalled server."""
    finished=asyncio.Event()
    async def stalled(reader,writer):
        try:
            await reader.readuntil(b'\r\n\r\n')
            await reader.read()  # Client deadline closes it; never send a response.
        finally:
            writer.close()
            await writer.wait_closed()
            finished.set()
    server=await asyncio.start_server(stalled,'127.0.0.1',0)
    port=server.sockets[0].getsockname()[1]
    original=httpx.AsyncClient
    def client(**kwargs):
        # Loopback fixture only: never touch inherited route settings or a provider.
        return original(trust_env=False,proxy=f'http://127.0.0.1:{port}' if proxy else None,**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',client)
    cfg=CONFIG if proxy else {**CONFIG,'base_url':f'http://127.0.0.1:{port}/v1'}
    try:
        adapter=AgentAdapter()
        assert await adapter.chat_with(cfg,'system','user',timeout=0.05) is None
        await asyncio.wait_for(finished.wait(),1)
        meta=adapter.last_response_meta
        scope='PROXY_CONNECT' if proxy else 'TARGET_POST'
        assert meta['transport_phase']==scope.lower()+':http11.receive_response_headers.failed'
        assert any(e['event']=='connection.connect_tcp.complete' for e in meta['transport_events'])
        assert any(e['event']=='http11.send_request_body.complete' and e['request_kind']==scope for e in meta['transport_events'])
        assert not meta['response_headers_received']
        assert 'SECRET' not in json.dumps(meta)
    finally:
        server.close()
        await server.wait_closed()

pytestmark = pytest.mark.usefixtures("mock_private_billing")
