"""遥测(telemetry) API 测试: 匿名事件收集 + 所有者汇总。"""
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import TelemetryEvent


@pytest.mark.asyncio
async def test_collect_events_is_anonymous_and_204(db_session, test_workspace, monkeypatch):
    """匿名事件收集: 无需认证, 返回 204, 事件落库。"""
    db = db_session
    _fixture_ws = test_workspace
    cfg = get_settings()
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "api_token", "owner-" + uuid4().hex)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as conn:
            yield conn

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            body = {
                "client_id": "test-client-0001",
                "events": [
                    {"name": "page_view", "page": "/bank"},
                    {"name": "chat.send", "props": {"action": "explain"}},
                ],
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "device": "desktop",
            }
            resp = await client.post("/telemetry/events", json=body)
            assert resp.status_code == 204, resp.text

            rows = (await db.execute(select(TelemetryEvent).order_by(TelemetryEvent.id))).scalars().all()
            assert len(rows) == 2
            assert rows[0].client_id == "test-client-0001"
            assert rows[0].event == "page_view"
            assert rows[0].page == "/bank"
            assert rows[0].region == "中国大陆"
            assert rows[0].locale == "zh-CN"
            assert rows[0].ip_hash and len(rows[0].ip_hash) == 64
            assert rows[1].props == {"action": "explain"}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_collect_events_validates_names(db_session, test_workspace, monkeypatch):
    """非法 client_id/事件名/空批次 → 422。"""
    db = db_session
    _fixture_ws = test_workspace
    cfg = get_settings()
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "api_token", "owner-" + uuid4().hex)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as conn:
            yield conn

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            assert (await client.post("/telemetry/events", json={"client_id": "x", "events": [{"name": "ok"}]})).status_code == 422
            assert (await client.post("/telemetry/events", json={"client_id": "test-client-0002", "events": [{"name": "Bad Event!"}]})).status_code == 422
            assert (await client.post("/telemetry/events", json={"client_id": "test-client-0003", "events": []})).status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_summary_requires_owner(db_session, test_workspace, monkeypatch):
    """summary 需本地 owner(未带 token → 401)。"""
    db = db_session
    _fixture_ws = test_workspace
    cfg = get_settings()
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "api_token", "owner-" + uuid4().hex)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as conn:
            yield conn

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            assert (await client.get("/telemetry/summary")).status_code == 401
            ok = await client.get("/telemetry/summary", headers={"X-Api-Token": cfg.api_token})
            assert ok.status_code == 200, ok.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_summary_aggregates(db_session, test_workspace, monkeypatch):
    """汇总: 用户数/活跃/事件/页面/设备/地域/趋势。"""
    db = db_session
    _fixture_ws = test_workspace
    cfg = get_settings()
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "api_token", "owner-" + uuid4().hex)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as conn:
            yield conn

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    try:
        # 清除其他测试写入的事件, 保证聚合断言独立
        await db.execute(TelemetryEvent.__table__.delete())
        await db.commit()
        db.add_all([
            TelemetryEvent(client_id="u1", event="page_view", page="/", device="desktop", locale="zh-CN", timezone="Asia/Shanghai", region="中国大陆", ip_hash="h1"),
            TelemetryEvent(client_id="u1", event="page_view", page="/bank", device="desktop", locale="zh-CN", timezone="Asia/Shanghai", region="中国大陆", ip_hash="h1"),
            TelemetryEvent(client_id="u2", event="chat.send", page="/study/chat", device="mobile", locale="en", timezone="America/New_York", region="美国", ip_hash="h2"),
        ])
        await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            resp = await client.get("/telemetry/summary", headers={"X-Api-Token": cfg.api_token})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["users"]["total"] == 2
            assert data["users"]["active_1d"] == 2
            assert data["events_total"] == 3
            assert [e["name"] for e in data["top_events"]] == ["page_view", "chat.send"]
            assert {p["page"] for p in data["top_pages"]} == {"/", "/bank"}
            assert {d["device"]: d["count"] for d in data["devices"]} == {"desktop": 2, "mobile": 1}
            assert {r["region"]: r["count"] for r in data["regions"]} == {"中国大陆": 2, "美国": 1}
            assert data["trend"] and "date" in data["trend"][0]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_rate_limit_hits_429(db_session, test_workspace, monkeypatch):
    """每 IP 滑动窗口超限 → 429。"""
    db = db_session
    _fixture_ws = test_workspace
    cfg = get_settings()
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "api_token", "owner-" + uuid4().hex)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as conn:
            yield conn

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    from app.api.v1 import telemetry as telemetry_mod
    telemetry_mod._RATE.clear()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            body = {"client_id": "test-client-0004", "events": [{"name": "page_view", "page": "/"}]}
            for _ in range(telemetry_mod._RATE_MAX):
                assert (await client.post("/telemetry/events", json=body)).status_code == 204
            assert (await client.post("/telemetry/events", json=body)).status_code == 429
    finally:
        telemetry_mod._RATE.clear()
        app.dependency_overrides.clear()
