"""Probe HTTP contracts on both actual application entry points."""
import asyncio
import runpy
import secrets
import socket
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from asyncpg import InvalidPasswordError
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import db, main, public_main


@pytest_asyncio.fixture(params=[(main.app, "careerpilot-api"), (public_main.app, "careerpilot-public")],
                      ids=["personal", "public"])
async def probe_client(request, monkeypatch):
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", "https://seed.test")
    monkeypatch.setenv("SEED_SESSION_SECRET", secrets.token_urlsafe(48))
    app, service = request.param
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://seed.test") as client:
        yield client, service


def assert_probe(response, service, ready):
    assert response.status_code == (200 if ready else 503)
    assert response.json() == {"ok": ready, "service": service}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_ready_with_real_database(probe_client, db_session):
    client, service = probe_client
    assert_probe(await client.get("/readyz"), service, True)


@pytest.mark.asyncio
async def test_liveness_ignores_database_and_recovery(probe_client, monkeypatch):
    client, service = probe_client

    def forbidden(*args, **kwargs):
        raise AssertionError("liveness must not acquire a database session")

    monkeypatch.setattr(db, "SessionLocal", forbidden)
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["service"] == service


@pytest.mark.asyncio
async def test_refused_database_is_safe_and_recovers(probe_client, db_session, monkeypatch, caplog):
    client, service = probe_client
    working_factory = db.SessionLocal
    # Binding without listening reserves an owned loopback port that refuses TCP.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        url = db_session.bind.url.set(port=reserved.getsockname()[1], username="probe_secret_user",
                                      password="probe_secret_password")
        failed_engine = create_async_engine(url)
        try:
            monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(failed_engine))
            assert_probe(await client.get("/readyz"), service, False)
            assert (await client.get("/healthz")).status_code == 200
        finally:
            await failed_engine.dispose()
    assert "probe_secret" not in caplog.text
    monkeypatch.setattr(db, "SessionLocal", working_factory)
    assert_probe(await client.get("/readyz"), service, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["quarantined", "missing"])
async def test_database_success_does_not_bypass_recovery(probe_client, db_session, state):
    client, service = probe_client
    assert_probe(await client.get("/readyz"), service, True)
    before = dict((await db_session.execute(text("SELECT * FROM recovery_control"))).mappings().one())
    try:
        if state == "quarantined":
            await db_session.execute(text("UPDATE recovery_control SET access_state='QUARANTINED'"))
        else:
            await db_session.execute(text("DELETE FROM recovery_control"))
        await db_session.commit()
        assert await db_session.scalar(text("SELECT 1")) == 1
        assert_probe(await client.get("/readyz"), service, False)
        assert (await client.get("/healthz")).status_code == 200
        assert_probe(await client.get("/readyz"), service, False)
    finally:
        if state == "quarantined":
            await db_session.execute(text("UPDATE recovery_control SET access_state=:state"),
                                     {"state": before["access_state"]})
        else:
            columns = ",".join(before)
            values = ",".join(":" + name for name in before)
            await db_session.execute(text(f"INSERT INTO recovery_control ({columns}) VALUES ({values})"), before)
        await db_session.commit()
    assert_probe(await client.get("/readyz"), service, True)


@pytest.mark.asyncio
async def test_pool_exhaustion_times_out_while_liveness_responds(probe_client, db_session, monkeypatch):
    client, service = probe_client
    engine = create_async_engine(db_session.bind.url, pool_size=1, max_overflow=0, pool_timeout=30)
    monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(engine))
    try:
        async with engine.connect():
            pending = asyncio.create_task(client.get("/readyz"))
            await asyncio.sleep(0.05)
            assert (await asyncio.wait_for(client.get("/healthz"), 0.5)).status_code == 200
            assert_probe(await asyncio.wait_for(pending, 2.5), service, False)
        assert_probe(await client.get("/readyz"), service, True)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_query_timeout_and_recovery(probe_client, db_session):
    client, service = probe_client
    await db_session.execute(text("LOCK TABLE recovery_control IN ACCESS EXCLUSIVE MODE"))
    try:
        pending = asyncio.create_task(client.get("/readyz"))
        await asyncio.sleep(0.05)
        assert (await asyncio.wait_for(client.get("/healthz"), 0.5)).status_code == 200
        assert_probe(await asyncio.wait_for(pending, 2.5), service, False)
    finally:
        await db_session.rollback()
    assert_probe(await client.get("/readyz"), service, True)


@pytest.mark.asyncio
async def test_request_cancellation_propagates(probe_client, db_session, monkeypatch):
    client, service = probe_client
    engine = create_async_engine(db_session.bind.url, pool_size=1, max_overflow=0)
    monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(engine))
    try:
        async with engine.connect():
            pending = asyncio.create_task(client.get("/readyz"))
            await asyncio.sleep(0.05)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        assert_probe(await client.get("/readyz"), service, True)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unexpected_programming_error_is_not_readiness_false(probe_client, monkeypatch):
    client, _ = probe_client

    def broken_factory():
        raise ValueError("programming defect")

    monkeypatch.setattr(db, "SessionLocal", broken_factory)
    with pytest.raises(ValueError, match="programming defect"):
        await client.get("/readyz")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    OperationalError("SELECT secret FROM private_table", {"password": "probe_secret"},
                     Exception("postgresql://probe_secret@private-host/internal")),
    InvalidPasswordError("probe_secret at private-host"),
])
async def test_expected_database_errors_do_not_expose_details(probe_client, monkeypatch, caplog, failure):
    client, service = probe_client

    def unavailable():
        raise failure

    monkeypatch.setattr(db, "SessionLocal", unavailable)
    response = await client.get("/readyz")
    assert_probe(response, service, False)
    assert "probe_secret" not in caplog.text
    assert "private-host" not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_public_host_guard_still_applies(monkeypatch):
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", "https://seed.test")
    monkeypatch.setenv("SEED_SESSION_SECRET", secrets.token_urlsafe(48))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public_main.app),
                                base_url="https://wrong.test") as client:
        assert (await client.get("/readyz")).status_code == 403
        assert (await client.get("/healthz")).status_code == 403


@pytest.mark.asyncio
async def test_personal_static_fallback_does_not_hide_readiness(db_session, monkeypatch, tmp_path):
    from app.config import get_settings

    (tmp_path / "index.html").write_text("<main>Fixture SPA</main>", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "static_dir", str(tmp_path))
    application = runpy.run_path(str(Path(main.__file__)))["app"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                base_url="http://127.0.0.1") as client:
        assert (await client.get("/study")).text == "<main>Fixture SPA</main>"
        assert_probe(await client.get("/readyz"), "careerpilot-api", True)
        try:
            await db_session.execute(text("UPDATE recovery_control SET access_state='QUARANTINED'"))
            await db_session.commit()
            assert_probe(await client.get("/readyz"), "careerpilot-api", False)
            assert (await client.get("/healthz")).status_code == 200
        finally:
            await db_session.execute(text("UPDATE recovery_control SET access_state='NORMAL'"))
            await db_session.commit()
