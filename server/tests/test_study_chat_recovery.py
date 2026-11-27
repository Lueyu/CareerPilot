"""Recovery and attempt fencing for a committed study turn."""
import asyncio
from datetime import timedelta

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_llm_stream_transport import CONFIG, DONE, Body, frame, install

from app.api.v1 import study_chat as api
from app.models.study_chat import StudyMessage
from app.services import study_chat as worker


async def lease_remaining(db, reply_id):
    return await db.scalar(select(StudyMessage.expires_at - func.clock_timestamp()).where(
        StudyMessage.id == reply_id))


@pytest.mark.asyncio
async def test_accepted_turn_has_short_lease_before_background_task_starts(db_session, test_workspace):
    session = await api.create(api.StartSession(), db_session, test_workspace)
    tasks = BackgroundTasks()
    body = api.NewMessage(request_id="queued-lease", content="解释检索召回率")
    ticket = await api.send(session["id"], body, tasks, db_session, test_workspace)
    assert ticket["status"] == "generating"
    assert timedelta(0) < await lease_remaining(db_session, ticket["assistant_message_id"]) <= timedelta(seconds=30)


@pytest.mark.asyncio
async def test_silent_provider_keeps_lease_and_old_attempt_cannot_touch_retry(monkeypatch, db_session, test_workspace):
    db, ws = db_session, test_workspace
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "GENERATION_LEASE_SECONDS", 8)
    monkeypatch.setattr(worker, "GENERATION_HEARTBEAT_SECONDS", 2)

    async def config():
        return CONFIG

    monkeypatch.setattr(worker, "get_llm_config", config)
    first_body = asyncio.Event()
    release = asyncio.Event()

    class SilentAfterPartial(Body):
        async def __aiter__(self):
            yield frame("已保存的开头")
            first_body.set()
            await release.wait()
            yield frame("旧任务结尾") + frame(finish="stop") + DONE

    requests = install(monkeypatch, SilentAfterPartial([]))
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    tasks = BackgroundTasks()
    body = api.NewMessage(request_id="heartbeat", content="解释向量检索")
    rid = (await api.send(sid, body, tasks, db, ws))["assistant_message_id"]
    generating = asyncio.create_task(tasks())
    try:
        await asyncio.wait_for(first_body.wait(), timeout=10)
        # Cross the initial lease with no provider progress.
        await asyncio.sleep(8.2)
        async with factory() as peer:
            partial = (await api.detail(sid, peer, ws))["messages"][-1]
            assert (partial["status"], partial["content"]) == ("generating", "已保存的开头")
            assert timedelta(0) < await lease_remaining(peer, rid) <= timedelta(seconds=8)
            await api.interrupt(sid, rid, peer, ws)
            retry_tasks = BackgroundTasks()
            retried = await api.retry(sid, rid, retry_tasks, peer, ws)
            assert retried["attempt"] == 2 and len(retry_tasks.tasks) == 1
            expires_at = await peer.scalar(select(StudyMessage.expires_at).where(StudyMessage.id == rid))
        release.set()
        await asyncio.wait_for(generating, timeout=5)
        async with factory() as peer:
            assert await peer.scalar(select(StudyMessage.expires_at).where(StudyMessage.id == rid)) == expires_at
            current = (await api.detail(sid, peer, ws))["messages"][-1]
            assert (current["attempt"], current["status"], current["content"]) == (2, "generating", "")
        assert len(requests) == 1
    finally:
        release.set()
        await asyncio.gather(generating, return_exceptions=True)


@pytest.mark.asyncio
async def test_stalled_generation_cannot_renew_past_its_timeout_and_cleanup_window(
    monkeypatch, db_session, test_workspace,
):
    db, ws = db_session, test_workspace
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "GENERATION_LEASE_SECONDS", 4)
    monkeypatch.setattr(worker, "GENERATION_HEARTBEAT_SECONDS", 1)
    settings = worker.get_settings().model_copy(update={"study_timeout_seconds": 3})
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    collecting = asyncio.Event()
    release = asyncio.Event()

    async def stall(*_args):
        collecting.set()
        await release.wait()

    monkeypatch.setattr(worker, "_gather_sources", stall)
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    tasks = BackgroundTasks()
    await api.send(sid, api.NewMessage(request_id="stalled-turn", content="解释向量检索"), tasks, db, ws)
    generation = asyncio.create_task(tasks())
    try:
        await asyncio.wait_for(collecting.wait(), timeout=5)
        await asyncio.sleep(7.2)
        assert not generation.done()
        async with factory() as peer:
            assert (await api.detail(sid, peer, ws))["messages"][-1]["status"] == "interrupted"
    finally:
        release.set()
        await asyncio.wait_for(generation, timeout=5)

pytestmark = pytest.mark.usefixtures("mock_private_billing")
