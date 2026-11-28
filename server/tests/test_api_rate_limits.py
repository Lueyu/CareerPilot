"""Synthetic HTTP/auth/provider, real isolated PostgreSQL admission and process state."""
import asyncio
import json
import os
import runpy
import secrets
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from credential_fixtures import enroll_token
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import rate_limits as gate
from app.config import ApiRatePolicy, Settings, get_settings
from app.db import get_session
from app.main import app
from app.models import ApiRateBucket, User
from app.models.public_auth import PublicAccount, PublicSession
from app.services import api_rate_limits as service


def policy(*, burst=10, daily=100, llm_burst=1, llm_daily=100):
    return ApiRatePolicy.model_validate({
        "read": {"burst": burst, "per_minute": 1, "daily": daily},
        "write": {"burst": burst, "per_minute": 1, "daily": daily},
        "llm": {"burst": llm_burst, "per_minute": 1, "daily": llm_daily},
    })


@pytest_asyncio.fixture
async def api(db_session, test_workspace, monkeypatch):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    cfg = get_settings()
    monkeypatch.setattr(cfg, "api_token", secrets.token_urlsafe(32))
    await enroll_token(db_session, cfg.api_token)
    monkeypatch.setattr(cfg, "default_workspace_slug", test_workspace.slug)
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "api_rate_limits", policy())
    monkeypatch.setattr(gate, "SessionLocal", factory)

    async def database():
        async with factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_session, database)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1",
                                 headers={"X-Api-Token": cfg.api_token, "X-Workspace": test_workspace.slug}) as client:
        yield client, cfg, factory, test_workspace


async def counts(factory, subject):
    async with factory() as db:
        return {row.category: (row.daily_count, row.burst_tokens, row.updated_at) for row in await db.scalars(
            select(ApiRateBucket).where(ApiRateBucket.subject == subject)
        )}


@pytest.mark.asyncio
async def test_http_write_denies_before_mutation_and_shared_credentials_cannot_reset(api):
    client, cfg, factory, workspace = api
    cfg.api_rate_limits = policy(burst=1)
    first = await client.patch("/settings/workspace", json={"name": "one accepted edit"})
    assert first.status_code == 200, first.text
    scoped = secrets.token_urlsafe(32)
    async with factory() as db:
        await enroll_token(db, scoped, workspace.slug)
    client.headers["X-Api-Token"] = scoped
    denied = await client.patch("/settings/workspace", json={"name": "must not persist"},
                                headers={"X-Forwarded-For": "203.0.113.99", "X-Request-ID": "secret-do-not-reflect"})
    assert denied.status_code == 429 and int(denied.headers["Retry-After"]) > 0
    assert denied.json()["detail"]["code"] == "API_RATE_LIMITED"
    assert denied.headers["X-Request-ID"] == denied.json()["request_id"]
    assert "secret-do-not-reflect" not in denied.text
    async with factory() as db:
        from app.models import Workspace
        assert (await db.get(Workspace, workspace.id)).name == "one accepted edit"
    assert (await counts(factory, f"workspace:{workspace.id}"))["write"][0] == 1


@pytest.mark.asyncio
async def test_failed_auth_or_scope_spoof_does_not_drain_victim(api):
    client, cfg, factory, workspace = api
    cfg.api_rate_limits = policy(burst=1)
    scoped = secrets.token_urlsafe(32)
    async with factory() as db:
        await enroll_token(db, scoped, workspace.slug)
    assert (await client.get("/study/sessions", headers={"X-Api-Token": "bad"})).status_code == 401
    assert (await client.get("/study/sessions", headers={"X-Api-Token": scoped, "X-Workspace": "victim"})).status_code == 404
    assert await counts(factory, f"workspace:{workspace.id}") == {}
    assert (await client.get("/study/sessions", headers={"X-Api-Token": scoped})).status_code == 200


@pytest.mark.asyncio
async def test_actual_llm_route_zero_paid_work_and_no_partial_write_debit(api, monkeypatch, caplog):
    from app.api.v1 import settings as settings_api

    client, cfg, factory, workspace = api
    cfg.api_rate_limits = policy(burst=2)
    calls = []

    async def config():
        return {"provider": "deepseek", "model": "synthetic", "base_url": "https://synthetic.invalid/v1", "api_key": ""}

    async def paid(*args, **kwargs):
        calls.append(kwargs["billing_scope"].workspace_id)
        return {"content": "OK"}

    monkeypatch.setattr(settings_api, "get_llm_config", config)
    monkeypatch.setattr(settings_api.adapter, "chat_with", paid)
    body = {"api_key": "synthetic-only"}
    assert (await client.post("/settings/llm/test", json=body)).json()["ok"]
    before = await counts(factory, f"workspace:{workspace.id}")
    with caplog.at_level("INFO", logger=gate.__name__):
        denied = await client.post("/settings/llm/test", json=body)
    assert denied.status_code == 429 and calls == [workspace.id]
    assert await counts(factory, f"workspace:{workspace.id}") == before
    assert denied.json()["request_id"] in caplog.text and "synthetic-only" not in caplog.text
    assert (await client.patch("/settings/workspace", json={"name": "remaining ordinary write"})).status_code == 200


@pytest.mark.asyncio
async def test_paid_get_is_limited_but_controlled_read_get_still_works(api, monkeypatch):
    from app.api.v1 import learning

    client, cfg, factory, workspace = api
    async with factory() as db:
        await service.admit(db, f"workspace:{workspace.id}", ("llm",), cfg.api_rate_limits)
    entered = []

    async def must_not_enter(*args, **kwargs):
        entered.append(True)
        raise AssertionError("paid business function reached")

    monkeypatch.setattr(learning, "build_teaching_content", must_not_enter)
    denied = await client.get("/learning/tasks/999999/content")
    assert denied.status_code == 429 and entered == []
    # This real route returns durable sessions and never triggers generation.
    assert (await client.get("/study/sessions")).status_code == 200
    assert (await counts(factory, f"workspace:{workspace.id}"))["read"][0] == 1


@pytest.mark.asyncio
async def test_competing_connections_admit_once_and_other_subject_is_independent(api):
    _, _, factory, workspace = api
    quota = policy(burst=1)

    async def attempt(subject):
        async with factory() as db:
            try:
                await service.admit(db, subject, ("write", "llm"), quota)
                return 200
            except HTTPException as exc:
                return exc.status_code

    subject = f"workspace:{workspace.id}"
    results = await asyncio.gather(*(attempt(subject) for _ in range(8)))
    assert results.count(200) == 1 and results.count(429) == 7
    assert await attempt("fixture-other-" + uuid4().hex) == 200
    assert {key: value[0] for key, value in (await counts(factory, subject)).items()} == {"write": 1, "llm": 1}


@pytest.mark.asyncio
async def test_strict_day_cap_reset_and_settings_changes_preserve_counts(api, monkeypatch):
    _, _, factory, workspace = api
    quota = policy(burst=100, daily=2, llm_burst=100, llm_daily=1)
    clock = [datetime(2026, 11, 28, 15, 59, 58, tzinfo=UTC)]  # UTC+8 23:59:58.

    async def server_time(db):
        return clock[0]

    monkeypatch.setattr(service, "_database_time", server_time)
    subject = f"workspace:{workspace.id}"
    async with factory() as db:
        await service.admit(db, subject, ("write", "llm"), quota)
    before = await counts(factory, subject)
    async with factory() as db:
        with pytest.raises(HTTPException) as denied:
            await service.admit(db, subject, ("write", "llm"), quota)
    assert denied.value.headers["Retry-After"] == "2"
    assert await counts(factory, subject) == before
    # Ordinary writes can still consume their remaining independent daily allowance.
    async with factory() as db:
        await service.admit(db, subject, ("write",), quota)
    # Lower then raise same-day configuration: existing count is retained.
    quota.write.daily = 1
    async with factory() as db:
        with pytest.raises(HTTPException):
            await service.admit(db, subject, ("write",), quota)
    quota.write.daily = 2
    async with factory() as db:
        with pytest.raises(HTTPException):
            await service.admit(db, subject, ("write",), quota)
    assert (await counts(factory, subject))["write"][0] == 2
    clock[0] += timedelta(seconds=3)
    async with factory() as db:
        await service.admit(db, subject, ("write", "llm"), quota)
    assert {key: value[0] for key, value in (await counts(factory, subject)).items()} == {"write": 1, "llm": 1}


@pytest.mark.asyncio
async def test_full_day_does_not_refill_daily_allowance_and_burst_recovers(api, monkeypatch):
    _, _, factory, workspace = api
    quota = policy(burst=1, daily=2)
    clock = [datetime(2026, 11, 28, 0, tzinfo=UTC)]

    async def server_time(db):
        return clock[0]

    monkeypatch.setattr(service, "_database_time", server_time)
    subject = f"workspace:{workspace.id}"
    async with factory() as db:
        await service.admit(db, subject, ("read",), quota)
    async with factory() as db:
        with pytest.raises(HTTPException) as burst:
            await service.admit(db, subject, ("read",), quota)
    assert burst.value.headers["Retry-After"] == "60"
    clock[0] += timedelta(hours=8)
    async with factory() as db:
        await service.admit(db, subject, ("read",), quota)
    clock[0] += timedelta(hours=7)
    async with factory() as db:
        with pytest.raises(HTTPException) as daily:
            await service.admit(db, subject, ("read",), quota)
    assert daily.value.headers["Retry-After"] == "3600"


@pytest.mark.asyncio
async def test_restart_process_sees_existing_daily_quota(api):
    _, _, factory, workspace = api
    subject = f"workspace:{workspace.id}"
    quota = policy(burst=100, daily=1)
    async with factory() as db:
        await service.admit(db, subject, ("read",), quota)
    program = """
import asyncio,json,os
from fastapi import HTTPException
from app.config import ApiRatePolicy
from app.db import SessionLocal,engine
from app.services.api_rate_limits import admit
async def main():
    async with SessionLocal() as db:
        try:
            await admit(db,os.environ['E3_SUBJECT'],('read',),ApiRatePolicy.model_validate_json(os.environ['E3_POLICY']))
            print(json.dumps({'status':200}))
        except HTTPException as e:
            print(json.dumps({'status':e.status_code,'retry':e.headers['Retry-After']}))
    await engine.dispose()
asyncio.run(main())
"""
    env = {**os.environ, "DATABASE_URL": str(factory.kw["bind"].url.render_as_string(hide_password=False)),
           "E3_SUBJECT": subject, "E3_POLICY": quota.model_dump_json()}
    child = await asyncio.create_subprocess_exec(sys.executable, "-c", program, env=env,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await child.communicate()
    assert child.returncode == 0, stderr.decode()
    result = json.loads(stdout)
    assert result["status"] == 429 and int(result["retry"]) > 0
    env["E3_SUBJECT"] = "process-race-" + uuid4().hex

    async def competitor():
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", program, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        output, error = await process.communicate()
        assert process.returncode == 0, error.decode()
        return json.loads(output)["status"]

    assert sorted(await asyncio.gather(competitor(), competitor())) == [200, 429]


@pytest.mark.asyncio
async def test_expired_state_cleanup_keeps_current_day_rows(api, monkeypatch):
    _, cfg, factory, workspace = api
    now = datetime.now(UTC)
    prefix = "expired-" + uuid4().hex
    async with factory.begin() as db:
        db.add_all([ApiRateBucket(subject=f"{prefix}-{i}", category="read", burst_tokens=0,
                                 daily_count=1, quota_day=(now - timedelta(days=2)).date(),
                                 updated_at=now - timedelta(days=2), expires_at=now - timedelta(days=1))
                    for i in range(105)])
    async with factory() as db:
        await service.admit(db, f"workspace:{workspace.id}", ("read",), cfg.api_rate_limits)
    async with factory() as db:
        remaining = list(await db.scalars(select(ApiRateBucket).where(ApiRateBucket.subject.startswith(prefix))))
    assert len(remaining) == 5
    assert (await counts(factory, f"workspace:{workspace.id}"))["read"][0] == 1


@pytest.mark.asyncio
async def test_registered_cookie_subject_origin_guard_and_native_reject_before_forward(api, monkeypatch):
    from app.api import public_native
    from app.services.public_identity import credential_digest

    _, cfg, factory, workspace = api
    origin = "http://127.0.0.1:18903"
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("SEED_SESSION_SECRET", "synthetic-e3-" + uuid4().hex * 2)
    monkeypatch.delenv("SEED_STATIC_DIR", raising=False)
    registered = runpy.run_module("app.public_main")["app"]

    async def database():
        async with factory() as db:
            yield db

    registered.dependency_overrides[get_session] = database
    tokens = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
    async with factory.begin() as db:
        user = User(email=uuid4().hex + "@synthetic.invalid", display_name="synthetic")
        db.add(user)
        await db.flush()
        account = PublicAccount(username=uuid4().hex, user_id=user.id, workspace_id=workspace.id,
                                password_hash="unused", recovery_hash="unused")
        db.add(account)
        await db.flush()
        db.add_all([PublicSession(token_hash=credential_digest("session", token), account_id=account.id,
                                  expires_at=datetime.now(UTC) + timedelta(hours=1)) for token in tokens])
    cfg.api_rate_limits = policy(burst=1)
    forwarded = []

    async def native_target(*args):
        forwarded.append(True)
        raise AssertionError("native provisioning must not run")

    monkeypatch.setattr(public_native, "native_target", native_target)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=registered), base_url=origin,
                                 headers={"Origin": origin}) as client:
        # Headers do not establish registered identity.
        assert (await client.get("/api/v1/memories", headers={"X-Api-Token": cfg.api_token})).status_code == 401
        client.cookies.set("seed_dev_session", tokens[0])
        assert (await client.get("/api/v1/auth/me", headers={"X-Expected-Workspace": "999999"})).status_code == 409
        assert (await client.post("/api/v1/memories", headers={"Origin": "https://wrong.invalid"},
                                  json={"text": "blocked", "confirmed": True})).status_code == 403
        assert await counts(factory, f"workspace:{workspace.id}") == {}
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        client.cookies.set("seed_dev_session", tokens[1])
        assert (await client.get("/api/v1/memories", headers={"X-Workspace": "forged"})).status_code == 429
        # Exhaust only LLM; the native request must not transfer data or provision a target.
        async with factory() as db:
            await service.admit(db, f"workspace:{workspace.id}", ("llm",), cfg.api_rate_limits)
        response = await client.post("/api/v1/native/projects/1/analyses", headers={"X-Expected-Workspace": str(workspace.id)}, json={})
        assert response.status_code == 429 and forwarded == []
        assert "write" not in await counts(factory, f"workspace:{workspace.id}")
        async def target(*args):
            return SimpleNamespace(base_url="https://synthetic.invalid", workspace=workspace.slug, api_token="synthetic")

        class DenialStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"detail":"upstream quota"}'

        monkeypatch.setattr(public_native, "native_target", target)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(429, stream=DenialStream(), headers={"Retry-After": "17"})
        )) as upstream:
            monkeypatch.setattr(public_native, "target_client", lambda *args: upstream)
            forwarded_denial = await client.patch("/api/v1/native/settings/workspace",
                headers={"X-Expected-Workspace": str(workspace.id)}, json={"name": "synthetic"})
        assert forwarded_denial.status_code == 429 and forwarded_denial.headers["Retry-After"] == "17"


@pytest.mark.asyncio
async def test_database_failure_is_safe_and_precedes_side_effect(api, monkeypatch):
    client, _, _, _ = api

    async def broken(*args):
        raise SQLAlchemyError("synthetic-secret-database-detail")

    monkeypatch.setattr(gate, "admit", broken)
    response = await client.patch("/settings/workspace", json={"name": "must not persist"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "API_QUOTA_UNAVAILABLE"
    assert "synthetic-secret" not in response.text


@pytest.mark.parametrize("field,value", [("burst", 0), ("daily", -1), ("per_minute", 0),
                                         ("burst", True), ("daily", 1.5), ("per_minute", "10")])
def test_invalid_configuration_fails_at_boundary(field, value):
    quota = policy().model_dump()
    quota["write"][field] = value
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_rate_limits=quota)


@pytest.mark.parametrize("method,path,expected", [
    ("GET", "/api/v1/learning/tasks/1/content", ("read", "llm")),
    ("GET", "/api/v1/native/learning/tasks/1/content", ("read", "llm")),
    ("POST", "/api/v1/projects", ("write", "llm")),
    ("POST", "/api/v1/projects/1/analyses", ("write", "llm")),
    ("POST", "/api/v1/question-generation/1/run", ("write", "llm")),
    ("GET", "/api/v1/study/sessions/1/messages/2/stream", ("read",)),
    ("GET", "/api/v1/coach/threads/1/turns/2/events", ("read",)),
    ("GET", "/api/v1/settings/llm/readiness", ("read",)),
    ("GET", "/api/v1/projects/1/dossier", ("read",)),
    ("POST", "/api/v1/benchmark/jd", ("write",)),
    ("POST", "/api/v1/coach/threads/1/turns", ("write", "llm")),
])
def test_business_route_classification(method, path, expected):
    assert gate.categories_for(method, path) == expected
