"""H8 public input contracts, without external providers or personal data."""
import asyncio
import json

import pytest
from pydantic import ValidationError

from app.api.v1.bank import ApproveItemReq, StartImportReq
from app.api.v1.learning_feedback import SubmissionIn
from app.api.v1.learning_projects import ProjectSubmitIn
from app.api.v1.public_content import RuleIn
from app.api.v1.telemetry import TelemetryItem


@pytest.mark.parametrize(('model', 'body'), [
    (ApproveItemReq, {'edits': {'1': {'unknown': 'ignored'}}}),
    (ApproveItemReq, {'edits': {'0': {'title': 'bad id'}}}),
    (ApproveItemReq, {'edits': {'1': {'difficulty': True}}}),
    (ApproveItemReq, {'edits': {'1': {'title': 'x' * 2001}}}),
    (ApproveItemReq, {'item_ids': [True]}),
    (ApproveItemReq, {'item_ids': list(range(1, 202))}),
    (StartImportReq, {'pasted_text': 'x' * 60001}),
    (StartImportReq, {'file_names': ['a'] * 13}),
    (StartImportReq, {'file_names': ['x' * 256]}),
    (SubmissionIn, {'payload': {'text_answer': 'ok', 'project_contract': {}}, 'request_key': 'a', 'expected_activity_revision': 1}),
    (SubmissionIn, {'payload': {'text_answer': 'ok', 'assistance': {'ai_assisted': 1}}, 'request_key': 'a', 'expected_activity_revision': 1}),
    (SubmissionIn, {'payload': {'text_answer': 3}, 'request_key': 'a', 'expected_activity_revision': 1}),
    (ProjectSubmitIn, {'snapshot_id': 1, 'request_key': 'a', 'expected_activity_revision': 1, 'assistance': {'ai_declared': 'false'}}),
    (ProjectSubmitIn, {'snapshot_id': 1, 'request_key': 'a', 'expected_activity_revision': 1, 'assistance': {'independent': True}}),
    (TelemetryItem, {'name': 'ok', 'props': {'nested': [[[[[[[[[1]]]]]]]]]}}),
    (TelemetryItem, {'name': 'ok', 'props': {'value': float('inf')}}),
    (TelemetryItem, {'name': 'ok', 'props': {'x' * 129: 1}}),
    (TelemetryItem, {'name': 'ok', 'props': {'x': 'x' * 8193}}),
    (RuleIn, {'key': 'custom', 'value': {'x': [0] * 129}}),
    (RuleIn, {'key': 'custom', 'value': {'x': float('nan')}}),
    (RuleIn, {'key': 'x' * 129, 'is_enabled': 1}),
])
def test_invalid_inputs_are_rejected(model, body):
    with pytest.raises(ValidationError):
        model.model_validate(body)


def test_old_shapes_preserve_absence_null_aliases_and_json_keys():
    for ids in (None, []):
        assert ApproveItemReq(item_ids=ids).item_ids == ids
    edits = {'123': {'title': '原题', 'options': None, 'company': None,
                     'rubric': {'points': [{'title': '标准', 'keywords': ['yes']}], 'max_points': 5.0,
                                'criteria': [{'id': 'C1', 'weight': 1.0}], 'extensions': {'allowed': True}}}}
    parsed = ApproveItemReq.model_validate({'edits': edits})
    assert parsed.edits[123].model_dump(exclude_unset=True) == edits['123']
    for payload in ({'text_answer': ' 原始答案\n'}, {'answer_text': ' legacy ', 'used_hint': True},
                    {'text_answer': 'ok', 'assistance': {'viewed_answer': True, 'ai_assisted': False}},
                    {'text_answer': 'ok', 'assistance': None}):
        body = SubmissionIn(payload=payload, request_key='stable', expected_activity_revision=1)
        assert body.payload.model_dump(exclude_unset=True) == payload
    assert ProjectSubmitIn(snapshot_id=1, request_key='a', expected_activity_revision=1).assistance.model_dump(exclude_unset=True) == {}


@pytest.mark.parametrize('value', [False, True, 0, 1, 'true', 'false', None])
@pytest.mark.parametrize('alias', ['hint_used', 'used_hint', 'reference_viewed', 'viewed_answer', 'ai_declared', 'ai_assisted'])
def test_every_assistance_alias_is_a_strict_boolean(alias, value):
    from app.input_contracts import AssistanceDeclarations
    if type(value) is bool:
        assert AssistanceDeclarations.model_validate({alias: value}).model_dump(exclude_unset=True) == {alias: value}
    else:
        with pytest.raises(ValidationError):
            AssistanceDeclarations.model_validate({alias: value})


@pytest.mark.parametrize('props', [
    {str(i): i for i in range(129)},
    {str(i): list(range(128)) for i in range(9)},
    {'n': float('-inf')}, {'bad': object()},
])
def test_json_container_and_total_node_limits(props):
    for model, body in ((TelemetryItem, {'name': 'ok', 'props': props}), (RuleIn, {'key': 'custom', 'value': props})):
        with pytest.raises(ValidationError):
            model.model_validate(body)


@pytest.mark.parametrize('key', ['-1', '01', '1.0', '+1', 'true', '2147483648'])
def test_noncanonical_item_keys_are_rejected(key):
    with pytest.raises(ValidationError):
        ApproveItemReq.model_validate({'edits': {key: {}}})


async def asgi_call(application, chunks, *, path='/api/v1/telemetry/events', method='POST', headers=()):
    messages = []
    reads = 0

    async def receive():
        nonlocal reads
        index = reads
        reads += 1
        if index < len(chunks):
            return {'type': 'http.request', 'body': chunks[index], 'more_body': index < len(chunks) - 1}
        return {'type': 'http.disconnect'}

    async def send(message):
        messages.append(message)

    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'scheme': 'http',
             'method': method, 'path': path, 'raw_path': path.encode(), 'query_string': b'',
             'root_path': '', 'headers': [(b'content-type', b'application/json'), *headers],
             'client': ('127.0.0.1', 1234), 'server': ('127.0.0.1', 80)}
    await application(scope, receive, send)
    status = next((m['status'] for m in messages if m['type'] == 'http.response.start'), None)
    return status, messages, reads


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [(), ((b'content-length', b'1'),), ((b'transfer-encoding', b'chunked'),), ((b'content-length', b'999999999'),)])
async def test_actual_received_chunks_reject_before_app_or_unbounded_tail(headers):
    from app.api.input_boundary import JsonInputBoundary

    calls = []

    async def forbidden(*args):
        calls.append(True)

    chunks = [b' ' * 65536, b' ' * 65536, b' ', b'unread-tail']
    status, _, reads = await asgi_call(JsonInputBoundary(forbidden), chunks, headers=headers)
    assert (status, reads, calls) == (413, 3, [])


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'cap'), [
    ('POST', '/api/v1/bank/imports', 2 * 1024 * 1024),
    ('POST', '/api/v1/bank/imports/+123/approve', 2 * 1024 * 1024),
    ('POST', '/api/v1/learning/activities/-1/submissions', 512 * 1024),
    ('POST', '/api/v1/learning/activities/123/project-submit/', 512 * 1024),
    ('PUT', '/api/v1/public-content/rules', 512 * 1024),
])
async def test_all_scoped_json_routes_are_received_byte_bounded(method, path, cap):
    from app.api.input_boundary import JsonInputBoundary

    async def forbidden(*args):
        pytest.fail('oversize input reached business app')

    assert (await asgi_call(JsonInputBoundary(forbidden), [b' ' * cap, b'x'], method=method, path=path))[0] == 413


@pytest.mark.asyncio
@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-32'])
async def test_deep_raw_json_is_safe_before_decoder_recursion(encoding):
    from app.api.input_boundary import JsonInputBoundary

    async def forbidden(*args):
        pytest.fail('deep input reached JSON decoder')

    raw = ('{"client_id":"fixture-client","events":[],"deep":' + '[' * 1500 + '0' + ']' * 1500 + '}').encode(encoding)
    assert (await asgi_call(JsonInputBoundary(forbidden), [raw[:17], raw[17:]]))[0] == 422


@pytest.mark.asyncio
async def test_valid_chunk_replay_exclusions_disconnect_and_cancellation():
    from starlette.requests import Request
    from starlette.responses import Response

    from app.api.input_boundary import JsonInputBoundary

    calls = []

    async def downstream(scope, receive, send):
        calls.append(await Request(scope, receive).body())
        await Response(b'unchanged', media_type='text/event-stream')(scope, receive, send)

    boundary = JsonInputBoundary(downstream)
    raw = json.dumps({'escaped': '"\\' + '[' * 100, 'unicode': '中文'}).encode()
    assert (await asgi_call(boundary, [raw[:9], b'', raw[9:]]))[0] == 200
    assert calls == [raw]
    # A binary upload and a streaming response remain outside the JSON cap.
    large = b'x' * (2 * 1024 * 1024 + 1)
    assert (await asgi_call(boundary, [large], path='/api/v1/bank/imports/upload'))[0] == 200
    assert calls[-1] == large
    assert (await asgi_call(boundary, []))[0] is None
    count = len(calls)

    async def cancelled():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await boundary({'type': 'http', 'method': 'POST', 'path': '/api/v1/telemetry/events'}, cancelled, None)
    assert len(calls) == count
