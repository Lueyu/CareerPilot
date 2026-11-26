"""工业级 agent 观测(ops) API 测试: 指标汇总 / 明细 / 健康。"""
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import AgentMetric


async def _setup(db_session, monkeypatch):
    db = db_session
    _fixture_ws = db_session  # noqa: F841 (夹具占位)
    cfg = get_settings()
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "api_token", "owner-" + uuid4().hex)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as conn:
            yield conn

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    return db, cfg, request_db


@pytest.mark.asyncio
async def test_ops_summary_requires_owner(db_session, test_workspace, monkeypatch):
    db, cfg, _ = await _setup(db_session, monkeypatch)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            assert (await client.get("/ops/metrics/summary")).status_code == 401
            ok = await client.get("/ops/metrics/summary", headers={"X-Api-Token": cfg.api_token})
            assert ok.status_code == 200, ok.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ops_summary_aggregates_agent_metrics(db_session, test_workspace, monkeypatch):
    db, cfg, _ = await _setup(db_session, monkeypatch)
    try:
        await db.execute(AgentMetric.__table__.delete())
        await db.commit()
        db.add_all([
            AgentMetric(kind="llm", provider="gateway", model="deepseek-v4-pro", duration_ms=1200,
                        ttft_ms=300, prompt_tokens=100, completion_tokens=200, total_tokens=300,
                        cost_cny=0.0006, ok=True, finish_reason="stop", stream=False),
            AgentMetric(kind="llm", provider="gateway", model="deepseek-v4-flash", duration_ms=300,
                        ttft_ms=80, prompt_tokens=50, completion_tokens=60, total_tokens=110,
                        cost_cny=0.00022, ok=False, error_kind="timeout", finish_reason=None, stream=True),
            AgentMetric(kind="llm", provider="gateway", model="deepseek-v4-flash", duration_ms=450,
                        ttft_ms=90, prompt_tokens=40, completion_tokens=70, total_tokens=110,
                        cost_cny=0.00022, ok=True, finish_reason="length", stream=True),
        ])
        await db.commit()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            resp = await client.get("/ops/metrics/summary", headers={"X-Api-Token": cfg.api_token})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            agent = data["agent"]
            assert agent["calls"] == 3
            assert agent["ok_rate"] == pytest.approx(66.7, abs=0.1)
            assert agent["errors"] == {"timeout": 1}
            assert agent["latency_ms"]["p50"] == 450
            assert agent["latency_ms"]["p95"] == 1200
            assert agent["latency_ms"]["p99"] == 1200
            assert agent["ttft_ms"]["p50"] == 90
            assert agent["tokens"] == {"prompt": 190, "completion": 330, "total": 520}
            assert agent["cost_estimate_cny"] == pytest.approx(0.00104, abs=0.0001)
            models = {m["model"]: m for m in agent["by_model"]}
            assert models["deepseek-v4-flash"]["calls"] == 2
            assert models["deepseek-v4-flash"]["errors"] == 1
            assert models["deepseek-v4-pro"]["calls"] == 1
            reasons = {r["name"]: r["count"] for r in agent["finish_reasons"]}
            assert reasons == {"stop": 1, "length": 1}
            assert agent["stream_share"] == pytest.approx(66.7, abs=0.1)
            assert data["engine"]["traces"] >= 0
            assert data["trend"] is not None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ops_recent_and_health(db_session, test_workspace, monkeypatch):
    db, cfg, _ = await _setup(db_session, monkeypatch)
    try:
        await db.execute(AgentMetric.__table__.delete())
        await db.commit()
        db.add_all([
            AgentMetric(kind="llm", model="deepseek-v4-flash", duration_ms=111, ok=True),
            AgentMetric(kind="study_chat", model="deepseek-v4-pro", duration_ms=222, ok=True),
        ])
        await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            resp = await client.get("/ops/metrics/recent?limit=5", headers={"X-Api-Token": cfg.api_token})
            assert resp.status_code == 200, resp.text
            items = resp.json()["items"]
            assert len(items) == 2
            assert items[0]["model"] == "deepseek-v4-pro"

            health = await client.get("/ops/health", headers={"X-Api-Token": cfg.api_token})
            assert health.status_code == 200, health.text
            assert health.json()["ok"] is True
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ops_summary_handles_empty_metrics(db_session, test_workspace, monkeypatch):
    """无指标数据时 summary 不崩, 返回空聚合。"""
    db, cfg, _ = await _setup(db_session, monkeypatch)
    try:
        await db.execute(AgentMetric.__table__.delete())
        await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            resp = await client.get("/ops/metrics/summary", headers={"X-Api-Token": cfg.api_token})
            assert resp.status_code == 200, resp.text
            assert resp.json()["agent"]["calls"] == 0
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_ops_summary_excludes_controlled_fixture_calls(db_session, test_workspace, monkeypatch):
    """Fixture adapters must not distort production success rate or the error list."""
    db, cfg, _ = await _setup(db_session, monkeypatch)
    try:
        await db.execute(AgentMetric.__table__.delete())
        await db.commit()
        db.add_all([
            AgentMetric(kind="llm", provider="gateway", model="deepseek-v4-pro",
                        duration_ms=900, ok=True, total_tokens=10),
            AgentMetric(kind="llm", provider="fixture", model="unchanged-model",
                        duration_ms=2100, ok=False, error_kind="network", total_tokens=0),
            AgentMetric(kind="llm", provider="fixture", model="unchanged-model",
                        duration_ms=2200, ok=True, total_tokens=0),
        ])
        await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            resp = await client.get("/ops/metrics/summary", headers={"X-Api-Token": cfg.api_token})
            assert resp.status_code == 200, resp.text
            agent = resp.json()["agent"]
            assert agent["calls"] == 1
            assert agent["ok_rate"] == 100.0
            assert agent["errors"] == {}
            assert "unchanged-model" not in {m["model"] for m in agent["by_model"]}
            recent = await client.get("/ops/metrics/recent", headers={"X-Api-Token": cfg.api_token})
            assert [i["model"] for i in recent.json()["items"]] == ["deepseek-v4-pro"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ops_summary_honours_time_window(db_session, test_workspace, monkeypatch):
    """Old rows must not leak into a 14-day success rate; days widens the window."""
    from datetime import datetime, timedelta, timezone
    db, cfg, _ = await _setup(db_session, monkeypatch)
    try:
        await db.execute(AgentMetric.__table__.delete())
        await db.commit()
        db.add_all([
            AgentMetric(kind="llm", provider="gateway", model="deepseek-v4-pro",
                        duration_ms=500, ok=True, total_tokens=10),
            AgentMetric(kind="llm", provider="gateway", model="deepseek-v4-pro",
                        duration_ms=999999, ok=False, error_kind="timeout", total_tokens=0,
                        created_at=datetime.now(timezone.utc) - timedelta(days=40)),
        ])
        await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            resp = await client.get("/ops/metrics/summary", headers={"X-Api-Token": cfg.api_token})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["window_days"] == 14
            assert data["sample_count"] == 1
            assert data["agent"]["calls"] == 1
            assert data["agent"]["ok_rate"] == 100.0
            assert data["agent_denominator"] == "每次模型调用"
            assert data["quality_denominator"] == "每条助手回复"

            wide = await client.get("/ops/metrics/summary?days=90", headers={"X-Api-Token": cfg.api_token})
            assert wide.status_code == 200, wide.text
            wide_data = wide.json()
            assert wide_data["window_days"] == 90
            assert wide_data["sample_count"] == 2
            assert wide_data["agent"]["ok_rate"] == 50.0

            bad = await client.get("/ops/metrics/summary?days=0", headers={"X-Api-Token": cfg.api_token})
            assert bad.status_code == 422
    finally:
        app.dependency_overrides.clear()
