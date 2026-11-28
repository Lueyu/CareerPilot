"""Public model tests against isolated PostgreSQL and controlled OpenAI-compatible HTTP."""
import asyncio
import json
import socket
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
import uvicorn
from conftest import TEST_DATABASE_URL, prepare_schema
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from model_transport_fixtures import install_model_transport
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app.config import get_settings
from app.models.public_model import PublicModelCall, PublicModelMonth
from app.models.workspace import Workspace
from app.services import public_model as model

MESSAGES = [{"role": "user", "content": "问题"}]
ANSWER = "测试答复"
USAGE = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
CHINA = timezone(timedelta(hours=8))
TODAY = datetime(2026, 11, 30, 23, 59, 58, tzinfo=CHINA)


@pytest_asyncio.fixture
async def ledger(monkeypatch):
    await prepare_schema()
    schema = "seed_model_" + uuid4().hex
    engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=NullPool,
        execution_options={"schema_translate_map": {None: schema}},
    )
    async with engine.begin() as connection:
        await connection.execute(CreateSchema(schema))
        tables = [Workspace.__table__, PublicModelCall.__table__, PublicModelMonth.__table__]
        await connection.run_sync(lambda db: Workspace.metadata.create_all(db, tables=tables))

    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(model, "SessionLocal", sessions)
    monkeypatch.setattr(model, "_now", lambda: TODAY)
    async with sessions.begin() as session:
        session.add_all(
            Workspace(id=workspace_id, slug=f"test-{workspace_id}", name="Synthetic model workspace")
            for workspace_id in (1, 2)
        )
    try:
        yield sessions
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


@pytest.fixture(autouse=True)
def seed_config(monkeypatch):
    for name, value in {
        "SEED_MODEL_BASE_URL": "https://provider.example/v1",
        "SEED_MODEL_KEY": "synthetic-key",
        "SEED_MODEL_NAME": "synthetic-reasoner",
        "SEED_MODEL_THINKING": "",
        "SEED_INPUT_CNY_PER_MILLION": "1000",
        "SEED_OUTPUT_CNY_PER_MILLION": "2000",
        "SEED_MONTHLY_CNY": "50",
        "SEED_DAILY_USER_CNY": "10",
        "SEED_LOCAL_UNMETERED": "false",
        "SEED_PUBLIC_ORIGIN": "https://preview.example",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def mock_provider(monkeypatch):

    def install(handler):
        install_model_transport(monkeypatch, httpx.MockTransport(handler))

    return install


def completion(usage=USAGE):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": ANSWER, "reasoning_content": "hidden reasoning"}}],
            "usage": usage,
        },
    )


async def answer(workspace_id, key):
    return await model.complete_text(workspace_id, key, MESSAGES, max_tokens=100)


async def stream_answer(workspace_id, key, *, max_tokens=100):
    return [fragment async for fragment in model.stream_text(
        workspace_id, key, MESSAGES, max_tokens=max_tokens
    )]


async def charges(ledger):
    async with ledger() as session:
        calls = (await session.scalars(select(PublicModelCall).order_by(PublicModelCall.workspace_id))).all()
        months = (await session.scalars(select(PublicModelMonth).order_by(PublicModelMonth.month))).all()
    return calls, months


@pytest.mark.asyncio
async def test_completion_charges_provider_usage_and_replays_in_both_modes(ledger, mock_provider):
    requests = []

    def provider(request):
        requests.append(request)
        return completion()

    mock_provider(provider)
    assert await answer(1, "shared-key") == ANSWER
    assert await answer(1, "shared-key") == ANSWER
    assert await stream_answer(1, "shared-key") == [ANSWER]
    assert await answer(2, "shared-key") == ANSWER
    assert len(requests) == 2
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer synthetic-key"
    assert json.loads(requests[0].content) == {
        "model": "synthetic-reasoner",
        "messages": MESSAGES,
        "max_tokens": 100,
        "stream": False,
    }

    calls, months = await charges(ledger)
    assert [(call.prompt_tokens, call.completion_tokens) for call in calls] == [(10, 20), (10, 20)]
    assert [call.reserved_nano_cny for call in calls] == [526_000_000, 526_000_000]
    assert [call.charged_nano_cny for call in calls] == [50_000_000, 50_000_000]
    assert months[0].charged_nano_cny == 100_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "value"), [
    ("SEED_MODEL_KEY", ""),
    ("SEED_INPUT_CNY_PER_MILLION", ""),
    ("SEED_INPUT_CNY_PER_MILLION", "0"),
    ("SEED_OUTPUT_CNY_PER_MILLION", "NaN"),
    ("SEED_DAILY_USER_CNY", ""),
    ("SEED_MODEL_BASE_URL", "http://provider.example/v1"),
])
async def test_bad_config_fails_closed(ledger, monkeypatch, mock_provider, name, value):
    monkeypatch.setenv(name, value)
    mock_provider(lambda request: pytest.fail("Invalid config reached provider"))
    with pytest.raises(model.PublicModelError) as error:
        await answer(1, "denied")
    assert error.value.status_code == 503
    assert await charges(ledger) == ([], [])


@pytest.mark.asyncio
async def test_oversized_input_fails_before_reservation(ledger, mock_provider):
    mock_provider(lambda request: pytest.fail("Oversized request reached provider"))
    with pytest.raises(model.PublicModelError) as error:
        await model.complete_text(1, "large", [{"role": "user", "content": "中" * 40000}])
    assert error.value.status_code == 422
    assert await charges(ledger) == ([], [])


@pytest.mark.asyncio
async def test_thinking_override_and_reasoning_only_result(ledger, monkeypatch, mock_provider):
    monkeypatch.setenv("SEED_MODEL_THINKING", "disabled")

    def provider(request):
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "disabled"}
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None, "reasoning_content": "private scratchpad"}}],
            "usage": USAGE,
        })

    mock_provider(provider)
    with pytest.raises(model.PublicModelError) as error:
        await answer(1, "reasoning-only")
    assert error.value.status_code == 502
    assert "private scratchpad" not in error.value.detail
    calls, months = await charges(ledger)
    assert calls[0].result is None
    assert calls[0].charged_nano_cny == months[0].charged_nano_cny == 50_000_000


@pytest.mark.asyncio
async def test_concurrent_site_limit_pending_replay_and_refund(ledger, monkeypatch, mock_provider):
    monkeypatch.setenv("SEED_MONTHLY_CNY", "0.6")
    monkeypatch.setattr(model, "_now", lambda: TODAY - timedelta(days=2))
    started = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def provider(request):
        nonlocal count
        count += 1
        started.set()
        await release.wait()
        return completion()

    mock_provider(provider)
    task = asyncio.create_task(answer(1, "first"))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        with pytest.raises(model.PublicModelError) as duplicate:
            await stream_answer(1, "first")
        assert duplicate.value.status_code == 409
        with pytest.raises(model.PublicModelError) as exhausted:
            await answer(2, "second")
        assert exhausted.value.status_code == 429
        assert exhausted.value.headers["Retry-After"] == "172802"
        assert "2026-12-01 00:00" in exhausted.value.detail
        assert count == 1
    finally:
        release.set()
        assert await task == ANSWER
    assert await answer(2, "second") == ANSWER
    assert count == 2


@pytest.mark.asyncio
async def test_daily_quota_resets_and_account_delete_does_not_refund_site(
    ledger, monkeypatch, mock_provider
):
    monkeypatch.setenv("SEED_DAILY_USER_CNY", "0.6")
    monkeypatch.setattr(model, "_now", lambda: TODAY - timedelta(days=1))
    mock_provider(lambda request: completion(None))
    assert await answer(1, "first") == ANSWER
    assert await answer(2, "first") == ANSWER
    with pytest.raises(model.PublicModelError) as exhausted:
        await answer(1, "next")
    assert exhausted.value.status_code == 429
    assert exhausted.value.headers["Retry-After"] == "2"
    assert "2026-11-30 00:00" in exhausted.value.detail
    monkeypatch.setattr(model, "_now", lambda: TODAY)
    assert await answer(1, "next") == ANSWER

    async with ledger.begin() as session:
        await session.execute(delete(Workspace).where(Workspace.id == 1))
    calls, months = await charges(ledger)
    assert [call.workspace_id for call in calls] == [2]
    assert calls[0].prompt_tokens is None
    assert calls[0].charged_nano_cny == 526_000_000
    assert months[0].charged_nano_cny == 1_578_000_000


@pytest.mark.asyncio
async def test_month_rollover_keeps_old_reservations_in_their_month(ledger, monkeypatch, mock_provider):
    monkeypatch.setenv("SEED_MONTHLY_CNY", "0.6")
    mock_provider(lambda request: completion(None))
    assert await answer(1, "november") == ANSWER
    with pytest.raises(model.PublicModelError) as exhausted:
        await answer(2, "december")
    assert exhausted.value.status_code == 429
    monkeypatch.setattr(model, "_now", lambda: TODAY + timedelta(seconds=2))
    assert await answer(2, "december") == ANSWER
    _, months = await charges(ledger)
    assert [(month.month, month.charged_nano_cny) for month in months] == [
        (date(2026, 11, 1), 526_000_000),
        (date(2026, 12, 1), 526_000_000),
    ]


@pytest.mark.asyncio
async def test_default_monthly_cap_is_fifty_cny(ledger, monkeypatch, mock_provider):
    monkeypatch.delenv("SEED_MONTHLY_CNY")
    monkeypatch.setenv("SEED_DAILY_USER_CNY", "100")
    monkeypatch.setenv("SEED_OUTPUT_CNY_PER_MILLION", "10000")
    mock_provider(lambda request: completion())
    assert await model.complete_text(1, "fits", MESSAGES, max_tokens=4000) == ANSWER
    with pytest.raises(model.PublicModelError) as exhausted:
        await model.complete_text(2, "over-default", MESSAGES, max_tokens=5000)
    assert exhausted.value.status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [
    "https://127.0.0.1:4100",
    "http://localhost:4100",
    "http://preview.example",
    "http://127.0.0.1:4100/preview",
    "http://127.0.0.1@preview.example",
])
async def test_unmetered_requires_literal_loopback_http(ledger, monkeypatch, mock_provider, origin):
    monkeypatch.setenv("SEED_LOCAL_UNMETERED", "true")
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", origin)
    mock_provider(lambda request: pytest.fail("Invalid preview origin reached provider"))
    with pytest.raises(model.PublicModelError) as error:
        await answer(1, "denied")
    assert error.value.status_code == 503
    assert await charges(ledger) == ([], [])


def sse(event):
    return ("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n").encode("utf-8")


@pytest_asyncio.fixture
async def sse_provider(monkeypatch):
    app = FastAPI()
    finished = asyncio.Event()
    first_fragment = asyncio.Event()
    requests = []
    usage = {"prompt_tokens": 100, "completion_tokens": 3200, "total_tokens": 3300}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        requests.append(await request.json())

        async def events():
            yield b": keep-alive\r\n\r\n"
            yield sse({"choices": [{"delta": {"reasoning_content": "private scratchpad"}}]})
            first = sse({"choices": [{"delta": {"content": "答"}}]})
            split = first.index("答".encode("utf-8")) + 1
            yield first[:split]
            yield first[split:]
            first_fragment.set()
            await finished.wait()
            yield sse({"choices": [{"delta": {"content": "复"}}]})
            yield sse({"choices": [], "usage": usage})
            yield b"data: [DONE]\r\n\r\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    monkeypatch.setattr(get_settings(), "model_local_origins", [origin])
    monkeypatch.setenv("SEED_MODEL_BASE_URL", origin + "/v1")
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        yield requests, first_fragment, finished
    finally:
        finished.set()
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        sock.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unmetered", [False, True])
async def test_loopback_sse_yields_text_and_records_reasoning_usage(
    ledger, sse_provider, monkeypatch, unmetered
):
    if unmetered:
        monkeypatch.setenv("SEED_LOCAL_UNMETERED", "true")
        monkeypatch.setenv("SEED_PUBLIC_ORIGIN", "http://[::1]:4100")
        monkeypatch.setenv("SEED_MODEL_THINKING", "disabled")
        monkeypatch.delenv("SEED_INPUT_CNY_PER_MILLION")
        monkeypatch.delenv("SEED_OUTPUT_CNY_PER_MILLION")
        monkeypatch.setenv("SEED_DAILY_USER_CNY", "0")
        monkeypatch.setenv("SEED_MONTHLY_CNY", "0")
    else:
        monkeypatch.setenv("SEED_DAILY_USER_CNY", "20")

    requests, first_fragment, finished = sse_provider
    async with asyncio.timeout(20):
        stream = model.stream_text(1, "live", MESSAGES, max_tokens=6000)
        try:
            assert await anext(stream) == "答"
            await first_fragment.wait()
            assert len(requests) == 1
            assert requests[0]["stream_options"] == {"include_usage": True}
            assert requests[0]["max_tokens"] == 6000
            assert requests[0].get("thinking") == ({"type": "disabled"} if unmetered else None)
            finished.set()
            assert [part async for part in stream] == ["复"]
        finally:
            await stream.aclose()
    assert await answer(1, "live") == "答复"

    calls, months = await charges(ledger)
    assert len(calls) == 1
    call = calls[0]
    assert (call.prompt_tokens, call.completion_tokens) == (100, 3200)
    if unmetered:
        assert call.reserved_nano_cny is None and call.charged_nano_cny is None
        assert months == []
    else:
        assert call.reserved_nano_cny == 12_326_000_000
        assert call.charged_nano_cny == months[0].charged_nano_cny == 6_500_000_000


@pytest.mark.asyncio
async def test_stream_cancellation_keeps_reservation(ledger, sse_provider):
    requests, first_fragment, _ = sse_provider
    stream = model.stream_text(1, "cancelled", MESSAGES, max_tokens=100)
    try:
        assert await asyncio.wait_for(anext(stream), timeout=20) == "答"
        await asyncio.wait_for(first_fragment.wait(), timeout=5)
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await stream.aclose()
    with pytest.raises(model.PublicModelError) as duplicate:
        await answer(1, "cancelled")
    assert duplicate.value.status_code == 409
    calls, months = await charges(ledger)
    assert len(requests) == len(calls) == 1
    assert calls[0].result is None and calls[0].completion_tokens is None
    assert calls[0].charged_nano_cny == months[0].charged_nano_cny == 526_000_000


@pytest.mark.asyncio
async def test_upstream_error_is_bounded_and_preserves_charge(ledger, mock_provider):
    mock_provider(lambda request: httpx.Response(500, text="secret upstream diagnostic"))
    with pytest.raises(model.PublicModelError) as error:
        await answer(1, "failed")
    assert error.value.status_code == 502
    assert "secret" not in error.value.detail
    calls, months = await charges(ledger)
    assert calls[0].prompt_tokens is None
    assert calls[0].charged_nano_cny == months[0].charged_nano_cny == 526_000_000


@pytest.mark.asyncio
async def test_zero_completion_tokens_with_answer_retains_reservation(ledger, mock_provider):
    mock_provider(lambda request: completion({"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10}))
    assert await answer(1, "unreported") == ANSWER
    calls, months = await charges(ledger)
    assert calls[0].completion_tokens is None
    assert calls[0].charged_nano_cny == months[0].charged_nano_cny == 526_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_registered_real_policy_denial_is_terminal_safe_and_keeps_reserve(ledger, monkeypatch, stream):
    from unittest.mock import AsyncMock

    from app.services import model_outbound, public_coach
    resolver = AsyncMock(return_value=("93.184.216.34", "127.0.0.1"))
    monkeypatch.setattr(model_outbound, "_resolve", resolver)
    dial = AsyncMock()
    monkeypatch.setattr(model_outbound.AnyIOBackend, "connect_tcp", dial)
    with pytest.raises(model.PublicModelError) as error:
        if stream:
            await stream_answer(1, "outbound-denied")
        else:
            await answer(1, "outbound-denied")
    assert error.value.detail == {"code": "MODEL_OUTBOUND_POLICY", "message": model_outbound.SAFE_MESSAGE}
    assert public_coach._public_error(error.value) == model_outbound.SAFE_MESSAGE
    assert "127.0" not in str(error.value.detail) and "synthetic-key" not in str(error.value.detail)
    assert resolver.await_count == 1
    dial.assert_not_called()
    calls, months = await charges(ledger)
    assert len(calls) == 1 and calls[0].result is None
    assert calls[0].charged_nano_cny == calls[0].reserved_nano_cny == months[0].charged_nano_cny


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_registered_real_local_origin_uses_shared_backend(ledger, monkeypatch, stream):
    from test_model_outbound_policy import local_provider
    async with local_provider(monkeypatch) as (url, requests):
        monkeypatch.setenv("SEED_MODEL_BASE_URL", url)
        result = await stream_answer(1, "local-outbound") if stream else await answer(1, "local-outbound")
        assert result == (["OK"] if stream else "OK")
        assert len(requests) == 1 and b"Authorization: Bearer synthetic-key" in requests[0][0]
        assert b"Host: local.example:" in requests[0][0]
    calls, months = await charges(ledger)
    assert calls[0].prompt_tokens == 3 and calls[0].completion_tokens == 2
