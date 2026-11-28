"""Real HTTP dependencies and isolated PostgreSQL, with only synthetic source/grade data."""
import asyncio
import json
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import ApiRatePolicy, get_settings
from app.db import get_session
from app.main import app
from app.models import (
    ContentPublicationRule,
    Evidence,
    ImportItem,
    ImportSession,
    Question,
    ScoringRun,
    TelemetryEvent,
    Trace,
)
from app.models.learning_feedback import LearningActivity, LearningSubmission


@pytest.fixture
def synthetic_input_quota(monkeypatch):
    """Opt-in long boundary scenarios still use the actual durable admission code."""
    cfg = get_settings()
    policy = cfg.api_rate_limits.model_dump()
    for category in ('read', 'write', 'llm'):
        policy[category] = {'burst': 1000, 'per_minute': 10000, 'daily': 10000}
    monkeypatch.setattr(cfg, 'api_rate_limits', ApiRatePolicy.model_validate(policy))


@pytest_asyncio.fixture
async def client(db_session, test_workspace, enrolled_default_owner, synthetic_input_quota, monkeypatch):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1/api/v1',
                                 headers={'X-Api-Token': enrolled_default_owner, 'X-Workspace': test_workspace.slug}) as value:
        yield value
    app.dependency_overrides.pop(get_session, None)


async def counts(session):
    return {model.__name__: await session.scalar(select(func.count()).select_from(model)) for model in (
        ContentPublicationRule, Evidence, ImportItem, ImportSession, Question, ScoringRun, TelemetryEvent, Trace, LearningSubmission,
    )}


@pytest.mark.asyncio
async def test_invalid_requests_have_zero_business_writes_and_keep_http_contract(client, db_session, monkeypatch):
    from app.api.v1 import bank

    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail('invalid boundary input reached business effect')

    monkeypatch.setattr(bank, '_start_import_session', forbidden)
    monkeypatch.setattr(bank, 'generate_answer', forbidden)
    initial = await counts(db_session)
    cases = [
        ('POST', '/bank/imports', {'pasted_text': 'x' * 60001}),
        ('POST', '/bank/imports', {'file_names': ['name'] * 13}),
        ('POST', '/bank/imports/1/approve', {'edits': {'1': {'title': None}}}),
        ('POST', '/bank/imports/1/approve', {'edits': {'1': {'unknown': 1}}}),
        ('POST', '/bank/imports/1/approve', {'edits': {'1': {'difficulty': 1.5}}}),
        ('POST', '/bank/imports/1/approve', {'edits': {'-1': {}}}),
        ('POST', '/learning/activities/1/submissions', {'payload': {'text_answer': 'ok', 'source': 'independent'}, 'request_key': 'bad', 'expected_activity_revision': 1}),
        ('POST', '/learning/activities/1/submissions', {'payload': {'text_answer': 'ok', 'used_hint': 1}, 'request_key': 'bad', 'expected_activity_revision': 1}),
        ('POST', '/learning/activities/1/project-submit', {'snapshot_id': 1, 'request_key': 'bad', 'expected_activity_revision': 1, 'assistance': {'feedback_seen': False}}),
        ('POST', '/telemetry/events', {'client_id': 'fixture-client', 'events': [{'name': 'ok', 'props': {'n': float('inf')}}]}),
        ('POST', '/telemetry/events', {'client_id': 'fixture-client', 'events': [{'name': 'ok', 'props': {'x': [0] * 129}}]}),
        ('PUT', '/public-content/rules', {'key': 'x' * 65, 'value': {}}),
        ('PUT', '/public-content/rules', {'key': 'auto_merge', 'value': {'n': float('nan')}}),
        ('PUT', '/public-content/rules', {'key': 'auto_merge', 'is_enabled': 'false'}),
    ]
    for method, path, body in cases:
        response = await client.request(method, path, content=json.dumps(body), headers={'Content-Type': 'application/json'})
        assert response.status_code == 422, (path, response.text)
        assert isinstance(response.json()['detail'], list)
        assert all('input' not in error for error in response.json()['detail'])
    for raw in (b'{broken', b'\xff', b'{"key":"auto_merge","value":{"n":1e999}}'):
        response = await client.put('/public-content/rules', content=raw, headers={'Content-Type': 'application/json'})
        assert response.status_code in (400, 422)
    assert await counts(db_session) == initial
    assert calls == []
    # Ordinary auth, service errors and unscoped query 422 retain their status.
    response = await client.put('/public-content/rules', json={'key': 'custom'}, headers={'X-Api-Token': 'invalid'})
    assert response.status_code == 401
    response = await client.post('/bank/imports', json={})
    assert response.status_code == 400
    assert (await client.get('/bank?limit=201')).status_code == 422
    assert await counts(db_session) == initial


@pytest.mark.asyncio
async def test_free_json_persists_unchanged_and_rules_upsert_without_publication(client, db_session, test_workspace):
    value = {'policy': ['license', {'allow': True, 'threshold': 0.75}], 'nullable': None, '中文': '保持原样'}
    for key in ('license_policy', 'fact_check_strictness', 'future_custom_rule'):
        response = await client.put('/public-content/rules', json={'key': key, 'value': value, 'is_enabled': False})
        assert response.status_code == 200, response.text
        assert response.json()['value'] == value
        assert response.json()['is_enabled'] is False
        again = await client.put('/public-content/rules', json={'key': key, 'value': value, 'is_enabled': False})
        assert again.json()['id'] == response.json()['id']
    assert await db_session.scalar(select(func.count()).select_from(ContentPublicationRule).where(
        ContentPublicationRule.workspace_id == test_workspace.id)) == 3
    client_id = 'h8-' + uuid4().hex
    body = {'client_id': client_id, 'events': [{'name': 'custom.event', 'props': value}]}
    raw = json.dumps(body, ensure_ascii=False).encode()

    async def chunks():
        yield raw[:11]
        yield raw[11:29]
        yield raw[29:]

    try:
        response = await client.post('/telemetry/events', content=chunks(), headers={'Content-Type': 'application/json', 'Content-Length': '1'})
        assert response.status_code == 204, response.text
        row = await db_session.scalar(select(TelemetryEvent).where(TelemetryEvent.client_id == client_id))
        assert row.props == value
    finally:
        await db_session.execute(delete(TelemetryEvent).where(TelemetryEvent.client_id == client_id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_approval_string_ids_partial_nulls_and_missing_all_semantics(client, db_session, test_workspace, monkeypatch):
    from test_bank_review_actions import raw_draft

    from app.api.v1 import bank

    async def no_provider(*args, **kwargs):
        pytest.fail('approval of complete existing content must not generate')

    monkeypatch.setattr(bank, 'generate_answer', no_provider)
    imported, item = await raw_draft(db_session, test_workspace)
    path = f'/bank/imports/{imported.id}/approve'
    assert (await client.post(path, json={'item_ids': []})).json()['created'] == []
    rubric = {'points': [{'title': '保留原标准', 'detail': '实际规则', 'keywords': ['test']}], 'max_points': 5.0,
              'extensions': {'custom': True}}
    response = await client.post(path, json={'edits': {str(item.id): {'rubric': rubric, 'company': None, 'options': None}}})
    assert response.status_code == 200, response.text
    code = response.json()['created'][0]
    question = await db_session.scalar(select(Question).where(Question.short_code == code))
    assert question.title == item.title and question.answer_html == item.draft_answer
    assert question.rubric == rubric and question.company is None and question.options is None
    assert question.generation_meta['source_item_id'] == item.id
    assert question.generation_meta['source_files'] == ['面经.md']
    assert (await client.post(path, json={'item_ids': None})).json()['created'] == []
    assert (await client.post('/bank/imports/2147483647/approve', json={})).status_code == 404


@pytest.mark.asyncio
async def test_submission_legacy_fingerprint_aliases_server_help_and_ownership(client, db_session, test_workspace, monkeypatch):
    from test_learning_feedback import task_with_valid_origin

    from app.services.learning_feedback import prepare_path, record_assistance, submit_activity

    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    prepared = await prepare_path(db_session, test_workspace, task.id)
    activity = await db_session.get(LearningActivity, prepared['activities'][0]['id'])
    await record_assistance(db_session, test_workspace, activity.id, 'REFERENCE', 'known', activity.version)
    payload = {'answer_text': '  原答不能 trim\n', 'used_hint': True,
               'assistance': {'viewed_answer': False, 'ai_assisted': True}}
    # Seed exactly the legacy service fingerprint, then retry through the new HTTP schema.
    old = await submit_activity(db_session, test_workspace, activity, payload, 'legacy-replay', activity.version)
    await db_session.commit()
    body = {'payload': payload, 'request_key': 'legacy-replay', 'expected_activity_revision': activity.version}
    response = await client.post(f'/learning/activities/{activity.id}/submissions', json=body)
    assert response.status_code == 202, response.text
    assert response.json()['submission_id'] == old.id
    old_id, activity_id = old.id, activity.id
    await db_session.refresh(old)
    saved = await db_session.get(LearningSubmission, old_id)
    assert saved.payload == payload
    assert saved.assistance['reference_viewed'] is True
    assert saved.assistance['hint_used'] is True and saved.assistance['ai_declared'] is True
    assert (await client.post(f'/learning/activities/{activity_id}/submissions', json={
        **body, 'payload': {**payload, 'answer_text': 'different'}})).status_code == 409
    assert (await client.post('/learning/activities/2147483647/submissions', json=body)).status_code == 404
    assert (await client.post(f'/learning/activities/{activity_id}/submissions', json=body,
                              headers={'X-Workspace': 'nonexistent-h8'})).status_code == 404
    assert await db_session.scalar(select(func.count()).select_from(LearningSubmission).where(
        LearningSubmission.workspace_id == test_workspace.id)) == 1
    response = await client.post(f'/learning/activities/{activity_id}/submissions', json={
        'payload': {'text_answer': '  新的原始答题\n'}, 'request_key': 'new-http',
        'expected_activity_revision': body['expected_activity_revision'], 'parent_submission_id': old_id})
    assert response.status_code == 202, response.text
    fresh = await db_session.get(LearningSubmission, response.json()['submission_id'])
    assert fresh.payload == {'text_answer': '  新的原始答题\n'}
    assert fresh.assistance['reference_viewed'] is True


@pytest.mark.asyncio
async def test_paste_import_old_request_creates_session_and_multipart_is_separate(client, db_session, monkeypatch):
    from app.api.v1 import bank
    calls = []

    async def synthetic_pipeline(import_id, workspace_id, **kwargs):
        calls.append((import_id, workspace_id, kwargs['paste_text']))

    monkeypatch.setattr(bank, '_process_import', synthetic_pipeline)
    response = await client.post('/bank/imports', json={'pasted_text': '  合法面经\n', 'file_names': None})
    assert response.status_code == 200, response.text
    await asyncio.gather(*tuple(bank._BG_IMPORT_TASKS))
    imported = await db_session.get(ImportSession, response.json()['id'])
    assert imported.paste_text == '合法面经' and calls[0][0] == imported.id
    assert (await client.post('/bank/imports', json={'pasted_text': None, 'file_names': []})).status_code == 400


@pytest.mark.asyncio
async def test_bank_page_cap_and_complete_owned_inventory(client, db_session, test_workspace):
    prefix = 'h8-' + uuid4().hex[:10]
    rows = [Question(short_code=f'{prefix}-{i}', workspace_id=test_workspace.id, title=f'H8 sentinel {i}',
                     topic='rag', source_type='USER_PRIVATE', qtype='open', difficulty=3) for i in range(405)]
    db_session.add_all(rows)
    await db_session.commit()
    ids = []
    for offset in (0, 200, 400):
        response = await client.get(f'/bank?scope=mine&limit=200&offset={offset}')
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['total'] == 405
        assert len(page['items']) == min(200, 405 - offset)
        ids.extend(item['id'] for item in page['items'])
    assert ids == [row.id for row in rows]
    assert (await client.get('/bank?limit=5000')).status_code == 422


@pytest.mark.asyncio
async def test_asgi_missing_and_false_lengths_write_nothing(client, db_session):
    from test_bounded_inputs import asgi_call

    before = await counts(db_session)
    for headers in ((), ((b'content-length', b'1'),), ((b'transfer-encoding', b'chunked'),)):
        headers = (*headers, (b'origin', b'http://127.0.0.1:5173'), (b'accept-language', b'zh-CN'))
        status, messages, reads = await asgi_call(app, [b' ' * 65536, b' ' * 65536, b'x', b'unread'], headers=headers)
        assert (status, reads) == (413, 3)
        response_headers = dict(messages[0]['headers'])
        assert response_headers[b'x-request-id']
        assert response_headers[b'content-language'] == b'zh-CN'
        assert response_headers[b'access-control-allow-origin'] == b'http://127.0.0.1:5173'
    deep = ('{"x":' + '[' * 1500 + '0' + ']' * 1500 + '}').encode()
    status, messages, _ = await asgi_call(app, [deep], headers=((b'origin', b'http://127.0.0.1:5173'),))
    assert status == 422
    assert dict(messages[0]['headers'])[b'x-request-id']
    assert dict(messages[0]['headers'])[b'access-control-allow-origin'] == b'http://127.0.0.1:5173'
    assert await counts(db_session) == before


@pytest.mark.asyncio
async def test_early_rejection_still_consumes_existing_e3_quota(client, db_session, monkeypatch):
    from app.models.api_rate_limit import ApiRateBucket
    cfg = get_settings()
    policy = cfg.api_rate_limits.model_dump()
    policy['write'] = {'burst': 1, 'per_minute': 1, 'daily': 1}
    monkeypatch.setattr(cfg, 'api_rate_limits', ApiRatePolicy.model_validate(policy))
    before = await counts(db_session)
    headers = {'Origin': 'http://127.0.0.1:5173', 'Content-Type': 'application/json'}
    response = await client.put('/public-content/rules', content=b' ' * (512 * 1024 + 1), headers=headers)
    assert response.status_code == 413
    assert response.headers['x-request-id'] and response.headers['access-control-allow-origin'] == headers['Origin']
    response = await client.put('/public-content/rules', json={'key': 'custom'}, headers=headers)
    assert response.status_code == 429, response.text
    assert int(response.headers['retry-after']) > 0 and response.headers['x-request-id']
    assert await db_session.scalar(select(func.count()).select_from(ApiRateBucket).where(
        ApiRateBucket.category == 'write')) >= 1
    assert await counts(db_session) == before
