"""Exercise both exam list contracts against isolated PostgreSQL."""
import datetime as dt
from collections import Counter

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import exams
from app.main import app
from app.models import Exam, Workspace


@pytest.mark.asyncio
async def test_exam_history_pages_and_legacy_resume(db_session, test_workspace, monkeypatch):
    workspace_id = test_workspace.id
    kinds = ("diagnosis", "focused", "bank_exam", "project")
    timestamp = dt.datetime.now(dt.timezone.utc)
    history = [
        Exam(
            workspace_id=workspace_id,
            title=f"History {index + 1}",
            kind=kinds[index % len(kinds)],
            status="COMPLETED",
            created_at=timestamp,
            completed_at=timestamp,
            score=80,
        )
        for index in range(25)
    ]
    history[-1].status = "ACTIVE"
    history[-1].started_at = timestamp
    history[-1].completed_at = None
    history[-1].score = None
    other_workspace = Workspace(slug=f"{test_workspace.slug}-other", name="Other")
    db_session.add(other_workspace)
    await db_session.flush()
    other_exam = Exam(workspace_id=other_workspace.id, title="Other workspace", kind="diagnosis")
    db_session.add_all([
        *history,
        Exam(workspace_id=workspace_id, title="Practice", kind="practice"),
        other_exam,
    ])
    await db_session.commit()

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def test_session():
        async with factory() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, exams.get_session, test_session)
    from app.config import get_settings
    headers = {"X-Api-Token": get_settings().api_token, "X-Workspace": test_workspace.slug}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        legacy = (await client.get("/api/v1/exams")).raise_for_status().json()
        assert set(legacy) == {"history", "kinds"}
        newest = list(reversed(history))
        assert [row["id"] for row in legacy["history"]] == [exam.id for exam in newest[:20]]
        assert {kind["kind"]: kind["history_count"] for kind in legacy["kinds"]} == Counter(
            exam.kind for exam in newest[:20]
        )
        active = next(row for row in legacy["history"] if row["kind"] == "diagnosis" and row["status"] == "ACTIVE")
        detail = (await client.get(f"/api/v1/exams/{active['id']}")).raise_for_status().json()
        assert detail["id"] == history[-1].id and detail["status"] == "ACTIVE"

        path = "/api/v1/exams/history"
        first = (await client.get(path)).raise_for_status().json()
        assert first["history"] == legacy["history"]
        assert first["next_cursor"] == newest[19].id
        params = {"before_id": first["next_cursor"]}
        second = (await client.get(path, params=params)).raise_for_status().json()
        for page in (first, second):
            assert page["total_count"] == 25
            assert {kind["kind"]: kind["history_count"] for kind in page["kinds"]} == Counter(
                exam.kind for exam in newest
            )
        assert second["next_cursor"] is None
        assert len(second["history"]) == 5
        assert [row["id"] for row in first["history"] + second["history"]] == [exam.id for exam in newest]
        oldest = second["history"][-1]
        report = (await client.get(f"/api/v1/exams/{oldest['id']}/report")).raise_for_status().json()
        assert report["id"] == history[0].id and report["score"] == 80

        db_session.add(Exam(workspace_id=workspace_id, title="New exam", kind="diagnosis"))
        await db_session.commit()
        repeated = (await client.get(path, params=params)).raise_for_status().json()
        assert repeated["history"] == second["history"]
        assert repeated["total_count"] == 26

        empty = (await client.get(path, params={"before_id": 1})).raise_for_status().json()
        assert empty["history"] == [] and empty["next_cursor"] is None and empty["total_count"] == 26
        assert (await client.get(path, params={"before_id": 0})).status_code == 422
        another = (await client.get(path, headers={"X-Workspace": other_workspace.slug})).raise_for_status().json()
        assert another["total_count"] == 1
        assert another["next_cursor"] is None
        assert [row["id"] for row in another["history"]] == [other_exam.id]
