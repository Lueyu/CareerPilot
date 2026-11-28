import asyncio
import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from credential_fixtures import enroll_token
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import product_feedback
from app.db import get_session
from app.main import app as native_app
from app.models import Question, Workspace
from app.models.product_feedback import FeedbackAttempt, FeedbackSubmission
from app.public_main import app as public_app
from app.services import feedback_delivery

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/product-feedback"
ORIGIN = "https://feedback.test"
TOKEN = "synthetic-workspace-credential-" + uuid4().hex


@pytest_asyncio.fixture
async def clients(db_session, monkeypatch):
    workspace = Workspace(slug=uuid4().hex, name="Synthetic native workspace", is_demo=False)
    db_session.add(workspace)
    await db_session.commit()
    # Each fixture needs a distinct credential; old digests are permanent tombstones.
    global TOKEN
    TOKEN = "synthetic-workspace-credential-" + uuid4().hex
    await enroll_token(db_session, TOKEN, workspace.slug)
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("SEED_SESSION_SECRET", uuid4().hex * 2)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def database():
        async with factory() as session:
            yield session

    for app in (native_app, public_app):
        monkeypatch.setitem(app.dependency_overrides, get_session, database)
    delivery = AsyncMock()
    monkeypatch.setattr(feedback_delivery, "review_and_send", delivery)
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=native_app), base_url="http://native.test") as native,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=public_app), base_url=ORIGIN,
                          headers={"Origin": ORIGIN}) as public,
    ):
        yield native, public, delivery, workspace


def payload(message="Synthetic feedback"):
    return {"submission_id": str(uuid4()), "message": message}


def token_headers():
    return {"X-Api-Token": TOKEN}


async def register(client):
    response = await client.post("/api/v1/auth/register", json={
        "username": "fixture_" + uuid4().hex[:12],
        "password": "synthetic feedback password",
    })
    assert response.status_code == 201
    return {"X-Expected-Workspace": str(response.json()["workspace_id"])}


async def test_native_auth_validation_and_replay(clients, db_session):
    native, _, delivery, workspace = clients
    body = payload("  Synthetic feedback  ")
    assert (await native.post(PATH, json=body)).status_code == 401
    assert (await native.post(PATH, headers={**token_headers(), "X-Workspace": "other"}, json=body)).status_code == 404
    for invalid in (payload("   "), payload("a" * 2001), {"submission_id": "bad", "message": "private fixture"}):
        response = await native.post(PATH, headers=token_headers(), json=invalid)
        assert response.status_code == 422
        assert "private fixture" not in response.text

    async def oversized_chunks():
        yield b'{"message":"'
        yield b"x" * 16384
        yield b"x" * 16384

    assert (await native.post(PATH, headers={**token_headers(), "Content-Type": "application/json"},
                              content=oversized_chunks())).status_code == 413
    response = await native.post(PATH, headers=token_headers(), json=body)
    assert response.status_code == 200 and response.json() == {"status": "sent"}
    body["message"] = "Synthetic feedback"
    assert (await native.post(PATH, headers=token_headers(), json=body)).status_code == 200
    assert (await native.post(PATH, headers=token_headers(), json={**body, "message": "changed"})).status_code == 409
    assert delivery.await_count == 1
    assert delivery.await_args.args[0] == body["message"]
    row = await db_session.scalar(select(FeedbackSubmission).where(FeedbackSubmission.workspace_id == workspace.id))
    assert row.status == "sent"
    assert row.message_digest == hashlib.sha256(body["message"].encode()).hexdigest()


async def test_gateway_uses_exact_workspace_and_rejects_proxy_bypass(clients, db_session):
    _, public, delivery, _ = clients
    body = payload()
    assert (await public.post(PATH, json=body)).status_code == 401
    expected = await register(public)
    assert (await public.post(PATH, json=body, headers={"Origin": "https://other.test"})).status_code == 403
    assert (await public.post(PATH, json=body)).status_code == 409
    assert (await public.post(PATH, json=body, headers={"X-Expected-Workspace": "0"})).status_code == 409
    assert (await public.post(PATH, json=body, headers=list(expected.items()) * 2)).status_code == 409
    assert (await public.post(PATH, json=body, params={"expected_workspace_id": next(iter(expected.values()))})).status_code == 409
    assert (await public.post("/api/v1/native/product-feedback", json=body, headers=expected)).status_code == 404
    assert (await public.post(PATH, json={"question_id": 1, "text": "legacy"}, headers=expected)).status_code == 422
    assert (await public.post(PATH, json=body, headers=expected)).json() == {"status": "sent"}
    question = Question(
        short_code=uuid4().hex, workspace_id=int(expected["X-Expected-Workspace"]),
        title="Synthetic question", topic="fixture",
    )
    db_session.add(question)
    await db_session.commit()
    response = await public.post("/api/v1/feedback", headers=expected, json={
        "question_id": question.id, "text": "Synthetic question comment",
    })
    assert response.status_code == 201
    next_workspace = await register(public)
    assert (await public.post(PATH, json=body, headers=expected)).status_code == 409
    assert (await public.post(PATH, json=body, headers=next_workspace)).json() == {"status": "sent"}
    assert delivery.await_count == 2


@pytest.mark.parametrize(("error", "initial", "replay", "call_count"), [
    (feedback_delivery.FeedbackUnavailable, 503, 200, 2),
    (feedback_delivery.FeedbackRejected, 422, 422, 1),
    (feedback_delivery.FeedbackDeliveryUnknown, 409, 409, 1),
    (RuntimeError, 409, 409, 1),
])
async def test_delivery_outcomes(clients, error, initial, replay, call_count):
    native, _, delivery, _ = clients
    delivery.side_effect = [error("private fixture"), None]
    body = payload()
    response = await native.post(PATH, headers=token_headers(), json=body)
    assert response.status_code == initial
    assert "private fixture" not in response.text
    assert (await native.post(PATH, headers=token_headers(), json=body)).status_code == replay
    assert delivery.await_count == call_count


async def test_reservation_and_quotas_across_entry_points(clients, db_session, monkeypatch):
    native, public, delivery, _ = clients
    monkeypatch.setattr(product_feedback, "_WORKSPACE_LIMIT", 1)
    expected = await register(public)
    # Earlier tests share their fixture database. Set the global limit relative to current usage.
    active = await db_session.scalar(select(func.count()).select_from(FeedbackAttempt))
    monkeypatch.setattr(product_feedback, "_GLOBAL_LIMIT", active + 2)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def deliver(*args):
        entered.set()
        await release.wait()

    delivery.side_effect = deliver
    first = payload()
    task = asyncio.create_task(native.post(PATH, headers=token_headers(), json=first))
    contenders = []
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not task.done()
        assert (await native.post(PATH, headers=token_headers(), json=first)).status_code == 409
        assert (await native.post(PATH, headers=token_headers(), json=payload())).status_code == 429
        contenders = [asyncio.create_task(public.post(PATH, headers=expected, json=payload())) for _ in range(2)]
        done, _ = await asyncio.wait(contenders, timeout=5, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == 1
        assert done.pop().result().status_code == 429
        another_workspace = await register(public)
        assert (await public.post(PATH, headers=another_workspace, json=payload())).status_code == 429
    finally:
        release.set()
        responses = await asyncio.gather(task, *contenders)
    assert [response.status_code for response in responses].count(200) == 2
    assert delivery.await_count == 2

    await db_session.execute(update(FeedbackAttempt).where(
        FeedbackAttempt.workspace_id == int(expected["X-Expected-Workspace"])
    ).values(created_at=func.now() - product_feedback._WINDOW))
    await db_session.commit()
    assert (await public.post(PATH, headers=another_workspace, json=payload())).status_code == 200
    assert (await native.post(PATH, headers=token_headers(), json=first)).status_code == 200


async def test_interrupted_sender_never_replays(clients):
    native, _, delivery, _ = clients
    delivery.side_effect = asyncio.CancelledError
    body = payload()
    with pytest.raises(asyncio.CancelledError):
        await native.post(PATH, headers=token_headers(), json=body)
    assert (await native.post(PATH, headers=token_headers(), json=body)).status_code == 409
    assert delivery.await_count == 1


async def test_post_delivery_commit_failure_remains_ambiguous(clients, monkeypatch):
    native, _, delivery, _ = clients
    commit = AsyncSession.commit

    async def fail_after_delivery(session):
        if any(isinstance(row, FeedbackSubmission) and row.status == "sent" for row in session.dirty):
            raise SQLAlchemyError("synthetic commit failure")
        await commit(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_after_delivery)
    body = payload()
    assert (await native.post(PATH, headers=token_headers(), json=body)).status_code == 409
    assert (await native.post(PATH, headers=token_headers(), json=body)).status_code == 409
    assert delivery.await_count == 1

async def test_public_cancelled_feedback_is_not_replayed(clients, db_session):
    _, public, delivery, _ = clients
    expected = await register(public)
    delivery.side_effect = asyncio.CancelledError
    body = payload()
    with pytest.raises(asyncio.CancelledError):
        await public.post(PATH, headers=expected, json=body)
    assert (await public.post(PATH, headers=expected, json=body)).status_code == 409
    assert delivery.await_count == 1
    row = await db_session.get(FeedbackSubmission, (int(expected["X-Expected-Workspace"]), body["submission_id"]))
    assert row.status == "in_progress"
