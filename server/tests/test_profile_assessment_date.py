"""Reads of capability details preserve the date of the underlying assessment."""
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_session
from app.main import app
from app.models import Evidence, Skill, SkillState
from app.services.competency import recompute_skill_states


@pytest.mark.asyncio
async def test_detail_reports_same_assessment_date_as_profile_without_advancing_it(
    db_session, test_workspace, monkeypatch,
):
    suffix = uuid4().hex[:12]
    assessed = Skill(key=f"dated-{suffix}", name="Assessed skill")
    unassessed = Skill(key=f"undated-{suffix}", name="Unassessed skill")
    observed_at = datetime(2025, 9, 18, 12, 30, tzinfo=timezone.utc)
    db_session.add_all([assessed, unassessed])
    await db_session.flush()
    db_session.add(Evidence(
        workspace_id=test_workspace.id, skill_id=assessed.id, short_code=f"EV-{suffix}",
        title="Independent assessment", kind="EXAM_ANSWER", occurred_at=observed_at,
        meta={"assessed": True, "question_id": 1, "canonical_question_id": 1, "exam_id": 1,
              "exam_question_id": 1, "judge_version": "rule-v1", "qtype": "mcq",
              "score": 1, "max_points": 1},
    ))
    await db_session.flush()
    await recompute_skill_states(db_session, test_workspace.id)
    await db_session.commit()

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def fixture_session():
        async with sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, fixture_session)
    headers = {"X-Api-Token": "cp-dev-token", "X-Workspace": test_workspace.slug}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        overview_response = await client.get("/api/v1/profile")
        overview_response.raise_for_status()
        dates = {domain["key"]: domain["last_assessed_at"] for domain in overview_response.json()["domains"]}
        for skill, expected in [(assessed, observed_at.isoformat()), (unassessed, None)]:
            assert dates[skill.key] == expected
            for _ in range(2):
                response = await client.get(f"/api/v1/profile/{skill.key}")
                response.raise_for_status()
                assert response.json()["state"]["last_assessed_at"] == expected

    stored = await db_session.execute(
        select(SkillState.skill_id, SkillState.last_assessed_at).where(
            SkillState.workspace_id == test_workspace.id,
            SkillState.skill_id.in_([assessed.id, unassessed.id]),
        )
    )
    assert dict(stored.all()) == {assessed.id: observed_at, unassessed.id: None}
