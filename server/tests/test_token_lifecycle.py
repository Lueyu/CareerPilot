"""H1 behavior checks against an explicitly owned fresh PostgreSQL installation."""
import asyncio
import hashlib
import json
import os
import secrets
import sys
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import ApiRateBucket, LocalCredential, LocalWorkspaceBoundary, Workspace, WorkspaceMember
from app.services.local_credentials import authenticate, enroll, revoke, rotate
from app.services.repository_credentials import repository_credentials


@pytest_asyncio.fixture
async def lifecycle(db_session, test_workspace, monkeypatch):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    cfg = get_settings()
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "default_workspace_slug", test_workspace.slug)
    async def database():
        async with factory() as session:
            yield session
    monkeypatch.setitem(app.dependency_overrides, get_session, database)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
        yield client, factory, test_workspace, cfg


async def install(factory, *, workspace=None):
    raw = secrets.token_urlsafe(32)
    async with factory.begin() as session:
        row = await enroll(session, raw, workspace=workspace)
    return raw, row.id


async def status(raw):
    try:
        principal = await authenticate(raw)
        return 200, principal
    except HTTPException as exc:
        return exc.status_code, exc


async def peer(factory, raw, *, http=False, forwarded="203.0.113.1", attempts=1, startup=False):
    program = '''
import asyncio,json,os
from contextlib import AsyncExitStack
from fastapi import HTTPException
from app.db import engine
from app.services.local_credentials import authenticate
async def main():
    if os.environ['H1_HTTP']=='1':
        import httpx
        from app.main import app
        async with AsyncExitStack() as stack:
            if os.environ['H1_STARTUP']=='1':
                await stack.enter_async_context(app.router.lifespan_context(app))
            client=await stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture'))
            statuses=[]
            for i in range(int(os.environ['H1_ATTEMPTS'])):
                token=os.environ['H1_RAW'] if int(os.environ['H1_ATTEMPTS'])==1 else os.environ['H1_RAW']+str(i)
                r=await client.get('/api/v1/settings/workspaces',headers={'Authorization':'Bearer '+token,
                    'X-Forwarded-For':os.environ['H1_FORWARDED']+str(i),'X-Workspace':'forged'+str(i)})
                statuses.append(r.status_code)
        print(json.dumps({'status':r.status_code,'retry':r.headers.get('Retry-After'),'statuses':statuses}))
    else:
        try:
            p=await authenticate(os.environ['H1_RAW'])
            print(json.dumps({'status':200,'kind':p.kind,'workspace_id':p.workspace_id}))
        except HTTPException as e:
            print(json.dumps({'status':e.status_code,'retry':(e.headers or {}).get('Retry-After')}))
    await engine.dispose()
asyncio.run(main())
'''
    env = {**os.environ, "DATABASE_URL": factory.kw["bind"].url.render_as_string(hide_password=False),
           "H1_RAW": raw, "H1_HTTP": str(int(http)), "H1_FORWARDED": forwarded, "H1_ATTEMPTS": str(attempts),
           "H1_STARTUP": str(int(startup))}
    if startup:
        env["API_TOKEN"] = raw
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", program, env=env,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
    assert process.returncode == 0, "Peer authentication process failed"
    return json.loads(stdout)


async def cli(factory, *args, env_extra=None):
    env = {**os.environ, "DATABASE_URL": factory.kw["bind"].url.render_as_string(hide_password=False), **(env_extra or {})}
    process = await asyncio.create_subprocess_exec(sys.executable, "token_lifecycle.py", *args, env=env,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
    return process.returncode, stdout.decode(), stderr.decode()


@pytest.mark.asyncio
async def test_configured_token_is_not_implicitly_enrolled(db_session):
    token = get_settings().api_token
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        response = await client.get("/api/v1/settings/workspaces", headers={"X-Api-Token": token})
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_enrollment_is_hash_only_and_headers_have_same_scope(lifecycle):
    client, factory, workspace, _ = lifecycle
    raw, credential_id = await install(factory, workspace=workspace.slug)
    async with factory() as session:
        stored = await session.scalar(text("SELECT row_to_json(c)::text FROM local_credentials c WHERE id=:id"), {"id": credential_id})
    assert raw not in stored
    assert json.loads(stored)["token_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    for headers in ({"Authorization": "Bearer " + raw}, {"X-Api-Token": raw}):
        response = await client.get("/settings/workspaces", headers=headers)
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["workspaces"]] == [workspace.id]
        assert raw not in response.text and "token_hash" not in response.text
    async with factory() as session:
        row = await session.get(LocalCredential, credential_id)
        assert row.last_used_at >= row.created_at and row.expires_at > row.last_used_at
    # Invalid bearer takes precedence over a valid alternative header.
    assert (await client.get("/settings/workspaces", headers={"Authorization": "Bearer invalid", "X-Api-Token": raw})).status_code == 401


@pytest.mark.asyncio
async def test_expiry_revoke_rotation_are_seen_in_other_process(lifecycle):
    _, factory, workspace, _ = lifecycle
    raw, credential_id = await install(factory, workspace=workspace.slug)
    assert (await peer(factory, raw))["workspace_id"] == workspace.id
    successor = secrets.token_urlsafe(32)
    async with factory.begin() as session:
        row = await rotate(session, credential_id, successor)
        successor_id = row.id
    assert (await peer(factory, raw))["status"] == 401
    assert (await peer(factory, successor))["status"] == 200
    async with factory.begin() as session:
        await revoke(session, successor_id)
        await revoke(session, successor_id)
    assert (await peer(factory, successor))["status"] == 401
    expiring, expiring_id = await install(factory)
    async with factory.begin() as session:
        await session.execute(update(LocalCredential).where(LocalCredential.id == expiring_id).values(
            created_at=func.now() - timedelta(days=2), expires_at=func.now() - timedelta(days=1)))
    assert (await peer(factory, expiring))["status"] == 401
    async with factory.begin() as session:
        with pytest.raises(ValueError):
            await enroll(session, raw, workspace=workspace.slug)


@pytest.mark.asyncio
async def test_rotation_race_has_one_successor_and_no_owner_widening(lifecycle):
    _, factory, workspace, _ = lifecycle
    raw, credential_id = await install(factory, workspace=workspace.slug)
    async def attempt():
        candidate = secrets.token_urlsafe(32)
        try:
            async with factory.begin() as session:
                row = await rotate(session, credential_id, candidate)
            return candidate, row
        except ValueError:
            return None
    results = await asyncio.gather(*(attempt() for _ in range(5)))
    winners = [result for result in results if result]
    assert len(winners) == 1
    assert winners[0][1].workspace_id == workspace.id and winners[0][1].kind == "WORKSPACE"
    assert (await status(raw))[0] == 401
    assert (await status(winners[0][0]))[0] == 200


@pytest.mark.asyncio
async def test_last_revoke_and_empty_config_keep_owner_out(lifecycle):
    client, factory, workspace, cfg = lifecycle
    tenant, tenant_id = await install(factory, workspace=workspace.slug)
    owner, _ = await install(factory)
    assert (await client.post("/settings/workspaces", headers={"X-Api-Token": tenant}, json={"name": "forbidden"})).status_code == 403
    async with factory.begin() as session:
        await revoke(session, tenant_id)
    cfg.workspace_token_hashes = {}
    headers = {"X-Api-Token": owner}
    listing = await client.get("/settings/workspaces", headers=headers)
    assert listing.status_code == 200 and workspace.id not in [row["id"] for row in listing.json()["workspaces"]]
    assert (await client.get("/settings/overview", headers={**headers, "X-Workspace": workspace.slug})).status_code == 404
    assert (await client.patch(f"/settings/workspaces/{workspace.id}", headers=headers, json={"name": "forbidden"})).status_code == 404
    assert (await client.get("/settings/llm", headers={"X-Api-Token": tenant})).status_code == 401
    assert (await peer(factory, tenant, http=True))["status"] == 401


@pytest.mark.asyncio
async def test_legacy_migration_preserves_unenrolled_default_and_future_claims(lifecycle, monkeypatch):
    client, factory, workspace, cfg = lifecycle
    second = "uncreated-" + uuid4().hex[:12]
    cfg.workspace_token_hashes = {workspace.slug: "a" * 64, second: "b" * 64}
    raw = secrets.token_urlsafe(32)
    database = factory.kw["bind"].url.database
    rc, out, _ = await cli(factory, "enroll-env", "--confirm", f"ENROLL {database}", env_extra={
        "API_TOKEN": raw, "WORKSPACE_TOKEN_HASHES": json.dumps(cfg.workspace_token_hashes)})
    assert rc == 0 and raw not in out
    cfg.workspace_token_hashes = {}
    monkeypatch.setattr(cfg, "github_token", "must-not-inherit-global-github")
    monkeypatch.setattr(cfg, "gitcode_token", "must-not-inherit-global-gitcode")
    async with factory.begin() as session:
        assert await repository_credentials(session, workspace.id) == {"github_token": "", "gitcode_token": ""}
        session.add(Workspace(slug=second, name="Previously absent tenant"))
    result = await client.get("/settings/workspaces", headers={"X-Api-Token": raw})
    assert result.status_code == 200
    assert not {workspace.slug, second} & {row["slug"] for row in result.json()["workspaces"]}
    assert (await peer(factory, raw, http=True))["status"] == 200


@pytest.mark.asyncio
async def test_personal_creation_cannot_claim_tenant_membership_after_last_revoke(lifecycle):
    client, factory, _, _ = lifecycle
    async with factory.begin() as session:
        personal = await session.scalar(select(Workspace).where(Workspace.slug == "personal"))
        if personal is None:
            personal = Workspace(slug="personal", name="Tenant owns personal")
            session.add(personal)
            await session.flush()
        personal_id = personal.id
        await session.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == personal_id))
    raw, credential_id = await install(factory, workspace="personal")
    owner, _ = await install(factory)
    async with factory.begin() as session:
        await revoke(session, credential_id)
    for route in ("/settings/workspaces/personal", "/settings/workspaces"):
        response = await client.post(route, headers={"X-Api-Token": owner}, json={"name": "cannot borrow tenant"})
        assert response.status_code == 409
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == personal_id)) == 0
    assert (await status(raw))[0] == 401
    async with factory.begin() as session:
        await session.execute(delete(LocalWorkspaceBoundary).where(LocalWorkspaceBoundary.slug == "personal"))


@pytest.mark.asyncio
async def test_workspace_delete_and_slug_reuse_keep_digest_tombstone(lifecycle):
    client, factory, workspace, _ = lifecycle
    raw, credential_id = await install(factory, workspace=workspace.slug)
    response = await client.request("DELETE", "/settings/workspace", headers={"X-Api-Token": raw}, json={"confirm_slug": workspace.slug})
    assert response.status_code == 200
    async with factory.begin() as session:
        session.add(Workspace(slug=workspace.slug, name="Recreated workspace"))
    assert (await status(raw))[0] == 401
    async with factory.begin() as session:
        assert (await session.get(LocalCredential, credential_id)).workspace_id == workspace.id
        with pytest.raises(ValueError):
            await enroll(session, raw, workspace=workspace.slug)
    owner, _ = await install(factory)
    assert (await client.get("/settings/llm", headers={"X-Api-Token": owner})).status_code == 200


@pytest.mark.asyncio
async def test_failed_auth_commits_and_cannot_reset_by_token_forwarded_ip_or_process(lifecycle):
    client, factory, workspace, _ = lifecycle
    valid, _ = await install(factory, workspace=workspace.slug)
    # Mix dependency and actual middleware boundaries. Ten failures consume the one allowance.
    for _ in range(8):
        assert (await status(secrets.token_urlsafe(32)))[0] == 401
    peers = await asyncio.gather(*(peer(factory, secrets.token_urlsafe(32), http=True, forwarded=f"192.0.2.{i}", attempts=6) for i in range(2)))
    assert all(result["status"] == 429 for result in peers)
    blocked = await peer(factory, secrets.token_urlsafe(32), http=True, forwarded="10.1.2.3", attempts=6)
    assert blocked["status"] == 429 and int(blocked["retry"]) > 0
    direct, exc = await status("another-forged-token")
    assert direct == 429 and int(exc.headers["Retry-After"]) > 0
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApiRateBucket).where(ApiRateBucket.subject == f"workspace:{workspace.id}")) == 0
    assert (await client.get("/settings/workspaces", headers={"X-Api-Token": valid})).status_code == 200


@pytest.mark.asyncio
async def test_registry_failure_never_falls_back_to_env(lifecycle):
    client, factory, _, cfg = lifecycle
    raw, _ = await install(factory)
    cfg.api_token = raw
    async with factory.begin() as session:
        await session.execute(text("ALTER TABLE local_credentials RENAME TO h1_temporarily_missing"))
    try:
        response = await client.get("/settings/workspaces", headers={"X-Api-Token": raw})
        assert response.status_code == 503 and response.json()["detail"]["code"] == "AUTH_REGISTRY_UNAVAILABLE"
        assert raw not in response.text
    finally:
        async with factory.begin() as session:
            await session.execute(text("ALTER TABLE h1_temporarily_missing RENAME TO local_credentials"))


@pytest.mark.asyncio
async def test_generation_and_quarantine_prevent_archive_or_static_resurrection(lifecycle):
    _, factory, _, _ = lifecycle
    raw, credential_id = await install(factory)
    async with factory.begin() as session:
        original = await session.scalar(text("SELECT restore_id FROM recovery_control"))
        await session.execute(text("UPDATE recovery_control SET restore_id=:id"), {"id": uuid4().hex})
    try:
        assert (await peer(factory, raw))["status"] == 401
        async with factory.begin() as session:
            await session.execute(text("UPDATE recovery_control SET origin='RESTORE',access_state='QUARANTINED',paid_state='BLOCKED',credentials_state='BLOCKED'"))
        assert (await peer(factory, raw, http=True))["status"] == 503
        for command in (("enroll-env", "--confirm", f"ENROLL {factory.kw['bind'].url.database}"),
                        ("issue", "--show-token"), ("rotate", credential_id, "--show-token")):
            rc, out, err = await cli(factory, *command)
            assert rc == 1 and raw not in out + err
    finally:
        async with factory.begin() as session:
            await session.execute(text("UPDATE recovery_control SET restore_id=:id,origin='INSTALLATION',access_state='NORMAL',paid_state='NORMAL',credentials_state='NORMAL'"), {"id": original})


@pytest.mark.asyncio
async def test_cli_private_output_rotate_revoke_and_failed_output_preserves_old(lifecycle, tmp_path):
    _, factory, workspace, _ = lifecycle
    output = tmp_path / "issued.token"
    rc, out, err = await cli(factory, "issue", "--workspace", workspace.slug, "--output", str(output))
    assert rc == 0, err
    raw = output.read_text().strip()
    metadata = json.loads(out)
    assert raw not in out + err and "token_hash" not in metadata
    # A protected existing output is never overwritten; no revoke may commit.
    rc, _, _ = await cli(factory, "rotate", metadata["id"], "--output", str(output))
    assert rc == 1 and output.read_text().strip() == raw and (await status(raw))[0] == 200
    rotated = tmp_path / "rotated.token"
    rc, out, _ = await cli(factory, "rotate", metadata["id"], "--output", str(rotated))
    assert rc == 0
    successor = rotated.read_text().strip()
    assert (await peer(factory, raw))["status"] == 401 and (await peer(factory, successor))["status"] == 200
    successor_id = json.loads(out)["id"]
    assert (await cli(factory, "revoke", successor_id))[0] == 0
    rc, listed, _ = await cli(factory, "list")
    assert rc == 0 and raw not in listed and successor not in listed and "token_hash" not in listed
    assert (await peer(factory, successor))["status"] == 401


@pytest.mark.asyncio
async def test_explicit_env_enrollment_is_once_and_restart_does_not_reenable(lifecycle):
    _, factory, _, _ = lifecycle
    raw = secrets.token_urlsafe(32)
    command = ("enroll-env", "--confirm", f"ENROLL {factory.kw['bind'].url.database}")
    rc, out, _ = await cli(factory, *command, env_extra={"API_TOKEN": raw})
    assert rc == 0
    credential_id = json.loads(out)["id"]
    assert (await cli(factory, "revoke", credential_id))[0] == 0
    rc, out, err = await cli(factory, *command, env_extra={"API_TOKEN": raw})
    assert rc == 1 and raw not in out + err
    assert (await peer(factory, raw))["status"] == 401
    for _ in range(2):
        assert (await peer(factory, raw, http=True, startup=True))["status"] == 401
    unknown = secrets.token_urlsafe(32)
    assert (await peer(factory, unknown, http=True, startup=True))["status"] == 401
    async with factory() as session:
        assert await session.scalar(select(LocalCredential.id).where(LocalCredential.token_hash == hashlib.sha256(unknown.encode()).hexdigest())) is None


@pytest.mark.asyncio
async def test_cli_scope_only_migration_and_raw_input_enrollment(lifecycle, tmp_path):
    _, factory, workspace, _ = lifecycle
    database = factory.kw["bind"].url.database
    env = {"WORKSPACE_TOKEN_HASHES": json.dumps({workspace.slug: "f" * 64})}
    command = ("migrate-scopes", "--confirm", f"ENROLL {database}")
    assert (await cli(factory, *command, env_extra=env))[0] == 0
    assert (await cli(factory, *command, env_extra=env))[0] == 0
    async with factory() as session:
        assert await session.get(LocalWorkspaceBoundary, workspace.slug) is not None
        before = await session.scalar(select(func.count()).select_from(LocalCredential))
    raw = secrets.token_urlsafe(32)
    from personal_credentials import create_private_empty
    input_file = tmp_path / "legacy.token"
    create_private_empty(input_file)
    input_file.write_text(raw)
    rc, out, err = await cli(factory, "enroll", "--input-file", str(input_file), "--workspace", workspace.slug,
                             "--confirm", f"ENROLL {database}", env_extra=env)
    assert rc == 0 and raw not in out + err
    assert (await status(raw))[1].workspace_id == workspace.id
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(LocalCredential)) == before + 1


@pytest.mark.asyncio
async def test_cli_preflight_rejects_before_engine_or_output(lifecycle, tmp_path):
    _, factory, _, _ = lifecycle
    output = tmp_path / "must-not-exist.token"
    sentinel = "private-connection-input-must-not-echo-8934"
    rc, out, err = await cli(factory, "issue", "--output", str(output), env_extra={
        "DATABASE_URL": f"postgresql+asyncpg://owned:{sentinel}@127.0.0.1:invalid/fixture_test"})
    assert rc == 1 and sentinel not in out + err and not output.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", "cp-dev-token", "a" * 64, "test-token-" + "2" * 40])
async def test_enrollment_rejects_weak_input_without_rows(lifecycle, raw):
    _, factory, workspace, _ = lifecycle
    async with factory.begin() as session:
        before = await session.scalar(select(func.count()).select_from(LocalCredential))
        with pytest.raises(RuntimeError):
            await enroll(session, raw, workspace=workspace.slug)
        assert await session.scalar(select(func.count()).select_from(LocalCredential)) == before
