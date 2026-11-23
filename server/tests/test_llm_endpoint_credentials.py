from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app import db
from app.api.v1 import settings
from app.services import llm, project_autopilot_config


@pytest.fixture
def isolated(monkeypatch):
    env = SimpleNamespace(
        llm_provider="compatible", llm_base_url="https://old.invalid/v1",
        llm_api_key="synthetic-env", llm_model="original",
        study_flash_model="", study_strong_model="deep",
        study_strong_base_url="", study_strong_api_key="", study_strong_provider="",
    )
    session = AsyncMock()
    selected = Mock()
    selected.scalar_one_or_none.return_value = None
    session.execute.return_value = selected
    session.add = Mock(side_effect=lambda row: setattr(selected.scalar_one_or_none, "return_value", row))

    @asynccontextmanager
    async def local_session():
        yield session

    monkeypatch.setattr(db, "SessionLocal", local_session)
    for module in (llm, settings, project_autopilot_config, project_autopilot_config.study_routing):
        monkeypatch.setattr(module, "get_settings", lambda: env)
    monkeypatch.setattr(llm, "_CACHE", None)
    monkeypatch.setattr(llm, "_CACHE_AT", 0.0)
    monkeypatch.setattr(settings.adapter, "chat_with", AsyncMock(return_value={"content": "OK"}))
    return env, session, selected.scalar_one_or_none


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"base_url": "https://old.invalid/v2"},
    {"provider": "other"},
    {"base_url": "https://new.invalid/v1", "api_key": "  "},
])
async def test_endpoint_change_requires_key(isolated, change):
    _, session, _ = isolated
    with pytest.raises(HTTPException, match="API 密钥") as save:
        await settings.update_llm(settings.LlmUpdate(**change), session)
    probe = await settings.test_llm(settings.LlmTest(**change))
    assert not probe["ok"] and "API 密钥" in probe["error"]
    assert save.value.status_code == 422
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
    settings.adapter.chat_with.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_endpoint_edits_and_explicit_key_switch(isolated):
    env, session, selected = isolated
    assert (await settings.get_llm())["source"] == "env"
    payload = await settings.update_llm(
        settings.LlmUpdate(base_url=env.llm_base_url + "/", model="updated", api_key=""), session
    )
    assert payload["active"] and payload["model"] == "updated"
    await settings.test_llm(settings.LlmTest(model="preview"))
    assert settings.adapter.chat_with.call_args.args[0]["api_key"] == env.llm_api_key

    endpoint = "https://new.invalid/v1"
    body = {"base_url": endpoint, "api_key": "synthetic-new"}
    await settings.test_llm(settings.LlmTest(**body))
    sent = settings.adapter.chat_with.call_args.args[0]
    assert (sent["base_url"], sent["api_key"]) == (endpoint, body["api_key"])
    payload = await settings.update_llm(settings.LlmUpdate(**body), session)
    assert payload["api_key_set"] and "api_key" not in payload
    assert selected().value["provider"] == env.llm_provider

    payload = await settings.update_llm(settings.LlmUpdate(model="next", api_key=" "), session)
    assert payload["active"] and payload["model"] == "next"
    assert selected().value["api_key"] == body["api_key"]

    with pytest.raises(HTTPException, match="API 密钥"):
        await settings.update_llm(settings.LlmUpdate(base_url=""), session)
    assert selected().value["base_url"] == endpoint
    env.llm_provider = "different-default"
    env.llm_base_url = "https://different.invalid/v1"
    llm.set_llm_config_cache(None)
    payload = await settings.get_llm()
    assert payload["active"] and payload["base_url"] == endpoint
    assert payload["provider"] != env.llm_provider


@pytest.mark.asyncio
async def test_existing_keyless_override_cannot_inherit_env_key(isolated, monkeypatch):
    env, session, selected = isolated
    override = SimpleNamespace(value={"base_url": "https://new.invalid/v1"})
    selected.return_value = override
    payload = await settings.get_llm()
    assert not payload["active"] and not payload["api_key_set"]
    assert payload["base_url"] == override.value["base_url"]
    probe = await settings.test_llm(settings.LlmTest())
    assert not probe["ok"] and "API 密钥" in probe["error"]
    settings.adapter.chat_with.assert_not_awaited()

    monkeypatch.setattr(project_autopilot_config, "repository_credentials", AsyncMock(return_value={}))
    for key in ("llm:1", "llm", None):
        session.get.side_effect = lambda _, name: override if name == key else None
        config, _, _ = await project_autopilot_config.runtime_config(session, 1, require_consent=False)
        expected = "" if key else env.llm_api_key
        assert config["api_key"] == config["auto_profiles"]["flash"]["api_key"] == expected
        assert config["auto_profiles"]["strong"]["api_key"] == expected
