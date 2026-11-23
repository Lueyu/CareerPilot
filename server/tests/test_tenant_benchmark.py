import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.api.deps import get_workspace
from app.api.v1 import benchmark, profile
from app.db import get_session
from app.models import JdSource, JobStandard, Skill


@pytest.mark.asyncio
async def test_tenant_benchmark_profile_and_first_jd_follow_active_role(db_session, test_workspace, monkeypatch):
    ws = test_workspace
    ws.slug = "seed-" + ws.slug
    ws.target_role = "市场运营"
    ws.target_level = ""
    # Simulate a previously provisioned tenant with old AI skills.
    db_session.add(Skill(key=f"legacy-{ws.id}", name="旧 RAG 能力"))
    await db_session.commit()
    monkeypatch.setenv("TENANT_DISPLAY_ROLE", "obsolete deployment role")

    app = FastAPI()
    app.include_router(benchmark.router)
    app.include_router(profile.router)
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_workspace] = lambda: ws

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/benchmark")
        assert result.status_code == 200
        seed = result.json()
        assert seed["standard"]["role"] == "市场运营"
        assert seed["standard"]["is_seed"] is True
        assert seed["standard"]["jd_sample_count"] == 0
        assert sum(row["weight_pct"] for row in seed["rows"]) == 100
        assert [row["skill"]["name"] for row in seed["rows"]] == ["用户增长", "活动复盘", "用户分层"]
        assert all(row["status"] == "UNKNOWN" for row in seed["rows"])
        assert await db_session.scalar(select(func.count(JdSource.id)).where(JdSource.workspace_id == ws.id)) == 0
        await db_session.rollback()
        await db_session.refresh(ws)

        # As with a new role flowing from beta, no current standard exists yet.
        ws.target_role = "教师"
        await db_session.commit()
        jd = await client.post("/benchmark/jd", json={
            "text": "负责教学设计并根据学习反馈优化课堂。负责教学设计、教学设计，具备良好的沟通协作能力。",
        })
        assert jd.status_code == 200, jd.text
        assert jd.json()["status"] == "APPLIED"
        assert jd.json()["applied"]["changes"][0]["skill_name"] == "教学设计"
        updated = (await client.get("/benchmark")).json()
        assert updated["standard"]["role"] == "教师"
        assert updated["standard"]["jd_sample_count"] == 1
        assert "权重仍为种子假设" in updated["standard"]["note"]
        domains = (await client.get("/profile")).json()["domains"]
        assert {item["name"] for item in domains} == {"教学设计", "学习反馈", "沟通协作"}
        assert all(item["level"] is None for item in domains)
        await db_session.rollback()
        await db_session.refresh(ws)

        ws.target_role = "市场运营"
        await db_session.commit()
        assert (await client.get("/benchmark")).json()["standard"] == seed["standard"]
        assert await db_session.scalar(
            select(JobStandard.id).where(JobStandard.role == "教师", JobStandard.is_seed.is_(False))
        ) == jd.json()["applied"]["standard_id"]
        await db_session.rollback()
