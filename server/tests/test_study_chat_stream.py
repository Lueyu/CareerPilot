"""SSE streaming endpoint, lease expiry and relaxed timeout defaults for study chat."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import study_chat as api
from app.config import MAX_STUDY_TIMEOUT_SECONDS, get_settings
from app.models import Workspace
from app.models.study_chat import StudyMessage
from app.services import study_chat as worker


@pytest.fixture
def worker_session(monkeypatch, db_session):
    """Point the background/stream SessionLocal at the isolated test database."""
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    return factory


@pytest.mark.asyncio
async def test_stream_endpoint_ownership_and_missing_reply(monkeypatch, db_session, test_workspace):
    ws = test_workspace

    async def no_open_practice(session, owner, row, question_text):
        pass

    monkeypatch.setattr(api, "mark_help", no_open_practice)
    sid = (await api.create(api.StartSession(topic="SSE"), db_session, ws))["id"]
    body = api.NewMessage(content="为什么使用 SSE", request_id="stream-1")
    queue = BackgroundTasks()
    ticket = await api.send(sid, body, queue, db_session, ws)
    rid = ticket["assistant_message_id"]

    # 同 workspace 可见, 返回 StreamingResponse(text/event-stream)
    response = await api.stream(sid, rid, db_session, ws)
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"

    # 跨 workspace 404
    other = Workspace(slug="other-" + ws.slug, name="Other")
    db_session.add(other)
    await db_session.commit()
    try:
        foreign_sid = (await api.create(api.StartSession(), db_session, other))["id"]
        with pytest.raises(HTTPException, match="404"):
            await api.stream(foreign_sid, rid, db_session, other)
        with pytest.raises(HTTPException, match="404"):
            await api.stream(sid, rid + 1000, db_session, ws)
    finally:
        await db_session.execute(delete(Workspace).where(Workspace.id == other.id))
        await db_session.commit()


@pytest.mark.asyncio
async def test_message_stream_emits_snapshot_then_terminates(worker_session, db_session, test_workspace):
    """message_stream mirrors the durable row and stops at a terminal status."""
    db, ws = db_session, test_workspace

    async def no_open_practice(session, owner, row, question_text):
        pass

    api.mark_help, saved = no_open_practice, api.mark_help
    try:
        sid = (await api.create(api.StartSession(topic="SSE"), db, ws))["id"]
        body = api.NewMessage(content="持续轮询的替代方案", request_id="stream-2")
        queue = BackgroundTasks()
        ticket = await api.send(sid, body, queue, db, ws)
        rid = ticket["assistant_message_id"]

        stream = worker.message_stream(rid)
        first = await stream.__anext__()
        assert '"status": "generating"' in first and f'"id": {rid}' in first
        await stream.aclose()

        # 让生成完成(直接改库), 新流的第一帧就是终态并随即结束
        await db.execute(update(StudyMessage).where(StudyMessage.id == rid).values(
            status="completed", content="完成内容", error=None, expires_at=None,
            coach={"trace": {"stage": "completed", "calls": []}}))
        await db.commit()
        frames = []
        async for frame in worker.message_stream(rid):
            frames.append(frame)
        assert frames and '"status": "completed"' in frames[0] and "完成内容" in frames[0]
    finally:
        api.mark_help = saved


@pytest.mark.asyncio
async def test_message_stream_forwards_live_reasoning_frames(worker_session, db_session, test_workspace):
    """推理增量走进程内队列实时下发, 且不落库(数据库仍只有最终文本)。"""
    db, ws = db_session, test_workspace

    async def no_open_practice(session, owner, row, question_text):
        pass

    api.mark_help, saved = no_open_practice, api.mark_help
    try:
        sid = (await api.create(api.StartSession(topic="SSE"), db, ws))["id"]
        body = api.NewMessage(content="实时推理帧", request_id="stream-live")
        queue = BackgroundTasks()
        ticket = await api.send(sid, body, queue, db, ws)
        rid = ticket["assistant_message_id"]

        stream = worker.message_stream(rid)
        first_task = asyncio.create_task(stream.__anext__())
        await asyncio.sleep(0)  # 让生成器订阅队列并挂起在 queue.get()
        live = {"id": rid, "status": "generating", "content": "", "model": None,
                "attempt": 1, "error": None, "stage": "generating", "reasoning": "先想清楚幂等键的语义"}
        worker._publish_live(rid, live)
        frame = await asyncio.wait_for(first_task, timeout=2)
        assert '"reasoning": "先想清楚幂等键的语义"' in frame
        await stream.aclose()
        # 推理只是瞬时展示, 不写数据库
        row = await db.scalar(select(StudyMessage).where(StudyMessage.id == rid))
        assert row.content == ""
    finally:
        api.mark_help = saved


@pytest.mark.asyncio
async def test_expire_message_if_stale_flips_expired_lease(worker_session, db_session, test_workspace):
    db, ws = db_session, test_workspace

    async def no_open_practice(session, owner, row, question_text):
        pass

    api.mark_help, saved = no_open_practice, api.mark_help
    try:
        sid = (await api.create(api.StartSession(topic="SSE"), db, ws))["id"]
        body = api.NewMessage(content="租约过期", request_id="stream-3")
        queue = BackgroundTasks()
        ticket = await api.send(sid, body, queue, db, ws)
        rid = ticket["assistant_message_id"]
        await db.execute(update(StudyMessage).where(StudyMessage.id == rid).values(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
        assert await worker.expire_message_if_stale(rid) is True
        state = await api.detail(sid, db, ws)
        assert state["messages"][-1]["status"] == "interrupted"
        assert await worker.expire_message_if_stale(rid) is False
    finally:
        api.mark_help = saved


def test_relaxed_timeout_defaults():
    """The study timeout is generous and capped by the shared constant."""
    settings = get_settings()
    assert settings.study_timeout_seconds == MAX_STUDY_TIMEOUT_SECONDS
    assert MAX_STUDY_TIMEOUT_SECONDS >= 600
    # lease_deadline 使用同一上限, 不含异常长租约
    deadline = worker.lease_deadline()
    assert deadline > datetime.now(timezone.utc)
    assert (deadline - datetime.now(timezone.utc)).total_seconds() <= MAX_STUDY_TIMEOUT_SECONDS + 60
