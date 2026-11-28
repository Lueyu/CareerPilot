"""Synthetic credentials only. Every persistence check uses isolated PostgreSQL."""
import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import db
from app.api.v1 import settings as api
from app.config import get_settings
from app.models import AppSetting
from app.services import gitcode_repo, github_publish, llm, podcast_content, project_autopilot_config
from app.services.repository_credentials import repository_credentials
from app.services.secret_storage import SecretStorageError, open_setting, seal_setting
from migrate_secrets import migrate_legacy

CONFIG = {"provider": "synthetic", "base_url": "https://g5.invalid/v1", "model": "fixture",
          "api_key": "synthetic-llm-credential-6789"}


@pytest_asyncio.fixture
async def storage(db_session, monkeypatch):
    monkeypatch.setenv("CAREERPILOT_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(llm, "_CACHE", None)
    cfg = get_settings()
    for field, value in CONFIG.items():
        monkeypatch.setattr(cfg, f"llm_{field}", value if field != "api_key" else "synthetic-unrelated-env-key")
    monkeypatch.setattr(cfg, "github_token", "synthetic-unrelated-env-github")
    monkeypatch.setattr(cfg, "gitcode_token", "synthetic-unrelated-env-gitcode")
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def local_session():
        async with factory() as session:
            yield session

    monkeypatch.setattr(db, "SessionLocal", local_session)
    await db_session.execute(delete(AppSetting))
    await db_session.commit()
    yield db_session, factory
    await db_session.rollback()
    await db_session.execute(delete(AppSetting))
    await db_session.commit()


@pytest.mark.asyncio
async def test_api_write_has_no_raw_database_plaintext(storage):
    session, _ = storage
    secret = "synthetic-github-do-not-store-plain-9234"
    payload = await api.update_github(api.GithubUpdate(token=secret), session)
    raw = await session.scalar(text("SELECT value::text FROM app_settings WHERE key='github'"))
    assert secret not in raw
    assert payload["active"] and payload["token_hint"].endswith("9234")
    assert secret not in str(payload)


@pytest.mark.asyncio
async def test_llm_keep_delete_connection_and_raw_sql(storage, monkeypatch):
    session, _ = storage
    payload = await api.update_llm(api.LlmUpdate(**CONFIG), session)
    assert payload["active"] and payload["api_key_set"] and "api_key" not in payload
    for key in (None, "", "   "):
        await api.update_llm(api.LlmUpdate(model="edited", api_key=key), session)
        assert (await llm.get_llm_config())["api_key"] == CONFIG["api_key"]
    await api.update_llm(api.LlmUpdate(model="omitted"), session)
    raw = await session.scalar(text("SELECT value::text FROM app_settings WHERE key='llm'"))
    assert CONFIG["api_key"] not in raw
    transport = AsyncMock(return_value={"content": "OK"})
    monkeypatch.setattr(api.adapter, "chat_with", transport)
    assert (await api.test_llm(api.LlmTest(), SimpleNamespace(id=42)))["ok"]
    assert transport.call_args.args[0]["api_key"] == CONFIG["api_key"]
    # A test-only credential is not saved.
    await api.test_llm(api.LlmTest(api_key="synthetic-one-off"), SimpleNamespace(id=42))
    assert "synthetic-one-off" not in await session.scalar(text("SELECT value::text FROM app_settings WHERE key='llm'"))
    await api.reset_llm(session)
    assert (await llm.get_llm_config())["api_key"] == "synthetic-unrelated-env-key"
    await api.reset_github(session)
    assert await github_publish.get_github_token() == "synthetic-unrelated-env-github"


@pytest.mark.asyncio
async def test_workspace_credentials_and_all_nested_variants(storage, test_workspace, monkeypatch):
    session, _ = storage
    ws = test_workspace
    monkeypatch.setattr(get_settings(), "default_workspace_slug", ws.slug)
    key = f"llm:{ws.id}"
    variants = {**CONFIG, "strong_api_key": "synthetic-strong", "reasoning_api_key": "synthetic-reasoning",
                "profiles": [{"api_key": "synthetic-nested"}], "credentials": {"password": "synthetic-password"}}
    session.add(AppSetting(key=key, value=seal_setting(key, variants)))
    gh_key = f"github:{ws.id}"
    session.add(AppSetting(key=gh_key, value=seal_setting(gh_key, {"token": "synthetic-scoped-github"})))
    await session.commit()
    await api.update_gitcode_read_settings(api.GithubUpdate(token="synthetic-scoped-gitcode"), ws, session)
    assert (await api.get_gitcode_read_settings(ws, session))["active"]
    config, credentials, _ = await project_autopilot_config.runtime_config(session, ws.id, require_consent=False)
    assert config["api_key"] == CONFIG["api_key"]
    assert credentials == {"github_token": "synthetic-scoped-github", "gitcode_token": "synthetic-scoped-gitcode"}
    assert open_setting(key, (await session.get(AppSetting, key)).value) == variants
    raw = await session.scalar(text("SELECT string_agg(value::text, ',') FROM app_settings"))
    for secret in (CONFIG["api_key"], "synthetic-strong", "synthetic-reasoning", "synthetic-nested",
                   "synthetic-password", *credentials.values()):
        assert secret not in raw
    # Podcast consumes the same authenticated workspace tuple before its transport boundary.
    transport = AsyncMock(return_value=None)
    monkeypatch.setattr(podcast_content.AgentAdapter, "chat_with", transport)
    with pytest.raises(podcast_content.PodcastScriptError):
        await podcast_content.make_scripts(session, ws.id, title="Synthetic", answer="An independent fixture.",
                                           topic="test", series=False, source="synthetic")
    assert transport.call_args.args[0]["api_key"] == CONFIG["api_key"]
    await api.reset_gitcode_read_settings(ws, session)
    assert not (await api.get_gitcode_read_settings(ws, session))["active"]


def test_envelope_binds_row_field_and_list_position(monkeypatch):
    monkeypatch.setenv("CAREERPILOT_SECRETS_KEY", Fernet.generate_key().decode())
    original = {"api_key": "synthetic-main", "strong_api_key": "synthetic-strong",
                "profiles": [{"api_key": "synthetic-first"}, {"api_key": "synthetic-second"}]}
    sealed = seal_setting("llm:1", original)
    assert open_setting("llm:1", sealed) == original
    assert seal_setting("llm:1", sealed) == sealed
    for key, value in (("llm:2", sealed), ("llm", sealed), ("github:1", {"token": sealed["api_key"]})):
        with pytest.raises(SecretStorageError):
            open_setting(key, value)
    swapped = deepcopy(sealed)
    swapped["api_key"], swapped["strong_api_key"] = swapped["strong_api_key"], swapped["api_key"]
    with pytest.raises(SecretStorageError):
        open_setting("llm:1", swapped)
    swapped = deepcopy(sealed)
    swapped["profiles"].reverse()
    with pytest.raises(SecretStorageError):
        open_setting("llm:1", swapped)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["legacy", "missing", "wrong", "tamper", "version", "row", "field"])
@pytest.mark.parametrize("caller", ["llm", "connection", "runtime", "runtime_workspace", "podcast", "podcast_workspace",
                                    "github", "publisher", "repository", "repository_gitcode", "gitcode", "gitcode_status"])
async def test_actual_callers_fail_closed_before_network(storage, test_workspace, monkeypatch, caplog, fault, caller):
    session, _ = storage
    ws = test_workspace
    monkeypatch.setattr(get_settings(), "default_workspace_slug", ws.slug)
    if caller in {"github", "publisher"}:
        key, field = "github", "token"
    elif caller in {"repository", "repository_gitcode"}:
        key, field = f"{'github' if caller == 'repository' else 'gitcode'}:{ws.id}", "token"
    elif caller == "gitcode":
        key, field = "gitcode", "token"
    elif caller == "gitcode_status":
        key, field = f"gitcode:{ws.id}", "token"
    elif caller in {"runtime_workspace", "podcast_workspace"}:
        key, field = f"llm:{ws.id}", "api_key"
    else:
        key, field = "llm", "api_key"
    secret = "synthetic-secret-must-never-escape"
    value = {**CONFIG, field: secret} if key.startswith("llm") else {field: secret}
    sealed = seal_setting(key, value)
    if fault == "legacy":
        sealed = value
    elif fault == "missing":
        monkeypatch.delenv("CAREERPILOT_SECRETS_KEY")
    elif fault == "wrong":
        monkeypatch.setenv("CAREERPILOT_SECRETS_KEY", Fernet.generate_key().decode())
    elif fault == "tamper":
        sealed[field]["ciphertext"] = sealed[field]["ciphertext"][:25] + "AAAA"
    elif fault == "version":
        sealed[field]["$secret"] = "fernet-v999"
    elif fault == "row":
        sealed[field] = seal_setting(key + ":999", {field: secret})[field]
    elif fault == "field":
        sealed[field] = seal_setting(key, {"strong_api_key": secret})["strong_api_key"]
    session.add(AppSetting(key=key, value=sealed))
    await session.commit()
    network, billing, adapter = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "send", network)
    monkeypatch.setattr(llm, "reserve", billing)
    monkeypatch.setattr(llm.AgentAdapter, "chat_with", adapter)
    # Stale plaintext from an earlier successful call cannot bypass current storage validation.
    llm.set_llm_config_cache(CONFIG)
    github_publish.set_github_token_cache("synthetic-stale-token")
    with pytest.raises(SecretStorageError) as caught:
        if caller == "llm":
            await llm.AgentAdapter().chat("system", "user")
        elif caller == "connection":
            await api.test_llm(api.LlmTest(api_key="synthetic-replacement"), ws)
        elif caller.startswith("runtime"):
            await project_autopilot_config.runtime_config(session, ws.id, require_consent=False)
        elif caller.startswith("podcast"):
            await podcast_content.make_scripts(session, ws.id, title="Fixture", answer="Synthetic answer",
                                               topic="test", series=False, source="synthetic")
        elif caller == "github":
            await github_publish.get_github_token()
        elif caller == "publisher":
            await github_publish.make_publisher_rt()
        elif caller.startswith("repository"):
            await repository_credentials(session, ws.id)
        elif caller == "gitcode":
            await gitcode_repo.get_gitcode_token()
        else:
            await api.get_gitcode_read_settings(ws, session)
    assert caught.value.status_code == 503
    assert secret not in str(caught.value) + caplog.text
    network.assert_not_awaited()
    billing.assert_not_awaited()
    adapter.assert_not_awaited()


@pytest.mark.asyncio
async def test_migration_real_postgres_rollback_retry_idempotence_and_cli(storage, monkeypatch):
    session, factory = storage
    plaintext = {"token": "synthetic-first-flushed"}
    valid = seal_setting("llm", CONFIG)
    broken = deepcopy(valid)
    broken["api_key"]["$secret"] = "unknown"
    session.add_all([AppSetting(key="github", value=plaintext), AppSetting(key="llm", value=broken)])
    await session.commit()
    async with factory() as migration:
        with pytest.raises(SecretStorageError):
            await migrate_legacy(migration)
    async with factory() as check:
        assert (await check.get(AppSetting, "github")).value == plaintext
        assert (await check.get(AppSetting, "llm")).value == broken
    # Repair only synthetic test data and retry the exact same migration operation.
    (await session.get(AppSetting, "llm")).value = CONFIG
    await session.commit()
    async with factory() as migration:
        assert await migrate_legacy(migration) == {"checked_rows": 2, "changed_rows": 2}
    async with factory() as check:
        before = await check.scalar(text("SELECT jsonb_object_agg(key, value)::text FROM app_settings"))
        assert CONFIG["api_key"] not in before and plaintext["token"] not in before
    async with factory() as migration:
        assert await migrate_legacy(migration) == {"checked_rows": 2, "changed_rows": 0}
    async with factory() as check:
        assert await check.scalar(text("SELECT jsonb_object_agg(key, value)::text FROM app_settings")) == before
    env = {**os.environ, "G5_MIGRATION_TEST_URL": os.environ["CAREERPILOT_TEST_DATABASE_URL"]}
    command = [sys.executable, "migrate_secrets.py", "--database-url-env", "G5_MIGRATION_TEST_URL", "--apply"]
    result = await asyncio.to_thread(subprocess.run, command, env=env, capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)["changed_rows"] == 0
    env["CAREERPILOT_SECRETS_KEY"] = Fernet.generate_key().decode()
    result = await asyncio.to_thread(subprocess.run, command, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == ""
    assert CONFIG["api_key"] not in result.stdout + result.stderr
    assert env["CAREERPILOT_SECRETS_KEY"] not in result.stdout + result.stderr


@pytest.mark.asyncio
async def test_parallel_migration_converges(storage):
    session, factory = storage
    session.add(AppSetting(key="llm", value=CONFIG))
    await session.commit()

    async def migrate():
        async with factory() as connection:
            return await migrate_legacy(connection)

    results = await asyncio.gather(migrate(), migrate())
    assert sorted(result["changed_rows"] for result in results) == [0, 1]


@pytest.mark.asyncio
async def test_authenticated_http_mask_export_and_missing_key(storage, test_workspace, monkeypatch, caplog):
    from app.db import get_session
    from app.main import app

    _, factory = storage
    cfg = get_settings()
    from secrets import token_urlsafe

    from credential_fixtures import enroll_token
    monkeypatch.setattr(cfg, "api_token", token_urlsafe(32))
    await enroll_token(storage[0], cfg.api_token)
    monkeypatch.setattr(cfg, "default_workspace_slug", test_workspace.slug)

    async def request_session():
        async with factory() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, request_session)
    headers = {"X-Api-Token": cfg.api_token, "X-Workspace": test_workspace.slug}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
        assert (await client.get("/settings/llm")).status_code == 401
        saved = await client.put("/settings/llm", json=CONFIG, headers=headers)
        assert saved.status_code == 200, saved.text
        exported = await client.get("/settings/export", headers=headers)
        assert exported.status_code == 200, exported.text
        assert "app_settings" not in exported.text and CONFIG["api_key"] not in exported.text
        monkeypatch.delenv("CAREERPILOT_SECRETS_KEY")
        response = await client.get("/settings/llm", headers=headers)
        assert response.status_code == 503
        assert CONFIG["api_key"] not in response.text + caplog.text


@pytest.mark.asyncio
async def test_missing_key_write_is_not_persisted(storage, monkeypatch):
    session, _ = storage
    monkeypatch.delenv("CAREERPILOT_SECRETS_KEY")
    with pytest.raises(SecretStorageError):
        await api.update_llm(api.LlmUpdate(**CONFIG), session)
    await session.rollback()
    assert await session.get(AppSetting, "llm") is None


@pytest.mark.asyncio
async def test_bad_global_record_cannot_fall_back_on_database_failure(storage, monkeypatch):
    @asynccontextmanager
    async def unavailable():
        raise RuntimeError("synthetic-private-connection-detail")
        yield

    monkeypatch.setattr(db, "SessionLocal", unavailable)
    for resolver in (llm.get_llm_config, github_publish.get_github_token):
        with pytest.raises(SecretStorageError) as caught:
            await resolver()
        assert "synthetic-private" not in str(caught.value)


@pytest.mark.asyncio
async def test_successful_read_caches_only_readiness_booleans(storage):
    session, _ = storage
    await api.update_llm(api.LlmUpdate(**CONFIG), session)
    assert llm.AgentAdapter().active
    assert llm._CACHE == {"provider": True, "api_key": True}
    assert CONFIG["api_key"] not in str(llm._CACHE)


@pytest.mark.asyncio
async def test_migration_covers_every_persisted_family(storage, test_workspace, monkeypatch):
    session, factory = storage
    ws = test_workspace
    monkeypatch.setattr(get_settings(), "default_workspace_slug", ws.slug)
    values = {
        "llm": {**CONFIG, "strong_api_key": "synthetic-legacy-strong"},
        f"llm:{ws.id}": {**CONFIG, "reasoning": {"api_key": "synthetic-legacy-reasoning"}},
        "github": {"token": "synthetic-legacy-global-github"},
        f"github:{ws.id}": {"token": "synthetic-legacy-scoped-github"},
        "gitcode": {"token": "synthetic-legacy-global-gitcode"},
        f"gitcode:{ws.id}": {"token": "synthetic-legacy-scoped-gitcode"},
    }
    session.add_all(AppSetting(key=key, value=value) for key, value in values.items())
    await session.commit()
    async with factory() as migration:
        assert await migrate_legacy(migration) == {"checked_rows": 6, "changed_rows": 6}
    async with factory() as check:
        raw = await check.scalar(text("SELECT jsonb_object_agg(key, value)::text FROM app_settings"))
        for key, value in values.items():
            assert open_setting(key, (await check.get(AppSetting, key)).value) == value
        assert "synthetic-legacy" not in raw and CONFIG["api_key"] not in raw
    assert await github_publish.get_github_token() == values["github"]["token"]
    assert await gitcode_repo.get_gitcode_token() == values["gitcode"]["token"]
    assert (await llm.get_llm_config())["api_key"] == CONFIG["api_key"]
    # Use a fresh session because earlier migration changed this session's identity-map data.
    async with factory() as check:
        config, creds, _ = await project_autopilot_config.runtime_config(check, ws.id, require_consent=False)
        assert config["api_key"] == CONFIG["api_key"]
        assert creds == {"github_token": values[f"github:{ws.id}"]["token"],
                         "gitcode_token": values[f"gitcode:{ws.id}"]["token"]}
