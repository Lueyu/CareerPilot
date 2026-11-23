"""Durable provider call records and attempt-aware Stop."""
import asyncio

import httpx
import pytest
from fastapi import BackgroundTasks, FastAPI
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_llm_stream_transport import CONFIG, DONE, Body, event, frame, install

from app.api.v1 import study_chat as api
from app.models.study_chat import StudyMessage
from app.services import study_chat as worker


@pytest.fixture
def generation(monkeypatch, db_session):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    settings = worker.get_settings().model_copy(update={"study_strong_model": "", "study_timeout_seconds": 60})
    monkeypatch.setattr(worker, "get_settings", lambda: settings)

    async def config():
        return CONFIG

    monkeypatch.setattr(worker, "get_llm_config", config)
    return factory


async def accepted(db, ws, action="explain"):
    sid = (await api.create(api.StartSession(mode="auto"), db, ws))["id"]
    tasks = BackgroundTasks()
    ticket = await api.send(sid, api.NewMessage(request_id="intent", content="做一个重试示例", action=action), tasks, db, ws)
    return sid, ticket["assistant_message_id"], tasks


def intent(call):
    return tuple(call[field] for field in ("attempt", "generation", "provider", "requested_model", "outcome", "usage"))


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", ["", "已保存的正文"])
async def test_provider_intent_survives_expiry_and_manual_retry(monkeypatch, generation, db_session, test_workspace, partial):
    db, ws = db_session, test_workspace
    entered = asyncio.Event()

    class Partial(Body):
        async def __aiter__(self):
            yield frame(partial)
            entered.set()
            await asyncio.Event().wait()

    async def transport(_request):
        if partial:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Partial([]))
        entered.set()
        await asyncio.Event().wait()

    requests = install(monkeypatch, None, handler=transport)
    sid, rid, tasks = await accepted(db, ws)
    running = asyncio.create_task(tasks())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with generation() as peer:
            before = (await api.detail(sid, peer, ws))["messages"][-1]
            unknown = (1, 1, CONFIG["provider"], CONFIG["model"], "unknown", None)
            assert [intent(call) for call in before["trace"]["calls"]] == [unknown]
            assert before["content"] == partial
            await peer.execute(update(StudyMessage).where(StudyMessage.id == rid).values(expires_at=func.now()))
            await peer.commit()
        await worker.recover_interrupted()
        await asyncio.wait_for(running, 10)
        async with generation() as peer:
            interrupted = (await api.detail(sid, peer, ws))["messages"][-1]
            assert interrupted["status"] == "interrupted"
            assert interrupted["trace"]["calls"] == before["trace"]["calls"]
            retried = await api.retry(sid, rid, BackgroundTasks(), peer, ws)
            queued = (await api.detail(sid, peer, ws))["messages"][-1]
            first_call = before["trace"]["calls"][0]
            if partial:
                first_call = {**first_call, "saved_output": partial}
            assert retried["attempt"] == 2 and queued["trace"]["calls"] == [first_call]
        assert len(requests) == 1
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_buffered_artifact_repair_preserves_both_call_outcomes(monkeypatch, generation, db_session, test_workspace):
    db, ws = db_session, test_workspace
    buffering = asyncio.Event()
    usage = {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}

    class BufferedRepair(Body):
        async def __aiter__(self):
            yield frame("尚未校验的第二版")
            buffering.set()
            await asyncio.Event().wait()

    async def transport(_request):
        body = (Body([frame("缺少演示的首版"), frame(finish="stop"), event({"choices": [], "usage": usage}), DONE])
                if len(requests) == 1 else BufferedRepair([]))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

    requests = install(monkeypatch, None, handler=transport)
    sid, rid, tasks = await accepted(db, ws, action="demo")
    running = asyncio.create_task(tasks())
    try:
        await asyncio.wait_for(buffering.wait(), 10)
        async with generation() as peer:
            generating = (await api.detail(sid, peer, ws))["messages"][-1]
            first, second = generating["trace"]["calls"]
            assert intent(first) == (1, 1, CONFIG["provider"], CONFIG["model"], "completed", usage)
            assert (first["error"], first["saved_output"]) == ("invalid_artifact", "缺少演示的首版")
            assert intent(second) == (1, 2, CONFIG["provider"], CONFIG["model"], "unknown", None)
            assert second["retry_kind"] == "artifact_repair" and generating["content"] == ""
            await api.interrupt(sid, rid, peer, ws, expected_attempt=1)
            interrupted = (await api.detail(sid, peer, ws))["messages"][-1]
            assert (interrupted["status"], interrupted["content"]) == ("interrupted", "")
            assert interrupted["trace"]["calls"] == [first, second]
        await asyncio.wait_for(running, 10)
        assert len(requests) == 2
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("status, error, outcome", [
    (401, "http", "failed"), (503, "http", "unknown"), (200, "network", "unknown"),
])
async def test_provider_rejection_and_ambiguous_transport_outcomes(
    monkeypatch, generation, db_session, test_workspace, status, error, outcome,
):
    requests = install(monkeypatch, Body([], broken=True), status=status)
    sid, _, tasks = await accepted(db_session, test_workspace)
    await tasks()
    async with generation() as peer:
        reply = (await api.detail(sid, peer, test_workspace))["messages"][-1]
    assert reply["status"] == "failed" and len(requests) == 1
    (call,) = reply["trace"]["calls"]
    assert (call["error"], call["outcome"], call["usage"]) == (error, outcome, None)


@pytest.mark.asyncio
async def test_interrupt_query_fences_a_delayed_stop_and_keeps_legacy_clients(db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, rid, _ = await accepted(db, ws)
    await api.interrupt(sid, rid, db, ws)
    current = await api.retry(sid, rid, BackgroundTasks(), db, ws)
    assert current["attempt"] == 2

    factory = async_sessionmaker(db.bind, expire_on_commit=False)

    async def session():
        async with factory() as peer:
            yield peer

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides.update({api.get_session: session, api.get_workspace: lambda: ws})
    path = f"/study/sessions/{sid}/messages/{rid}/interrupt"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        stale = await client.post(path, params={"expected_attempt": 1})
        assert stale.status_code == 200 and stale.json() == current
        match = await client.post(path, params={"expected_attempt": 2})
        assert match.status_code == 200 and match.json() == {**current, "status": "interrupted"}
        later = await api.retry(sid, rid, BackgroundTasks(), db, ws)
        assert later["attempt"] == 3
        legacy = await client.post(path)
        assert legacy.status_code == 200 and legacy.json() == {**later, "status": "interrupted"}
