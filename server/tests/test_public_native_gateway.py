"""Exercise the registered gateway with synthetic accounts and an in-process upstream."""
import asyncio
import runpy
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import public_native
from app.db import get_session
from app.models import Base, Question, Workspace
from app.services import public_native_runtime as runtime

ORIGIN = "https://gateway.test"
AUTH = "/api/v1/auth"
NATIVE = "/api/v1/native"
PASSWORD = "synthetic password 123"
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def gateway(db_session, tmp_path, monkeypatch):
    shell = tmp_path / "index.html"
    shell.write_text("<html>synthetic shell</html>")
    (tmp_path / "podcast-offline.html").write_text("<html>offline entry</html>")
    (tmp_path / "podcast-sw.js").write_text("self.clients.claim()")
    assets = tmp_path / "static-assets"
    assets.mkdir()
    (assets / "entry.js").write_text("/* synthetic asset */")
    bank = tmp_path / "legacy.json"
    bank.write_text('{"questions":["owner-only"]}')
    for key, value in {
        "SEED_STATIC_DIR": str(tmp_path), "SEED_NATIVE_OWNER_BANK": str(bank),
        "SEED_PUBLIC_ORIGIN": ORIGIN, "SEED_SESSION_SECRET": uuid4().hex * 2,
        "SEED_NATIVE_OWNER_API_TOKEN": "synthetic-owner-token",
        "SEED_NATIVE_LOCAL_TENANTS": "1", "SEED_NATIVE_OWNER_BINDING": "",
    }.items():
        monkeypatch.setenv(key, value)
    app = runpy.run_module("app.public_main")["app"]
    pool = create_async_engine(db_session.bind.url, pool_size=3, max_overflow=0, pool_timeout=2)
    factory = async_sessionmaker(pool, expire_on_commit=False)
    from app.api import rate_limits

    monkeypatch.setattr(rate_limits, "SessionLocal", factory)

    async def database():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = database
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json={"workspaces": [
            {"slug": request.headers["x-workspace"]}, {"slug": "foreign-fixture"}
        ]})

    def mock_client(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kwargs)

    monkeypatch.setattr(runtime, "_clients", {})
    monkeypatch.setattr(runtime, "_new_client", lambda limit: mock_client())
    tenants = Mock()
    tenants.ensure.side_effect = lambda slug, role: SimpleNamespace(
        base_url="http://tenant.invalid", workspace_slug=slug, api_token="synthetic-tenant-token")
    monkeypatch.setattr(runtime, "local_runtime", lambda: tenants)
    monkeypatch.setattr(runtime, "_sync", {})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN,
                                 headers={"Origin": ORIGIN}) as client:
        try:
            yield client, requests, tenants
        finally:
            for target in list(runtime._clients):
                await runtime._close_clients(target)
            await pool.dispose()


async def register(client, db, username):
    response = await client.post(AUTH + "/register", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return await db.get(Workspace, response.json()["workspace_id"])


async def test_bound_and_ordinary_targets_and_private_projection(gateway, db_session, monkeypatch, track_public_question):
    client, calls, tenants = gateway
    path = NATIVE + "/settings/workspaces"
    assert (await client.get(path)).status_code == 401
    owner = await register(client, db_session, "Lueyu")
    assert (await client.get(NATIVE + "/access")).json() == {"personal_owner": False}
    monkeypatch.setenv("SEED_NATIVE_OWNER_BINDING", f"{owner.id}:{owner.slug}")
    assert (await client.get(NATIVE + "/access")).json() == {"personal_owner": True}
    assert (await client.get(NATIVE + "/bank/legacy-data")).json()["questions"] == ["owner-only"]

    async def verify_target(workspace, host, token):
        response = await client.get(path, headers={"Authorization": "Bearer client-token",
            "X-Api-Token": "client-token", "X-Workspace": "foreign-fixture",
            "X-Forwarded-Host": "foreign.invalid"})
        assert response.json() == {"default_slug": workspace, "workspaces": [{"slug": workspace}]}
        sent = calls[-1]
        assert (sent.url.host, sent.url.path) == (host, "/api/v1/settings/workspaces")
        assert sent.headers["x-api-token"] == token
        assert sent.headers["x-workspace"] == workspace
        assert not {"authorization", "cookie", "x-forwarded-host"} & set(sent.headers)

    await verify_target("personal", "127.0.0.1", "synthetic-owner-token")
    tenants.ensure.assert_not_called()
    peer = await register(client, db_session, "peer")
    assert (await client.get(path, params={"expected_workspace_id": owner.id})).status_code == 409
    assert (await client.post(NATIVE + "/example")).status_code == 409
    stale = {"x-expected-workspace": str(owner.id)}
    assert (await client.post(NATIVE + "/example", headers=stale)).status_code == 409
    assert (await client.request("DELETE", AUTH + "/account", headers=stale,
                                 json={"password": PASSWORD})).status_code == 409
    assert len(calls) == 1
    tenants.ensure.assert_not_called()
    assert (await client.get(NATIVE + "/bank/legacy-data")).json() == {"topics": [], "questions": []}

    for ws_id, title in [(peer.id, "my-question"), (owner.id, "owner-private")]:
        db_session.add(Question(workspace_id=ws_id, title=title, topic="fixture", short_code=uuid4().hex))
    db_session.add(track_public_question(Question(title="shared", topic="fixture", short_code=uuid4().hex)))
    await db_session.commit()

    tenant_name = "gateway_" + uuid4().hex + "_test"
    async with db_session.bind.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text(f'CREATE DATABASE "{tenant_name}"'))
    url = db_session.bind.url.set(database=tenant_name)
    tenants.database.return_value = nullcontext(url.render_as_string(hide_password=False))
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine) as destination:
            destination.add(Workspace(slug=peer.slug, name="Synthetic tenant"))
            await destination.commit()
            await verify_target(peer.slug, "tenant.invalid", "synthetic-tenant-token")
            tenants.ensure.assert_called_once_with(peer.slug, peer.target_role)
            tenants.database.assert_called_once_with(peer.slug, peer.target_role)
            titles = await destination.scalars(select(Question.title))
            assert list(titles) == ["my-question"]
    finally:
        await engine.dispose()


async def test_account_deletion_targets_only_selected_tenant(gateway, db_session, monkeypatch):
    client, calls, tenants = gateway
    owner = await register(client, db_session, "bound_fixture")
    monkeypatch.setenv("SEED_NATIVE_OWNER_BINDING", f"{owner.id}:{owner.slug}")

    async def remove(ws):
        return await client.request("DELETE", AUTH + "/account", headers={"x-expected-workspace": str(ws.id)},
                                    json={"password": PASSWORD})

    assert (await remove(owner)).status_code == 204
    tenants.remove.assert_not_called()
    assert (await client.get(NATIVE + "/access")).status_code == 401
    replacement = await register(client, db_session, "bound_fixture")
    assert (await client.get(NATIVE + "/access")).json() == {"personal_owner": False}
    tenants.remove.side_effect = RuntimeError("synthetic cleanup failure")
    assert (await remove(replacement)).status_code == 503
    assert (await client.get(AUTH + "/me")).json()["workspace_id"] == replacement.id
    tenants.remove.assert_called_once_with(replacement.slug)
    tenants.remove.reset_mock(side_effect=True)
    assert (await remove(replacement)).status_code == 204
    tenants.remove.assert_called_once_with(replacement.slug)
    assert (await client.get(AUTH + "/me")).status_code == 401
    assert not calls


async def test_product_deep_links_and_compiled_assets(gateway):
    client, _, _ = gateway
    for path in ("/", "/podcasts", "/onboarding", "/bank/workshop", "/study/retest/report", "/study/chat/12",
                 "/study/notes/12", "/exam/diagnosis/report", "/learning/task/12", "/profile/rag",
                 "/assets", "/assets/project/12", "/assets/pipeline", "/benchmark", "/observatory",
                 "/evolution", "/settings", "/memory", "/beta", "/account"):
        response = await client.get(path)
        assert (response.status_code, response.text) == (200, "<html>synthetic shell</html>"), path
    for path, content, mime in (("/podcast-offline.html", "<html>offline entry</html>", "html"),
                                ("/podcast-sw.js", "self.clients.claim()", "javascript")):
        response = await client.get(path)
        assert (response.status_code, response.text) == (200, content)
        assert mime in response.headers["content-type"]
    asset = await client.get("/static-assets/entry.js")
    assert asset.status_code == 200 and asset.text == "/* synthetic asset */"
    for missing in ("/static-assets/missing.js", "/api/v1/missing"):
        assert (await client.get(missing)).status_code == 404


async def test_workspace_list_does_not_forward_client_query_credentials(gateway, db_session, monkeypatch):
    client, calls, _ = gateway
    owner = await register(client, db_session, "query_fixture")
    monkeypatch.setenv("SEED_NATIVE_OWNER_BINDING", f"{owner.id}:{owner.slug}")
    response = await client.get(NATIVE + "/settings/workspaces", params={
        "expected_workspace_id": owner.id, "access_token": "synthetic-client-query-token",
    })
    if response.status_code == 400:
        assert not calls
    else:
        assert response.status_code == 200
        assert "synthetic-client-query-token" not in str(calls[-1].url)



async def test_concurrent_cold_and_streaming_requests_release_auth_connections(gateway, db_session, monkeypatch):
    client, calls, tenants = gateway
    ws = await register(client, db_session, "burst_fixture")
    started = threading.Event()
    resume = threading.Event()
    streams_open = asyncio.Event()
    release_streams = asyncio.Event()
    count = 20
    closed = []
    ensure = tenants.ensure.side_effect

    def cold_start(slug, role):
        started.set()
        assert resume.wait(15)
        return ensure(slug, role)

    async def transfer(_, db, selected):
        assert selected.id == ws.id

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            await release_streams.wait()
            yield b"last"

        async def aclose(self):
            closed.append(self)

    def upstream(request):
        calls.append(request)
        if len(calls) == count:
            streams_open.set()
        return httpx.Response(200, stream=Stream())

    monkeypatch.setattr(runtime, "_new_client", lambda limit: httpx.AsyncClient(
        transport=httpx.MockTransport(upstream)))
    monkeypatch.setattr(runtime, "_transfer", transfer)
    tenants.ensure.side_effect = cold_start
    requests = [asyncio.create_task(client.get(NATIVE + "/stream")) for _ in range(count)]
    try:
        assert await asyncio.to_thread(started.wait, 10)
        assert (await client.get(AUTH + "/me")).status_code == 200
        resume.set()
        await asyncio.wait_for(streams_open.wait(), 10)
        assert (await client.get(AUTH + "/me")).status_code == 200
        requests[0].cancel()
    finally:
        resume.set()
        release_streams.set()
        results = await asyncio.gather(*requests, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert all(r.content == b"firstlast" for r in results[1:])
    assert len(set(closed)) == count
    tenants.ensure.assert_called_once_with(ws.slug, ws.target_role)


async def test_delete_waits_for_cold_provision_even_if_client_cancels(gateway, db_session, monkeypatch):
    client, _, tenants = gateway
    ws = await register(client, db_session, "cold_delete")
    started = asyncio.Event()
    finish = asyncio.Event()

    async def transfer(*args):
        started.set()
        await finish.wait()

    monkeypatch.setattr(runtime, "_transfer", transfer)
    provisioning = asyncio.create_task(client.get(NATIVE + "/settings/workspaces"))
    await asyncio.wait_for(started.wait(), 10)
    provisioning.cancel()
    deleting = asyncio.create_task(client.request("DELETE", AUTH + "/account",
        headers={"x-expected-workspace": str(ws.id)}, json={"password": PASSWORD}))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(deleting), 0.5)
        tenants.remove.assert_not_called()
    finally:
        finish.set()
        await asyncio.gather(provisioning, return_exceptions=True)
    assert (await asyncio.wait_for(deleting, 10)).status_code == 204
    tenants.remove.assert_called_once_with(ws.slug)


async def test_real_connections_reused_while_stream_pool_is_full(monkeypatch):
    sockets = set()
    cookies = []
    release = asyncio.Event()
    reads_ready = asyncio.Event()
    streaming_ready = asyncio.Event()
    read_sockets = set()
    stream_sockets = set()

    async def serve(reader, writer):
        sockets.add(writer)
        try:
            while True:
                headers = await reader.readuntil(b"\r\n\r\n")
                if b"\r\ncookie:" in headers.lower():
                    cookies.append(headers)
                if headers.startswith(b"GET /stream "):
                    stream_sockets.add(writer)
                    if len(stream_sockets) == 2:
                        streaming_ready.set()
                    await release.wait()
                else:
                    read_sockets.add(writer)
                    if len(read_sockets) == 6:
                        reads_ready.set()
                    await reads_ready.wait()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nSet-Cookie: marker=test; Path=/\r\n\r\nok")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    target = runtime.NativeTarget(url, "personal", "synthetic")
    monkeypatch.setattr(runtime, "_clients", {})
    monkeypatch.setattr(runtime, "_sync", {})
    reads = runtime.target_client(target, False)
    streams = runtime.target_client(target, True)
    held = [asyncio.create_task(streams.get(url + "/stream")) for _ in range(3)]
    try:
        await asyncio.wait_for(streaming_ready.wait(), 5)
        for _ in range(2):
            results = await asyncio.wait_for(asyncio.gather(*(reads.get(url) for _ in range(20))), 5)
            assert all(r.text == "ok" for r in results)
            assert len(read_sockets) == 6
        assert len(sockets) == 8
        assert not cookies
    finally:
        release.set()
        await asyncio.gather(*held, return_exceptions=True)
        await runtime.close_native_runtime()
        server.close()
        await server.wait_closed()
    assert reads.is_closed and streams.is_closed


async def test_port_change_and_delete_retire_both_clients(gateway, db_session, monkeypatch):
    client, _, tenants = gateway
    ws = await register(client, db_session, "port_fixture")

    async def transfer(*args):
        pass

    monkeypatch.setattr(runtime, "_transfer", transfer)
    path = NATIVE + "/settings/workspaces"
    assert (await client.get(path)).status_code == 200
    state = runtime._sync[ws.slug]
    old = state.target
    old_clients = [runtime.target_client(old, streaming) for streaming in (True, False)]
    state.check_after = 0
    tenants.ensure.side_effect = lambda slug, role: SimpleNamespace(
        base_url="http://tenant.invalid:8765", workspace_slug=slug, api_token=old.api_token)
    assert (await client.get(path)).status_code == 200
    assert all(c.is_closed for c in old_clients)
    with pytest.raises(public_native.HTTPException) as unavailable:
        runtime.target_client(old, False)
    assert unavailable.value.status_code == 503
    current = [runtime.target_client(state.target, streaming) for streaming in (True, False)]
    response = await client.request("DELETE", AUTH + "/account", json={"password": PASSWORD},
                                    headers={"x-expected-workspace": str(ws.id)})
    assert response.status_code == 204
    assert all(c.is_closed for c in current)
