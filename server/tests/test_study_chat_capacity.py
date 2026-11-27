import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import study_chat as api
from app.config import Settings
from app.models import Workspace
from app.models.study_chat import StudyMessage, StudySession
from app.services import study_chat as worker
from app.services import study_chat_capacity as capacity


@pytest.fixture
def runtime(monkeypatch, db_session):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "_EXECUTION_SLOTS", None)
    monkeypatch.setattr(worker, "_WAITING_GENERATIONS", 0)
    settings = worker.get_settings().model_copy(update={
        "study_generation_workspace_limit": 1, "study_generation_global_limit": 2,
        "study_generation_wait_seconds": 0.15,
    })
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(capacity, "get_settings", lambda: settings)
    return factory, settings


async def accepted(db, ws, key=None):
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    tasks = BackgroundTasks()
    body = api.NewMessage(request_id=key or uuid4().hex, content="解释检索的原理")
    ticket = await api.send(sid, body, tasks, db, ws)
    return sid, ticket, tasks


@pytest.mark.asyncio
async def test_workspace_full_refuses_before_any_message_or_session_mutation(db_session, test_workspace):
    db, ws = db_session, test_workspace
    await accepted(db, ws)
    await accepted(db, ws)
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    before = await db.get(StudySession, sid)
    original = (before.title, before.context, before.updated_at)
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as refused:
        await api.send(sid, api.NewMessage(request_id="refused", content="改变标题和上下文"), tasks, db, ws)
    assert refused.value.status_code == 429
    assert int(refused.value.headers["Retry-After"]) > 0
    await db.rollback()
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as peer:
        after = await peer.get(StudySession, sid)
        assert (after.title, after.context, after.updated_at) == original
        assert not await peer.scalar(select(StudyMessage.id).where(StudyMessage.session_id == sid))
    assert not tasks.tasks


@pytest.mark.asyncio
async def test_distinct_workspaces_share_global_admission_cap(db_session, test_workspace):
    db, ws = db_session, test_workspace
    others = [Workspace(slug=uuid4().hex, name="Synthetic workspace") for _ in range(2)]
    db.add_all(others)
    await db.commit()
    ids = [w.id for w in others]
    try:
        await accepted(db, ws)
        await accepted(db, ws)
        await accepted(db, others[0])
        await accepted(db, others[0])
        sid = (await api.create(api.StartSession(), db, others[1]))["id"]
        with pytest.raises(HTTPException) as refused:
            await api.send(sid, api.NewMessage(request_id="global-full", content="解释原子性"), BackgroundTasks(), db, others[1])
        assert refused.value.status_code == 429
    finally:
        await db.rollback()
        await db.execute(delete(Workspace).where(Workspace.id.in_(ids)))
        await db.commit()


@pytest.mark.asyncio
async def test_concurrent_admissions_cannot_oversubscribe_workspace(runtime, db_session, test_workspace):
    factory, _ = runtime
    ws = test_workspace
    sids = [(await api.create(api.StartSession(), db_session, ws))["id"] for _ in range(6)]

    async def submit(sid):
        async with factory() as peer:
            try:
                await api.send(sid, api.NewMessage(request_id=uuid4().hex, content="并发请求"), BackgroundTasks(), peer, ws)
                return 202
            except HTTPException as exc:
                return exc.status_code

    outcomes = await asyncio.wait_for(asyncio.gather(*(submit(sid) for sid in sids)), 15)
    assert sorted(outcomes) == [202, 429, 429, 429, 429, 429]


@pytest.mark.asyncio
async def test_concurrent_different_workspaces_cannot_oversubscribe_global(runtime, db_session, test_workspace):
    factory, settings = runtime
    settings.study_generation_global_limit = 1
    db, ws = db_session, test_workspace
    other = Workspace(slug=uuid4().hex, name="Synthetic peer")
    db.add(other)
    await db.commit()
    other_id = other.id
    pairs = [(owner, (await api.create(api.StartSession(), db, owner))["id"]) for owner in (ws, other)]

    async def submit(owner, sid):
        async with factory() as peer:
            try:
                await api.send(sid, api.NewMessage(request_id=uuid4().hex, content="共享上限"), BackgroundTasks(), peer, owner)
                return 202
            except HTTPException as exc:
                return exc.status_code

    try:
        assert sorted(await asyncio.wait_for(asyncio.gather(*(submit(*pair) for pair in pairs)), 15)) == [202, 429]
    finally:
        await db.execute(delete(Workspace).where(Workspace.id == other_id))
        await db.commit()


@pytest.mark.asyncio
async def test_idempotent_replay_and_session_conflict_precede_capacity(runtime, db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, ticket, _ = await accepted(db, ws, "same")
    tasks = BackgroundTasks()
    replay = await api.send(sid, api.NewMessage(request_id="same", content="解释检索的原理"), tasks, db, ws)
    assert replay == ticket and not tasks.tasks
    with pytest.raises(HTTPException) as conflict:
        await api.send(sid, api.NewMessage(request_id="another", content="继续讲解"), tasks, db, ws)
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_retry_refusal_preserves_attempt_content_and_trace(runtime, db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, ticket, _ = await accepted(db, ws)
    rid = ticket["assistant_message_id"]
    await api.interrupt(sid, rid, db, ws, expected_attempt=1)
    before = (await api.detail(sid, db, ws))["messages"][-1]
    await db.rollback()
    await db.refresh(ws)
    await accepted(db, ws)
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as refused:
        await api.retry(sid, rid, tasks, db, ws)
    assert refused.value.status_code == 429
    await db.rollback()
    await db.refresh(ws)
    after = (await api.detail(sid, db, ws))["messages"][-1]
    assert (after["attempt"], after["content"], after["trace"]) == (before["attempt"], before["content"], before["trace"])
    assert not tasks.tasks


@pytest.mark.asyncio
async def test_stale_lease_is_reclaimed_and_old_task_never_calls_provider(runtime, monkeypatch, db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, ticket, tasks = await accepted(db, ws)
    rid = ticket["assistant_message_id"]
    await db.execute(update(StudyMessage).where(StudyMessage.id == rid).values(
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db.commit()
    called = []

    async def forbidden(*args):
        called.append(args)
        raise AssertionError("Expired generation must never call the provider")

    monkeypatch.setattr(worker, "_generate", forbidden)
    await accepted(db, ws)
    await tasks()
    await worker.recover_interrupted()
    await db.rollback()
    await db.refresh(ws)
    stale = (await api.detail(sid, db, ws))["messages"][-1]
    assert stale["status"] == "interrupted" and stale["trace"]["lifecycle"][-1]["event"] == "expired"
    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "error", "cancelled"])
async def test_whole_execution_releases_on_completion_provider_exception_or_cancel(
    runtime, monkeypatch, db_session, test_workspace, outcome,
):
    db, ws = db_session, test_workspace
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def provider(_turn, _settings, _deadline, _trace, save, _fail, _language):
        entered.set()
        try:
            if outcome == "cancelled":
                await asyncio.Event().wait()
            if outcome == "error":
                raise RuntimeError("Synthetic provider exception")
            await save("completed", status="completed", content="合成回复", expires_at=None)
        finally:
            cleaned.set()

    monkeypatch.setattr(worker, "_generate", provider)
    sid, ticket, tasks = await accepted(db, ws, "lifecycle")
    running = asyncio.create_task(tasks())
    await asyncio.wait_for(entered.wait(), 10)
    if outcome == "cancelled":
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    else:
        await asyncio.wait_for(running, 10)
    assert cleaned.is_set()
    slots = worker.execution_slots()
    for _ in range(2):
        await asyncio.wait_for(slots.acquire(), 1)
    assert slots.locked()
    for _ in range(2):
        slots.release()
    await db.rollback()
    await db.refresh(ws)
    reply = (await api.detail(sid, db, ws))["messages"][-1]
    expected = {"completed": "completed", "error": "failed", "cancelled": "interrupted"}[outcome]
    assert reply["status"] == expected
    assert [event["event"] for event in reply["trace"]["lifecycle"]] == ["accepted", "running", "cancelled" if outcome == "cancelled" else expected]
    assert reply["trace"]["request_id"] == "lifecycle" and reply["trace"]["workspace_id"] == ws.id
    await accepted(db, ws)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancelled", "expired", "retried"])
async def test_after_wait_rechecks_cancel_expiry_and_attempt(runtime, monkeypatch, db_session, test_workspace, change):
    factory, settings = runtime
    settings.study_generation_global_limit = 1
    settings.study_generation_wait_seconds = 2
    db, ws = db_session, test_workspace
    slots = worker.execution_slots()
    await slots.acquire()
    sid, ticket, tasks = await accepted(db, ws)
    rid = ticket["assistant_message_id"]
    called = []

    async def forbidden(*args):
        called.append(args)
        raise AssertionError("Superseded generation reached provider")

    monkeypatch.setattr(worker, "_generate", forbidden)
    waiting = asyncio.create_task(tasks())
    async with asyncio.timeout(5):
        while not slots._waiters:
            await asyncio.sleep(0.01)
    async with factory() as peer:
        if change == "expired":
            await peer.execute(update(StudyMessage).where(StudyMessage.id == rid).values(
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            await peer.commit()
        else:
            await api.interrupt(sid, rid, peer, ws, expected_attempt=1)
            if change == "retried":
                await api.retry(sid, rid, BackgroundTasks(), peer, ws)
    slots.release()
    await asyncio.wait_for(waiting, 5)
    assert not called
    await asyncio.wait_for(slots.acquire(), 1)
    slots.release()
    await db.rollback()
    row = await db.get(StudyMessage, rid, populate_existing=True)
    if change == "retried":
        assert row.attempt == 2 and row.status == "generating"


@pytest.mark.asyncio
async def test_permit_wait_is_bounded_and_does_not_leak(runtime, monkeypatch, db_session, test_workspace):
    _, settings = runtime
    settings.study_generation_global_limit = 1
    db, ws = db_session, test_workspace
    slots = worker.execution_slots()
    await slots.acquire()
    sid, _, tasks = await accepted(db, ws)
    try:
        await asyncio.wait_for(tasks(), 2)
        assert slots.locked()
        reply = (await api.detail(sid, db, ws))["messages"][-1]
        assert reply["status"] == "failed" and reply["trace"]["error_kind"] == "capacity"
    finally:
        slots.release()
    await accepted(db, ws)


@pytest.mark.asyncio
async def test_cancel_retry_flood_cannot_accumulate_unbounded_waiters(runtime, db_session, test_workspace):
    _, settings = runtime
    settings.study_generation_global_limit = 1
    settings.study_generation_wait_seconds = 3
    db, ws = db_session, test_workspace
    slots = worker.execution_slots()
    await slots.acquire()
    sid, ticket, first_tasks = await accepted(db, ws)
    first = asyncio.create_task(first_tasks())
    try:
        async with asyncio.timeout(5):
            while not slots._waiters:
                await asyncio.sleep(0.01)
        await api.interrupt(sid, ticket["assistant_message_id"], db, ws, expected_attempt=1)
        retry_tasks = BackgroundTasks()
        await api.retry(sid, ticket["assistant_message_id"], retry_tasks, db, ws)
        await asyncio.wait_for(retry_tasks(), 1)
        current = (await api.detail(sid, db, ws))["messages"][-1]
        assert current["status"] == "failed" and current["trace"]["error_kind"] == "capacity"
        assert worker._WAITING_GENERATIONS == 1
    finally:
        slots.release()
        await asyncio.wait_for(first, 5)
    assert worker._WAITING_GENERATIONS == 0


@pytest.mark.asyncio
async def test_background_registration_failure_rolls_back_accepted_turn(runtime, db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid = (await api.create(api.StartSession(), db, ws))["id"]

    class BrokenTasks(BackgroundTasks):
        def add_task(self, *args, **kwargs):
            raise RuntimeError("Synthetic task-registration failure")

    with pytest.raises(RuntimeError, match="task-registration"):
        await api.send(sid, api.NewMessage(request_id="launch-failed", content="不应该保存"), BrokenTasks(), db, ws)
    await db.rollback()
    assert not await db.scalar(select(StudyMessage.id).where(StudyMessage.session_id == sid))
    await db.refresh(ws)
    await accepted(db, ws)


@pytest.mark.asyncio
async def test_cancelled_provider_finishes_cleanup_before_next_workspace_executes(
    runtime, monkeypatch, db_session, test_workspace,
):
    _, settings = runtime
    settings.study_generation_global_limit = 1
    settings.study_generation_wait_seconds = 3
    db, ws = db_session, test_workspace
    other = Workspace(slug=uuid4().hex, name="Synthetic execution peer")
    db.add(other)
    await db.commit()
    other_id = other.id
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    release_cleanup = asyncio.Event()
    second_entered = asyncio.Event()
    active = maximum = 0

    async def provider(turn, _settings, _deadline, _trace, save, _fail, _language):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            if turn["row"].workspace_id == ws.id:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release_cleanup.wait()
            else:
                second_entered.set()
                await save("completed", status="completed", content="Second workspace", expires_at=None)
        finally:
            active -= 1

    monkeypatch.setattr(worker, "_generate", provider)
    sid, ticket, first = await accepted(db, ws)
    first_running = asyncio.create_task(first())
    second_running = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        await api.interrupt(sid, ticket["assistant_message_id"], db, ws, expected_attempt=1)
        first_running.cancel()
        await asyncio.wait_for(cleaning.wait(), 5)
        _, _, second = await accepted(db, other)
        second_running = asyncio.create_task(second())
        slots = worker.execution_slots()
        async with asyncio.timeout(5):
            while not slots._waiters:
                await asyncio.sleep(0.01)
        assert not second_entered.is_set()
        release_cleanup.set()
        await asyncio.gather(first_running, return_exceptions=True)
        await asyncio.wait_for(second_running, 10)
        assert second_entered.is_set() and maximum == 1 and active == 0
    finally:
        release_cleanup.set()
        if not first_running.done():
            first_running.cancel()
        if second_running is not None and not second_running.done():
            second_running.cancel()
        await asyncio.gather(*(task for task in (first_running, second_running) if task is not None), return_exceptions=True)
        await db.rollback()
        await db.execute(delete(Workspace).where(Workspace.id == other_id))
        await db.commit()


@pytest.mark.parametrize("field", ["study_generation_workspace_limit", "study_generation_global_limit", "study_generation_wait_seconds"])
def test_capacity_settings_reject_disabled_bounds(field):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 0})


@pytest.mark.asyncio
async def test_terminal_database_timeout_keeps_call_evidence_and_releases_permit(runtime, monkeypatch, db_session, test_workspace):
    factory, _ = runtime
    original_publish = worker.publish
    injected = False
    evidence = [{"attempt": 1, "outcome": "unknown", "provider_request_id": "synthetic-intent"}]

    async def generate(_turn, _settings, _deadline, trace, save, _fail, _language):
        trace["calls"] = evidence
        await save("generating")
        raise RuntimeError("Synthetic provider failure")

    async def publish(message_id, attempt, **values):
        nonlocal injected
        if values.get("status") == "failed" and not injected:
            injected = True
            async with factory() as db:
                async with asyncio.timeout(0.05):
                    await db.execute(text("SELECT pg_sleep(1)"))
        return await original_publish(message_id, attempt, **values)

    monkeypatch.setattr(worker, "_generate", generate)
    monkeypatch.setattr(worker, "publish", publish)
    _, ticket, tasks = await accepted(db_session, test_workspace)
    outcomes = await asyncio.gather(tasks(), return_exceptions=True)
    async with factory() as peer:
        reply = await peer.get(StudyMessage, ticket["assistant_message_id"])
        assert reply.coach["trace"]["calls"] == evidence
        assert reply.coach["trace"]["error_kind"] != "capacity"
    assert injected and isinstance(outcomes[0], TimeoutError)
    slots = worker.execution_slots()
    await asyncio.wait_for(slots.acquire(), 1)
    await asyncio.wait_for(slots.acquire(), 1)
    slots.release()
    slots.release()

pytestmark = pytest.mark.usefixtures("mock_private_billing")
