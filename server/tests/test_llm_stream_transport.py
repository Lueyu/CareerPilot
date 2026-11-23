"""Opt-in SSE uses one bounded request and never retains reasoning or partial results."""
import asyncio
import json
import time
from dataclasses import replace

import httpx
import pytest

from app.services.llm import AgentAdapter

CONFIG={'provider':'fixture','model':'unchanged-model','base_url':'https://fixture.invalid/v1','api_key':'SECRET_KEY'}

def frame(content=None,finish=None,**extra):
    delta={} if content is None else {'content':content}
    delta.update(extra)
    return event({'id':'safe-fixture-id','choices':[{'index':0,'delta':delta,'finish_reason':finish}]})

def event(value):
    return b'data: '+json.dumps(value,ensure_ascii=False).encode()+b'\r\n\r\n'

DONE=b'data: [DONE]\r\n\r\n'

class Body(httpx.AsyncByteStream):
    def __init__(self,chunks,*,stall=False,broken=False):
        self.chunks=chunks;self.stall=stall;self.broken=broken;self.closed=False;self.reads=0
    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads+=1
            yield chunk
        if self.stall:await asyncio.Event().wait()
        if self.broken:raise httpx.ReadError('SECRET_NETWORK_ERROR')
    async def aclose(self):self.closed=True

def install(monkeypatch,body,*,status=200,content_type='text/event-stream',handler=None):
    requests=[];original=httpx.AsyncClient
    async def dispatch(request):
        requests.append(json.loads(request.content))
        if handler:return await handler(request)
        return httpx.Response(status,headers={'content-type':content_type,'x-request-id':'stream-fixture','set-cookie':'SECRET_COOKIE'},stream=body)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(dispatch),**kw))
    return requests

@pytest.mark.asyncio
async def test_stream_opt_in_reassembles_split_utf8_and_retains_only_content_usage(monkeypatch):
    raw=(b': heartbeat\r\n\r\n'+frame(role='assistant')+frame(reasoning_content='SECRET_REASONING')+
         frame('{"answer":"你')+frame('好"}')+frame(finish='stop')+
         event({'choices':[],'usage':{'prompt_tokens':7,'completion_tokens':12,'total_tokens':19,'private':'SECRET_USAGE'}})+DONE)
    body=Body([raw[i:i+1] for i in range(len(raw))]);requests=install(monkeypatch,body)
    adapter=AgentAdapter()
    result=await adapter.chat_with(CONFIG,'exact system','exact user',json_mode=True,timeout=1,stream=True)
    assert result=={'content':'{"answer":"你好"}','usage':{'prompt_tokens':7,'completion_tokens':12,'total_tokens':19}}
    assert requests==[{'model':'unchanged-model','messages':[{'role':'system','content':'exact system'},{'role':'user','content':'exact user'}],
        'temperature':0.2,'response_format':{'type':'json_object'},'stream':True}]
    assert body.closed and adapter.last_error is None and adapter.last_usage==result['usage']
    meta=adapter.last_response_meta
    assert meta['stream_done'] and meta['response_body_complete'] and meta['finish_reason']=='stop'
    assert meta['first_sse_ms']<=meta['first_content_ms']<=meta['stream_done_ms']
    assert meta['output_bytes']==len(result['content'].encode())
    assert 'SECRET' not in json.dumps({'result':result,'meta':meta,'error':adapter.last_error,'usage':adapter.last_usage})

@pytest.mark.asyncio
@pytest.mark.parametrize('raw,code',[
    (frame('{"ok":true}')+frame(finish='stop'),'missing_done'),
    (frame('{"ok":true}')+DONE,'missing_finish'),
    (frame(reasoning_content='SECRET_REASONING')+frame(finish='stop')+DONE,'empty_content'),
    (frame('{"ok":true}')+frame(finish='stop')+b'data: [DONE]','incomplete_event'),
    (b'data: {"SECRET_BAD_JSON"\n\n','invalid_json'),
    (event({'choices':[{'index':1,'delta':{'content':'SECRET_OTHER_CHOICE'},'finish_reason':None}]})+DONE,'invalid_choice'),
    (event({'choices':[{'index':0,'delta':{}},{'index':1,'delta':{}}]})+DONE,'invalid_choice'),
    (frame('x',tool_calls=[{'private':'SECRET_TOOL'}])+DONE,'unsupported_delta'),
    (frame('x',function_call={'private':'SECRET_FUNCTION'})+DONE,'unsupported_delta'),
    (frame('x',refusal='SECRET_REFUSAL')+DONE,'unsupported_delta'),
    (frame('x')+frame(finish='content_filter')+DONE,'unsupported_finish'),
    (frame('x')+frame(finish='tool_calls')+DONE,'unsupported_finish'),
    (frame('x')+frame(finish='SECRET_FINISH')+DONE,'unsupported_finish'),
    (b'data: \xff\n\n','invalid_utf8'),
    (event({'error':{'message':'SECRET_PROVIDER_ERROR'}}),'provider_error'),
    (frame('x')+frame(finish='stop')+frame('more')+DONE,'content_after_finish'),
])
async def test_incomplete_or_ambiguous_stream_never_returns_partial_success(monkeypatch,raw,code):
    body=Body([raw]);requests=install(monkeypatch,body);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user',timeout=1,stream=True) is None
    assert len(requests)==1 and body.closed and not adapter.last_response_meta['response_body_complete']
    assert adapter.last_error=={'kind':'network' if code in {'missing_done','incomplete_event'} else 'invalid_response'}
    assert adapter.last_response_meta['stream_error']==code
    assert 'SECRET' not in json.dumps({'meta':adapter.last_response_meta,'error':adapter.last_error})

@pytest.mark.asyncio
async def test_length_finish_is_not_success_and_remains_available_for_compact_repair(monkeypatch):
    requests=install(monkeypatch,Body([frame('{"partial":')+frame(finish='length')+DONE]));adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user',stream=True) is None
    assert len(requests)==1 and adapter.last_response_meta['finish_reason']=='length'
    assert adapter.last_response_meta['stream_error']=='output_truncated'
    assert not adapter.last_response_meta['response_body_complete']

@pytest.mark.asyncio
async def test_stream_allows_realistic_long_reasoning_without_retaining_it(monkeypatch):
    body=Body([frame(reasoning_content='SECRET_REASONING')]*10000+[frame('{"ok":true}'),frame(finish='stop'),DONE])
    requests=install(monkeypatch,body);adapter=AgentAdapter()
    result=await adapter.chat_with(CONFIG,'system','user',stream=True,timeout=3)
    assert result=={'content':'{"ok":true}'} and adapter.last_usage is None and len(requests)==1
    assert adapter.last_response_meta['sse_event_count']>=10003
    assert 'SECRET_REASONING' not in json.dumps(vars(adapter))

@pytest.mark.asyncio
async def test_stream_reasoning_deltas_surface_via_callback_but_are_never_retained(monkeypatch):
    raw=(frame(role='assistant')+frame(reasoning_content='SECRET')+frame(reasoning_content='_REASONING')+
         frame('ok')+frame(finish='stop')+DONE)
    body=Body([raw[i:i+1] for i in range(len(raw))]);install(monkeypatch,body);adapter=AgentAdapter()
    reasoning=[];contents=[]
    async def on_reasoning(part):reasoning.append(part)
    async def on_content(part):contents.append(part)
    result=await adapter.chat_with(CONFIG,'s','u',stream=True,on_reasoning=on_reasoning,on_content=on_content)
    assert result=={'content':'ok'}
    assert reasoning==['SECRET','_REASONING'] and contents==['ok']
    meta=adapter.last_response_meta
    assert meta['first_reasoning_ms']<=meta['first_content_ms']<=meta['stream_done_ms']
    assert meta['reasoning_chars']==len('SECRET_REASONING')
    assert 'SECRET' not in json.dumps({'meta':meta,'error':adapter.last_error})

@pytest.mark.asyncio
async def test_stream_reasoning_callback_still_bounded_and_absent_without_opt_in(monkeypatch):
    from app.services import llm_stream
    monkeypatch.setattr(llm_stream,'DEFAULT_LIMITS',replace(llm_stream.DEFAULT_LIMITS,content_bytes=32))
    raw=(frame(reasoning_content='x'*40)+frame('ok')+frame(finish='stop')+DONE)
    body=Body([raw]);install(monkeypatch,body);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'s','u',stream=True) is None
    assert adapter.last_response_meta['stream_error']=='content_limit'
    assert adapter.last_response_meta.get('first_reasoning_ms') is None

@pytest.mark.asyncio
@pytest.mark.parametrize('before_headers',[False,True])
async def test_adapter_total_deadline_bounds_stream_without_external_wait_for(monkeypatch,before_headers):
    body=Body([frame(reasoning_content='SECRET_REASONING')],stall=True)
    async def no_headers(request):await asyncio.Event().wait()
    requests=install(monkeypatch,body,handler=no_headers if before_headers else None);adapter=AgentAdapter();started=time.monotonic()
    assert await adapter.chat_with(CONFIG,'system','user',timeout=.04,stream=True) is None
    assert time.monotonic()-started<1 and len(requests)==1
    assert adapter.last_error=={'kind':'timeout'} and not adapter.last_response_meta['response_body_complete']
    if not before_headers:assert body.closed

@pytest.mark.asyncio
async def test_ongoing_heartbeat_does_not_extend_stream_total_deadline(monkeypatch):
    class Heartbeats(Body):
        async def __aiter__(self):
            while True:
                yield b': heartbeat\n\n';await asyncio.sleep(.003)
    body=Heartbeats([]);requests=install(monkeypatch,body);adapter=AgentAdapter();started=time.monotonic()
    assert await adapter.chat_with(CONFIG,'system','user',timeout=.04,stream=True) is None
    assert time.monotonic()-started<1 and body.closed and len(requests)==1
    assert adapter.last_error=={'kind':'timeout'}

@pytest.mark.asyncio
async def test_cancelled_stream_propagates_cancellation_and_closes_without_retry(monkeypatch):
    body=Body([frame('part')],stall=True);requests=install(monkeypatch,body);adapter=AgentAdapter()
    task=asyncio.create_task(adapter.chat_with(CONFIG,'system','user',timeout=1,stream=True))
    for _ in range(100):
        if body.reads:break
        await asyncio.sleep(.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert len(requests)==1 and body.closed and not adapter.last_response_meta['response_body_complete']

@pytest.mark.asyncio
async def test_network_failure_preserves_usage_if_reported_but_never_partial_text(monkeypatch):
    body=Body([frame('SECRET_PARTIAL')+event({'choices':[],'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}})],broken=True)
    requests=install(monkeypatch,body);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user',stream=True) is None
    assert len(requests)==1 and body.closed and adapter.last_error=={'kind':'network'}
    assert adapter.last_usage=={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}
    assert 'SECRET' not in json.dumps(vars(adapter))

@pytest.mark.asyncio
@pytest.mark.parametrize('limit,value,chunks,code',[
    ('event_bytes',32,[b'data: '+b'x'*33,b'\n\n'],'event_limit'),
    ('wire_bytes',64,[b': '+b'x'*63+b'\n\n'],'wire_limit'),
    ('content_bytes',3,[frame('你好')],'content_limit'),
    ('events',2,[b': h\n\n'*3],'event_count_limit'),
])
async def test_stream_limits_apply_before_accumulating_unbounded_lines(monkeypatch,limit,value,chunks,code):
    from app.services import llm_stream
    monkeypatch.setattr(llm_stream,'DEFAULT_LIMITS',replace(llm_stream.DEFAULT_LIMITS,**{limit:value}))
    body=Body(chunks);requests=install(monkeypatch,body);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user',stream=True) is None
    assert len(requests)==1 and body.closed and adapter.last_response_meta['stream_error']==code
    if code=='event_limit':assert body.reads==1,'do not wait for newline before enforcing the event bound'

@pytest.mark.asyncio
@pytest.mark.parametrize('extra,code',[(0,'missing_done'),(1,'event_limit')])
async def test_event_budget_counts_crlf_and_blank_line_bytes(monkeypatch,extra,code):
    from app.services import llm_stream
    monkeypatch.setattr(llm_stream,'DEFAULT_LIMITS',replace(llm_stream.DEFAULT_LIMITS,event_bytes=12))
    # The exact 12-byte event is accepted; a 13-byte event must be rejected.
    raw=b':'+b'x'*(7+extra)+b'\r\n\r\n'
    body=Body([raw]);install(monkeypatch,body);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'s','u',stream=True) is None
    assert adapter.last_response_meta['stream_error']==code

@pytest.mark.asyncio
@pytest.mark.parametrize('complete',[False,True])
async def test_stream_progress_records_safe_last_event_and_pre_content_count(monkeypatch,complete):
    chunks=[frame(role='assistant'),frame(reasoning_content='SECRET_REASONING')]
    if complete:chunks.extend([frame('ok'),frame(finish='stop'),DONE])
    body=Body(chunks,stall=not complete);install(monkeypatch,body);adapter=AgentAdapter()
    result=await adapter.chat_with(CONFIG,'s','u',stream=True,timeout=.04 if not complete else 1)
    assert bool(result)==complete
    meta=adapter.last_response_meta
    assert meta['sse_events_before_content']==2
    assert meta['first_sse_ms']<=meta['last_sse_before_content_ms']<=meta['last_sse_ms']
    assert meta['sse_data_event_count']==(5 if complete else 2)
    if complete:assert meta['last_sse_before_content_ms']<=meta['first_content_ms']<=meta['last_sse_ms']
    else:assert 'first_content_ms' not in meta
    assert 'SECRET' not in json.dumps(vars(adapter))

@pytest.mark.asyncio
async def test_usage_missing_or_untrusted_is_unknown_not_invented_zero(monkeypatch):
    body=Body([frame('ok')+frame(finish='stop')+event({'choices':[],'usage':{'prompt_tokens':True,'completion_tokens':-1,'total_tokens':'SECRET'}})+DONE])
    install(monkeypatch,body);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'s','u',stream=True)=={'content':'ok'}
    assert adapter.last_usage is None and 'SECRET' not in json.dumps(vars(adapter))

@pytest.mark.asyncio
async def test_invalid_unicode_content_has_safe_protocol_diagnostic(monkeypatch):
    body=Body([b'data: {"choices":[{"index":0,"delta":{"content":"\\ud800"},"finish_reason":null}]}\n\n'])
    install(monkeypatch,body);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'s','u',stream=True) is None
    assert adapter.last_response_meta['stream_error']=='invalid_content'

@pytest.mark.asyncio
@pytest.mark.parametrize('status,content_type,kind',[(503,'text/event-stream','http'),(200,'application/json','invalid_response')])
async def test_stream_rejection_does_not_silently_send_nonstream_fallback(monkeypatch,status,content_type,kind):
    requests=install(monkeypatch,Body([b'SECRET_BODY']),status=status,content_type=content_type);adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'system','user',stream=True) is None
    assert len(requests)==1 and adapter.last_error['kind']==kind
    assert 'SECRET' not in json.dumps(vars(adapter))

@pytest.mark.asyncio
async def test_explicit_reasoning_effort_is_preserved_without_stream_options(monkeypatch):
    requests=install(monkeypatch,Body([frame('ok')+frame(finish='stop')+DONE]));adapter=AgentAdapter()
    assert await adapter.chat_with(CONFIG,'s','u',reasoning_effort='low',stream=True)=={'content':'ok'}
    assert requests[0]['reasoning_effort']=='low' and requests[0]['stream'] is True
    assert 'stream_options' not in requests[0] and 'max_tokens' not in requests[0]

@pytest.mark.asyncio
async def test_loopback_http_stream_enforces_idle_timeout_and_one_post(monkeypatch):
    requests=[];finished=asyncio.Event()
    async def stalled(reader,writer):
        try:
            requests.append(await reader.readuntil(b'\r\n\r\n'))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n')
            chunk=frame('partial');writer.write(f'{len(chunk):x}\r\n'.encode()+chunk+b'\r\n');await writer.drain()
            await reader.read()
        finally:writer.close();await writer.wait_closed();finished.set()
    server=await asyncio.start_server(stalled,'127.0.0.1',0);port=server.sockets[0].getsockname()[1]
    original=httpx.AsyncClient
    prepared=original(trust_env=False,timeout=httpx.Timeout(2,read=.03))
    def client(**kw):
        prepared.event_hooks=kw.get('event_hooks',{})
        return prepared
    monkeypatch.setattr(httpx,'AsyncClient',client)
    try:
        adapter=AgentAdapter()
        assert await adapter.chat_with({**CONFIG,'base_url':f'http://127.0.0.1:{port}/v1'},'s','u',timeout=2,stream=True) is None
        await asyncio.wait_for(finished.wait(),1)
        assert len(requests)==1 and adapter.last_error=={'kind':'timeout'}
        assert adapter.last_response_meta['response_headers_received'] and not adapter.last_response_meta['response_body_complete']
        assert adapter.last_response_meta['transport_events']
    finally:server.close();await server.wait_closed()
