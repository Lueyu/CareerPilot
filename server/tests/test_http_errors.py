"""Real app HTTP contracts with synthetic faults and isolated PostgreSQL, no providers."""
import asyncio
import os
import re
import runpy
import socket
import subprocess
import sys
import traceback
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from credential_fixtures import enroll_token
from fastapi import HTTPException, Request
from pydantic import BaseModel, field_validator
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, StreamingResponse

from app.api import errors
from app.config import get_settings

pytestmark = pytest.mark.asyncio
SENTINEL = "F7_SECRET_sql://credential@host/?token=never-print"
UNTRUSTED = "untrusted-business-ticket-do-not-log"


class Ticket(BaseModel):
    request_id: str
    amount: int

    @field_validator("amount")
    @classmethod
    def reject_synthetic(cls, value):
        if value == 13:
            raise ValueError(SENTINEL)
        return value


@pytest_asyncio.fixture(params=["main", "public_main"])
async def actual_app(request, db_session, monkeypatch):
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", "https://f7.test")
    monkeypatch.setenv("SEED_SESSION_SECRET", "f7-synthetic-secret-" * 4)
    monkeypatch.delenv("SEED_STATIC_DIR", raising=False)
    monkeypatch.setattr(get_settings(), "static_dir", None)
    path = Path(__file__).resolve().parents[1] / "app" / (request.param + ".py")
    app = runpy.run_path(str(path))["app"]

    @app.get("/api/v1/__f7/fault")
    async def fault():
        raise RuntimeError(SENTINEL)

    @app.get("/api/v1/__f7/business")
    async def business():
        raise HTTPException(409, {"code": "already_done", "message": "Keep this message"},
                            headers={"Retry-After": "7", "X-Business": "preserved"})

    @app.post("/api/v1/__f7/ticket")
    async def ticket(body: Ticket, request: Request):
        request.state.registered_workspace_slug = "synthetic-f7-policy"
        return {"request_id": body.request_id, "transport_id": request.state.request_id}

    @app.post("/api/v1/__f7/many-errors")
    async def many_errors(body: list[int]):
        return body

    @app.get("/api/v1/__f7/stream")
    async def stream():
        async def chunks():
            yield b"data: started\n\n"
            raise RuntimeError(SENTINEL)
        return StreamingResponse(chunks(), media_type="text/event-stream")

    @app.get("/api/v1/__f7/completed")
    async def completed():
        async def background():
            raise RuntimeError(SENTINEL)
        return JSONResponse({"ok": True}, background=BackgroundTask(background))

    @app.get("/api/v1/__f7/cancel")
    async def cancel():
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://f7.test",
                                 headers={"Origin": "https://f7.test", "Accept-Language": "en"}) as client:
        yield app, client, request.param


def assert_envelope(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    assert re.fullmatch("[a-f0-9]{32}", body["request_id"])
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert body["message"]
    return body


async def test_fault_redaction_concurrency_and_success_ticket(actual_app, caplog):
    app, client, edition = actual_app
    with caplog.at_level("ERROR"):
        responses = await asyncio.gather(*(client.get("/api/v1/__f7/fault", headers={
            "X-Request-ID": UNTRUSTED, "Origin": "http://localhost:5173" if edition == "main" else "https://f7.test"
        }) for _ in range(2)))
    ids = []
    for response in responses:
        body = assert_envelope(response, 500, "INTERNAL_ERROR")
        ids.append(body["request_id"])
        assert body["detail"]["message"] == body["message"]
        assert response.headers["Content-Language"] == "en"
        assert "Accept-Language" in response.headers["Vary"]
        if edition == "main":
            assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
        else:
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Frame-Options"] == "DENY"
        assert SENTINEL not in response.text and UNTRUSTED not in response.text
    assert len(set(ids)) == 2
    assert all(caplog.text.count("request_id=" + value) == 1 for value in ids)
    assert "RuntimeError" in caplog.text and "test_http_errors.py" in caplog.text
    assert SENTINEL not in caplog.text and UNTRUSTED not in caplog.text
    response = await client.post("/api/v1/__f7/ticket", json={"request_id": UNTRUSTED, "amount": 1})
    assert response.status_code == 200
    assert response.json()["request_id"] == UNTRUSTED
    assert response.json()["transport_id"] == response.headers["X-Request-ID"]
    assert response.json()["transport_id"] != UNTRUSTED
    assert app.state.http_error_counts.snapshot()["counts"]["http_500"] == 2


async def test_business_and_both_validation_shapes(actual_app):
    _, client, edition = actual_app
    response = await client.get("/api/v1/__f7/business")
    body = assert_envelope(response, 409, "already_done")
    assert body["detail"] == {"code": "already_done", "message": "Keep this message"}
    assert response.headers["Retry-After"] == "7" and response.headers["X-Business"] == "preserved"
    response = await client.post("/api/v1/__f7/ticket", json={"request_id": UNTRUSTED, "amount": SENTINEL})
    body = assert_envelope(response, 422, "VALIDATION_ERROR")
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"] == ["body", "amount"]
    assert SENTINEL not in response.text
    response = await client.post("/api/v1/__f7/ticket", json={"request_id": UNTRUSTED, "amount": 13})
    assert_envelope(response, 422, "VALIDATION_ERROR")
    assert SENTINEL not in response.text
    response = await client.post("/api/v1/__f7/many-errors", json=[SENTINEL] * 100)
    assert len(assert_envelope(response, 422, "VALIDATION_ERROR")["detail"]) == 50
    if edition == "public_main":
        response = await client.post("/api/v1/auth/login", json={"username": "?", "password": SENTINEL})
        body = assert_envelope(response, 422, "VALIDATION_ERROR")
        assert isinstance(body["detail"], str) and SENTINEL not in response.text


async def test_early_recovery_failure_and_quarantine_have_ids(actual_app, monkeypatch, caplog):
    _, client, _ = actual_app
    from app.api import recovery_guard
    from app.services.recovery_control import RecoveryBlocked
    monkeypatch.setattr(recovery_guard, "require_normal", AsyncMock(side_effect=RecoveryBlocked()))
    body = assert_envelope(await client.get("/api/v1/__f7/ticket", headers={"X-Request-ID": UNTRUSTED}),
                           503, "RECOVERY_QUARANTINED")
    assert body["detail"]["code"] == "RECOVERY_QUARANTINED"
    monkeypatch.setattr(recovery_guard, "require_normal", AsyncMock(side_effect=RuntimeError(SENTINEL)))
    with caplog.at_level("ERROR"):
        assert_envelope(await client.get("/api/v1/__f7/fault"), 500, "INTERNAL_ERROR")
    assert SENTINEL not in caplog.text


async def test_origin_and_h8_errors_are_normalized(actual_app):
    _, client, edition = actual_app
    if edition == "public_main":
        response = await client.post("/api/v1/auth/login", headers={"Origin": "https://evil.test"}, json={})
        assert_envelope(response, 403, "FORBIDDEN")
    else:
        # H8 precedes route dependency and retains the existing literal detail.
        response = await client.post("/api/v1/telemetry/events", content="[" * 33 + "]" * 33,
                                     headers={"Content-Type": "application/json"})
        body = assert_envelope(response, 422, "VALIDATION_ERROR")
        assert body["detail"] == "JSON nesting exceeds 32"
        response = await client.post("/api/v1/telemetry/events", content=b"x" * (128 * 1024 + 1))
        assert_envelope(response, 413, "PAYLOAD_TOO_LARGE")


async def test_original_cancellation_propagates(actual_app):
    _, client, _ = actual_app
    with pytest.raises(asyncio.CancelledError):
        await client.get("/api/v1/__f7/cancel")


async def test_started_stream_does_not_restart_or_finish(actual_app, caplog):
    app, _, _ = actual_app
    messages = []
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "method": "GET", "path": "/api/v1/__f7/stream", "raw_path": b"/api/v1/__f7/stream",
             "query_string": b"", "scheme": "https", "server": ("f7.test", 443),
             "client": ("127.0.0.1", 1234), "headers": [(b"host", b"f7.test")], "root_path": ""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    with pytest.raises(errors.StreamAborted) as captured, caplog.at_level("ERROR"):
        await app(scope, receive, send)
    assert len([m for m in messages if m["type"] == "http.response.start"]) == 1
    assert [m["body"] for m in messages if m["type"] == "http.response.body"] == [b"data: started\n\n"]
    assert not any(m.get("more_body") is False for m in messages if m["type"] == "http.response.body")
    assert SENTINEL not in "".join(traceback.format_exception(captured.value))
    assert SENTINEL not in caplog.text
    assert app.state.http_error_counts.snapshot()["counts"]["unexpected_started"] == 1


async def test_owner_only_counter_endpoint(actual_app, db_session, test_workspace, monkeypatch):
    app, client, _ = actual_app
    assert (await client.get("/api/v1/ops/http-errors")).status_code == 401
    scoped = "synthetic-scoped-" + uuid4().hex
    await enroll_token(db_session, scoped, test_workspace.slug)
    assert (await client.get("/api/v1/ops/http-errors", headers={"X-Api-Token": scoped})).status_code == 403
    token = "synthetic-owner-" + uuid4().hex
    monkeypatch.setattr(get_settings(), "api_token", token)
    await enroll_token(db_session, token)
    response = await client.get("/api/v1/ops/http-errors", headers={"X-Api-Token": token})
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["scope"] == "application_process" and snapshot["durable"] is False
    assert snapshot["counts"]["http_401"] == snapshot["counts"]["http_403"] == 1
    assert app.state.http_error_counts.snapshot() == snapshot


async def test_outer_policy_fault_is_safe_and_policy_is_reused(actual_app, monkeypatch, caplog):
    app, client, edition = actual_app
    if edition == "public_main":
        policy = next(item.cls for item in app.user_middleware if item.cls.__name__ == "PrivateResponses")

        def fail(_slug):
            raise RuntimeError(SENTINEL)

        monkeypatch.setitem(policy.__call__.__globals__, "invalidate_transfer", fail)
    else:
        from starlette.middleware.cors import CORSMiddleware
        original = CORSMiddleware.is_allowed_origin
        calls = 0

        def fail_once(self, origin):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError(SENTINEL)
            return original(self, origin)

        monkeypatch.setattr(CORSMiddleware, "is_allowed_origin", fail_once)
    with caplog.at_level("ERROR"):
        response = await client.post("/api/v1/__f7/ticket", json={"request_id": UNTRUSTED, "amount": 1},
                                     headers={"Origin": "http://localhost:5173" if edition == "main" else "https://f7.test"})
    assert_envelope(response, 500, "INTERNAL_ERROR")
    assert response.headers["Content-Language"] == "en"
    if edition == "main":
        assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    else:
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Frame-Options"] == "DENY"
    assert SENTINEL not in caplog.text


async def test_completed_failure_is_observable_without_replacing_body(actual_app, caplog):
    app, client, _ = actual_app
    with caplog.at_level("ERROR"):
        response = await client.get("/api/v1/__f7/completed")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert app.state.http_error_counts.snapshot()["counts"] == {"unexpected_completed": 1}
    assert "completed" in caplog.text and SENTINEL not in caplog.text


async def test_transport_disconnect_does_not_turn_into_500(actual_app):
    app, _, _ = actual_app
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
             "method": "GET", "path": "/api/v1/__f7/stream", "raw_path": b"/api/v1/__f7/stream",
             "query_string": b"", "scheme": "https", "server": ("f7.test", 443),
             "client": ("127.0.0.1", 1234), "headers": [(b"host", b"f7.test")], "root_path": ""}

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(_message):
        raise OSError("synthetic closed socket")

    with pytest.raises(ClientDisconnect):
        await app(scope, receive, send)
    assert app.state.http_error_counts.snapshot()["counts"] == {}


@pytest.mark.parametrize("transport_failure", [False, True])
async def test_disconnect_does_not_expose_an_original_secret(transport_failure, caplog):
    messages = []
    counts = errors.ErrorCounts()

    async def application(scope, receive, send):
        await receive()
        raise RuntimeError(SENTINEL)

    async def receive():
        if transport_failure:
            raise OSError(SENTINEL)
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    boundary = errors.ErrorBoundary(application, counts=counts, response_policy=lambda response: response)
    scope = {"type": "http", "method": "GET", "path": "/disconnected-fixture", "headers": [], "state": {}}
    with pytest.raises(ClientDisconnect) as captured, caplog.at_level("ERROR"):
        await boundary(scope, receive, send)
    assert SENTINEL not in str(captured.value)
    assert captured.value.__context__ is None
    assert SENTINEL not in caplog.text
    assert messages == []
    assert counts.snapshot()["counts"] == ({} if transport_failure else {"unexpected_disconnected": 1})


async def test_safe_faults_do_not_reach_uvicorn_raw_logger(tmp_path, db_session):
    # A disposable local server verifies the outer framework/server logging path.
    script = tmp_path / "f7_server_probe.py"
    script.write_text('''from app.main import app
from starlette.responses import JSONResponse, StreamingResponse
from starlette.requests import ClientDisconnect
from starlette.background import BackgroundTask
@app.get("/__f7_fault")
async def fault():
    raise RuntimeError("''' + SENTINEL + '''")
@app.get("/__f7_stream")
async def stream():
    async def chunks():
        yield b"partial"
        raise RuntimeError("''' + SENTINEL + '''")
    return StreamingResponse(chunks())
''', encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + str(Path(__file__).resolve().parents[1])
    log = tmp_path / "uvicorn.log"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "f7_server_probe:app", "--host", "127.0.0.1",
                                    "--port", str(port), "--lifespan", "off", "--no-access-log"],
                                   cwd=Path(__file__).resolve().parents[1], env=env, stdout=output,
                                   stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
                for _ in range(100):
                    try:
                        if (await client.get("/healthz")).status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    assert process.poll() is None
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("Disposable server did not become ready")
                response = await client.get("/__f7_fault", headers={"X-Request-ID": UNTRUSTED})
                body = assert_envelope(response, 500, "INTERNAL_ERROR")
                with pytest.raises(httpx.RemoteProtocolError):
                    await client.get("/__f7_stream")
                assert (await client.get("/healthz")).status_code == 200
        finally:
            process.terminate()
            await asyncio.to_thread(process.wait, 15)
    text = log.read_text(encoding="utf-8")
    assert body["request_id"] in text
    assert "StreamAborted" in text
    assert SENTINEL not in text and UNTRUSTED not in text

async def test_real_study_chat_keeps_persisted_business_ticket(db_session, test_workspace, monkeypatch):
    from sqlalchemy import select

    from app.api.v1 import study_chat as chat
    from app.db import get_session
    from app.models import Question
    from app.models.study_chat import StudyMessage

    app = runpy.run_module("app.main")["app"]
    credential = "synthetic-chat-" + uuid4().hex
    await enroll_token(db_session, credential, test_workspace.slug)

    async def database():
        yield db_session

    async def no_provider(*args, **kwargs):
        pytest.fail("Inventory must not schedule a provider")

    monkeypatch.setitem(app.dependency_overrides, get_session, database)
    monkeypatch.setattr(chat, "generate_reply", no_provider)
    db_session.add(Question(workspace_id=test_workspace.id, short_code=uuid4().hex,
                            title="F7 correlation inventory", topic="f7inventory", qtype="open", tags=[]))
    await db_session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture",
                                 headers={"X-Api-Token": credential}) as client:
        created = await client.post("/api/v1/study/sessions", json={})
        assert created.status_code == 201, created.text
        session_id = created.json()["id"]
        path = f"/api/v1/study/sessions/{session_id}/messages"
        body = {"request_id": UNTRUSTED, "agent_role": "collect", "content": "题库里有哪些关于f7inventory的题"}
        first = await client.post(path, json=body, headers={"X-Request-ID": UNTRUSTED})
        replay = await client.post(path, json=body)
        assert first.status_code == replay.status_code == 202, first.text
        assert first.json() == replay.json() and first.json()["status"] == "completed"
        assert first.headers["X-Request-ID"] != replay.headers["X-Request-ID"]
        rows = list(await db_session.scalars(select(StudyMessage).where(StudyMessage.session_id == session_id)))
        assert len(rows) == 2 and all(row.request_id == UNTRUSTED for row in rows)
        assert first.headers["X-Request-ID"] != UNTRUSTED
