"""Public auth endpoints against an isolated PostgreSQL schema."""

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import public_memory
from app.api.public_auth import current_workspace, public_origin_guard, router, validate_auth_config
from app.db import get_session
from app.models import Event, ImportItem, ImportSession, MemoryItem, Question, User, Workspace, WorkspaceMember
from app.models.public_auth import PublicAccount, PublicSession

ORIGIN = "https://seed.test"
AUTH = "/api/v1/auth"
COOKIE = "__Host-seed_session"
PASSWORD = "sample password 123"
REPLACEMENT = "different password 456"


@pytest_asyncio.fixture
async def browser(db_session, monkeypatch):
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("SEED_SESSION_SECRET", secrets.token_urlsafe(48))
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def database():
        async with factory() as session:
            yield session

    @asynccontextmanager
    async def connect(ip="192.0.2.1", origin=ORIGIN):
        validate_auth_config()
        app = FastAPI()
        app.middleware("http")(public_origin_guard)
        app.include_router(router, prefix="/api/v1")
        app.include_router(public_memory.router, prefix="/api/v1")
        app.dependency_overrides[get_session] = database

        @app.get("/workspace")
        async def workspace(account_workspace: Workspace = Depends(current_workspace)):
            return {"id": account_workspace.id, "target_role": account_workspace.target_role}

        transport = httpx.ASGITransport(app=app, client=(ip, 12345))
        async with httpx.AsyncClient(transport=transport, base_url=origin, headers={"origin": origin}) as client:
            yield client

    return connect


def credentials(username, password=PASSWORD):
    return {"username": username, "password": password}


@pytest.mark.asyncio
async def test_stale_tab_cannot_write_read_logout_or_delete_after_account_switch(browser):
    async with browser() as shared, browser() as anonymous:
        first = (await shared.post(f"{AUTH}/register", json=credentials("first_tab"))).json()
        first_workspace = {"x-expected-workspace": str(first["workspace_id"])}
        draft = {"text": "First account's draft", "confirmed": True}
        assert (await anonymous.post("/api/v1/memories", headers=first_workspace, json=draft)).status_code == 401

        second = (await shared.post(f"{AUTH}/register", json=credentials("second_tab"))).json()
        second_workspace = {"x-expected-workspace": str(second["workspace_id"])}
        for method, path, payload in [
            ("POST", "/api/v1/memories", draft),
            ("GET", "/api/v1/memories", None),
            ("GET", f"/workspace?expected_workspace_id={first['workspace_id']}", None),
            ("POST", f"{AUTH}/logout", None),
            ("DELETE", f"{AUTH}/account", {"password": PASSWORD}),
        ]:
            headers = {} if "?" in path else first_workspace
            rejected = await shared.request(method, path, headers=headers, json=payload)
            assert rejected.status_code == 409, (method, path)
            assert rejected.json()["detail"]["code"] == "workspace_changed"
            assert "set-cookie" not in rejected.headers

        assert (await shared.get(f"{AUTH}/me")).json()["workspace_id"] == second["workspace_id"]
        assert (await shared.get("/api/v1/memories", headers=second_workspace)).json() == []
        saved = await shared.post("/api/v1/memories", headers=second_workspace, json={**draft, "text": "Second account's draft"})
        assert saved.status_code == 201
        assert (await shared.get("/api/v1/memories", headers=second_workspace)).json() == [saved.json()]

        # A later logout response must not overwrite a cookie another tab installed.
        logout = await shared.post(f"{AUTH}/logout", headers=second_workspace)
        assert logout.status_code == 204
        assert "set-cookie" not in logout.headers
        expired = await shared.get("/api/v1/memories", headers=second_workspace)
        assert expired.status_code == 401
        assert expired.json()["detail"]["code"] == "session_required"
        assert (await shared.post(f"{AUTH}/login", json=credentials("first_tab"))).status_code == 200
        assert (await shared.get("/api/v1/memories", headers=first_workspace)).json() == []
        removed = await shared.request("DELETE", f"{AUTH}/account", headers=first_workspace, json={"password": PASSWORD})
        assert removed.status_code == 204
        assert "set-cookie" not in removed.headers
        assert (await shared.get(f"{AUTH}/me")).status_code == 401


@pytest.mark.asyncio
async def test_registration_session_identity_logout_and_expiry(browser, db_session, test_workspace):
    async with browser() as owner, browser() as anonymous:
        assert (await anonymous.get(f"{AUTH}/me")).status_code == 401
        assert (await anonymous.get("/workspace")).status_code == 401

        result = await owner.post(f"{AUTH}/register", json=credentials("New_Candidate"))
        assert result.status_code == 201
        assert result.headers["cache-control"] == "no-store"
        assert all(flag in result.headers["set-cookie"] for flag in ["Secure", "HttpOnly", "SameSite=strict", "Path=/"])
        assert "Domain=" not in result.headers["set-cookie"]
        assert list(owner.cookies.keys()) == [COOKIE]
        account = result.json()
        workspace_id = account["workspace_id"]
        assert account["username"] == "new_candidate"
        assert len(account["recovery_code"]) == 32
        assert (await owner.get(f"{AUTH}/me")).json() == {"username": "new_candidate", "workspace_id": workspace_id}
        assert (await owner.get("/workspace", headers={"x-workspace": test_workspace.slug})).json() == {
            "id": workspace_id, "target_role": ""
        }
        assert (await anonymous.post(f"{AUTH}/register", json=credentials("NEW_CANDIDATE"))).status_code == 409

        registered = await db_session.scalar(select(PublicAccount).where(PublicAccount.workspace_id == workspace_id))
        member = await db_session.get(WorkspaceMember, (workspace_id, registered.user_id))
        assert member.role == "owner"
        assert registered.password_hash != PASSWORD
        assert registered.recovery_hash != account["recovery_code"]
        stored_token = await db_session.scalar(select(PublicSession.token_hash).where(
            PublicSession.account_id == registered.id
        ))
        stolen_cookie = owner.cookies.get(COOKIE)
        assert stored_token != stolen_cookie

        logout = await owner.post(f"{AUTH}/logout")
        assert logout.status_code == 204
        assert logout.content == b""
        assert (await owner.get(f"{AUTH}/me", headers={"cookie": f"{COOKIE}={stolen_cookie}"})).status_code == 401

        login = await owner.post(f"{AUTH}/login", json=credentials("NEW_candidate"))
        assert login.status_code == 200
        assert login.json() == {"username": "new_candidate", "workspace_id": workspace_id}
        await db_session.execute(
            update(PublicSession).where(PublicSession.account_id == registered.id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db_session.commit()
        assert (await owner.get(f"{AUTH}/me")).status_code == 401


@pytest.mark.asyncio
async def test_concurrent_registration_rolls_back_the_losing_workspace(browser, db_session):
    before = await db_session.scalar(select(func.count()).select_from(Workspace))
    async with browser() as first, browser() as second:
        body = credentials("same_username")
        results = await asyncio.gather(
            first.post(f"{AUTH}/register", json=body),
            second.post(f"{AUTH}/register", json=body),
        )
        assert sorted(result.status_code for result in results) == [201, 409]
    after = await db_session.scalar(select(func.count()).select_from(Workspace))
    assert after == before + 1
    assert await db_session.scalar(select(func.count()).select_from(User).where(User.display_name == "same_username")) == 1


@pytest.mark.asyncio
async def test_recovery_consumes_code_once_and_revokes_all_old_sessions(browser):
    async with browser() as owner, browser() as other, browser() as left, browser() as right:
        registration = await owner.post(f"{AUTH}/register", json=credentials("rotate_user"))
        assert registration.status_code == 201
        old_code = registration.json()["recovery_code"]
        assert (await other.post(f"{AUTH}/login", json=credentials("rotate_user"))).status_code == 200

        body = {**credentials("rotate_user", REPLACEMENT), "recovery_code": old_code}
        results = await asyncio.gather(
            left.post(f"{AUTH}/recover", json=body),
            right.post(f"{AUTH}/recover", json=body),
        )
        assert sorted(result.status_code for result in results) == [200, 401]
        fresh = next(result for result in results if result.status_code == 200)
        new_code = fresh.json()["recovery_code"]
        assert fresh.headers["cache-control"] == "no-store"
        assert old_code != new_code
        assert (await left.get(f"{AUTH}/me")).status_code != (await right.get(f"{AUTH}/me")).status_code
        for client in [owner, other]:
            assert (await client.get(f"{AUTH}/me")).status_code == 401
        assert (await other.post(f"{AUTH}/login", json=credentials("rotate_user"))).status_code == 401
        assert (await other.post(f"{AUTH}/login", json=credentials("rotate_user", REPLACEMENT))).status_code == 200
        body["recovery_code"] = new_code
        assert (await other.post(f"{AUTH}/recover", json=body)).status_code == 200


@pytest.mark.asyncio
async def test_origin_and_host_guard_covers_every_write_and_keeps_credentials_out_of_errors(browser):
    async with browser() as client:
        for method, path in [
            ("POST", "/register"),
            ("POST", "/login"),
            ("POST", "/recover"),
            ("POST", "/logout"),
            ("DELETE", "/account"),
        ]:
            for origin in ["https://other.test", "null", ""]:
                response = await client.request(method, f"{AUTH}{path}", headers={"origin": origin})
                assert response.status_code == 403
        assert (await client.get(f"{AUTH}/me", headers={"host": "other.test"})).status_code == 403
        assert (await client.post(f"{AUTH}/login", headers=[
            ("origin", ORIGIN), ("origin", "https://other.test")
        ])).status_code == 403
        response = await client.post(f"{AUTH}/register", json=credentials("format_valid", "secret"))
        assert response.status_code == 422
        assert "secret" not in response.text

    async with browser(origin="http://127.0.0.1:5173") as client:
        assert (await client.get(f"{AUTH}/me")).status_code == 403


@pytest.mark.asyncio
async def test_registered_account_deletion_cascades_owned_content_only(browser, db_session, test_workspace):
    async with browser() as owner, browser() as other:
        signup = await owner.post(f"{AUTH}/register", json=credentials("departing_user"))
        workspace_id = signup.json()["workspace_id"]
        account = await db_session.scalar(select(PublicAccount).where(PublicAccount.workspace_id == workspace_id))
        user_id = account.user_id
        question = Question(workspace_id=workspace_id, short_code="AUTH-private-q", title="Fixture", topic="fixture")
        imported = ImportSession(workspace_id=workspace_id, short_code="AUTH-private-import")
        memory = MemoryItem(workspace_id=workspace_id, mtype="ProjectExperience", content="Fixture")
        feedback = Event(workspace_id=workspace_id, kind="seed.question.feedback")
        db_session.add_all([question, imported, memory, feedback])
        await db_session.flush()
        item = ImportItem(session_id=imported.id, question_id=question.id, title="Fixture item")
        db_session.add(item)
        await db_session.commit()
        tracked = [(model, model.id) for model in [account, question, imported, memory, feedback, item]]

        login = await other.post(f"{AUTH}/login", json=credentials("departing_user"))
        assert login.status_code == 200
        rejected = await owner.request("DELETE", f"{AUTH}/account", json={"password": "incorrect"})
        assert rejected.status_code == 401
        assert await db_session.get(Workspace, workspace_id) is not None

        removed = await owner.request(
            "DELETE", f"{AUTH}/account", json={"password": PASSWORD}, headers={"x-workspace": test_workspace.slug}
        )
        assert removed.status_code == 204
        assert removed.content == b""
        assert COOKIE not in owner.cookies
        assert (await other.get(f"{AUTH}/me")).status_code == 401
        assert await db_session.get(Workspace, test_workspace.id, populate_existing=True) is not None
        assert await db_session.get(Workspace, workspace_id, populate_existing=True) is None
        assert await db_session.get(User, user_id, populate_existing=True) is None
        for model, record_id in tracked:
            assert await db_session.get(type(model), record_id, populate_existing=True) is None


@pytest.mark.asyncio
async def test_attempt_limits_persist_across_instances_and_charge_parallel_failures(browser):
    body = {**credentials("missing_user"), "recovery_code": "a" * 32}
    async with browser(ip="192.0.2.19") as first:
        attempts = await asyncio.gather(*(first.post(f"{AUTH}/recover", json=body) for _ in range(8)))
        assert sorted(attempt.status_code for attempt in attempts) == [401] * 6 + [429] * 2
        assert int(next(attempt.headers["retry-after"] for attempt in attempts if attempt.status_code == 429)) > 0
    async with browser(ip="192.0.2.20") as second:
        assert (await second.post(f"{AUTH}/recover", json=body)).status_code == 429

    async with browser(ip="192.0.2.30") as first:
        attempts = await asyncio.gather(
            *(first.post(f"{AUTH}/register", json=credentials(f"registration_limit_{index}")) for index in range(7))
        )
        assert sorted(attempt.status_code for attempt in attempts) == [201] * 6 + [429]
    async with browser(ip="192.0.2.30") as second:
        assert (await second.post(f"{AUTH}/register", json=credentials("later_registration"))).status_code == 429


@pytest.mark.parametrize("origin", ["", "http://seed.test", "https://seed.test/", "https://a@seed.test", "https://seed.test:443"])
def test_startup_rejects_invalid_origin(monkeypatch, origin):
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("SEED_SESSION_SECRET", secrets.token_urlsafe(48))
    with pytest.raises(RuntimeError, match="SEED_PUBLIC_ORIGIN"):
        validate_auth_config()


@pytest.mark.parametrize("secret", ["", "cp-dev-token", "replace-me-with-a-real-secret-please", "x" * 32])
def test_startup_rejects_known_or_short_secrets(monkeypatch, secret):
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("SEED_SESSION_SECRET", secret)
    with pytest.raises(RuntimeError, match="SEED_SESSION_SECRET"):
        validate_auth_config()


@pytest.mark.asyncio
async def test_explicit_loopback_uses_a_dev_cookie(browser, monkeypatch):
    origin = "http://127.0.0.1:5173"
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", origin)
    async with browser(origin=origin) as client:
        result = await client.post(f"{AUTH}/register", json=credentials("local_only"))
        assert result.status_code == 201
        assert "Secure" not in result.headers["set-cookie"]
        assert list(client.cookies.keys()) == ["seed_dev_session"]
        assert (await client.get(f"{AUTH}/me")).status_code == 200
