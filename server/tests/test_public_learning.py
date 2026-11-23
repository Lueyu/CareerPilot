from datetime import date, timedelta
from secrets import token_urlsafe
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import public_auth, public_learning, public_library
from app.db import get_session
from app.models import MemoryItem, Question
from app.models.question import SOURCE_GENERATED, SOURCE_PRIVATE, SOURCE_VERIFIED

ORIGIN = "https://learning.test"
PLAN = "/api/v1/learning/daily-plan"


def question(workspace_id, role="Backend", *, source=SOURCE_PRIVATE, demo=False):
    return Question(
        short_code=uuid4().hex, title=f"{role} practice {uuid4().hex}", topic="Databases",
        role=role, workspace_id=workspace_id, source_type=source, is_demo=demo,
    )


@pytest.mark.asyncio
async def test_registered_plan_scopes_questions_and_preserves_evidence(db_session, monkeypatch):
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("SEED_SESSION_SECRET", token_urlsafe(48))
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def database():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.middleware("http")(public_auth.public_origin_guard)
    for routes in (public_auth.router, public_learning.router, public_library.router):
        app.include_router(routes, prefix="/api/v1")
    app.dependency_overrides[get_session] = database
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers={"origin": ORIGIN}) as client:
        assert (await client.get(PLAN)).status_code == 401
        owner = (await client.post("/api/v1/auth/register", json={
            "username": "learning_owner", "password": "synthetic learning password",
        })).json()["workspace_id"]
        owner_header = {"X-Expected-Workspace": str(owner)}
        empty = (await client.get(PLAN, headers=owner_header)).json()
        assert empty["target_role"] == ""
        assert empty["progress"]["total"] == 0
        assert not empty["deadline_confirmed"]

        async def choose(role):
            response = await client.put("/api/v1/library/role", headers=owner_header, json={"role": role})
            assert response.status_code == 200

        await choose("Backend")
        first, second, third = question(owner), question(owner, source=SOURCE_GENERATED), question(owner)
        other_role = question(owner, "Design")
        db_session.add_all([
            first, second, third, other_role, question(None), question(owner, demo=True),
            question(owner, source=SOURCE_VERIFIED),
        ])
        await db_session.commit()

        plan = (await client.get(PLAN, headers=owner_header)).json()
        today = date.fromisoformat(plan["today"])
        assert date.fromisoformat(plan["deadline"]) == today + timedelta(days=14)
        assert plan["target_role"] == "Backend"
        assert plan["schedule_profile"] is None
        assert plan["progress"]["total"] == 3
        assert [item["question_id"] for day in plan["days"] for item in day["items"]] == [
            first.id, second.id, third.id,
        ]
        body = {"target_role": "Backend", "note": "Recorded an answer and a follow-up improvement."}

        async def record(question_id):
            return await client.post(PLAN + "/evidence", headers=owner_header,
                                     json={**body, "item_key": f"question:{question_id}"})

        assert (await record(third.id)).status_code == 422
        assert (await record(other_role.id)).status_code == 404
        recorded = (await record(first.id)).json()
        assert recorded["progress"]["completed"] == 1
        assert (await record(first.id)).status_code == 200
        update = {
            "target_role": "Backend", "deadline": (today + timedelta(days=7)).isoformat(), "daily_minutes": 35,
        }
        assert (await client.put(PLAN, headers=owner_header, json={
            **update, "schedule_profile": "agent_interview_sprint",
        })).status_code == 422
        changed = (await client.put(PLAN, headers=owner_header, json=update)).json()
        assert changed["deadline_confirmed"]
        assert changed["daily_minutes"] == 35
        assert changed["deadline"] == update["deadline"]
        assert changed["progress"]["completed"] == 1
        replanned = (await client.post(PLAN + "/reschedule", headers=owner_header,
                                       json={"target_role": "Backend"})).json()
        completed = next(item for day in replanned["days"] for item in day["items"] if item["status"] == "completed")
        assert completed["question_id"] == first.id
        assert completed["evidence"]["note"] == body["note"]
        assert completed["verification"] is None
        memory = (await db_session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == owner))).one()
        assert memory.meta["evidence_kind"] == "self_report"

        await choose("Design")
        assert (await record(first.id)).status_code == 409
        design = (await client.get(PLAN, headers=owner_header)).json()
        assert design["progress"]["completed"] == 0
        assert [item["question_id"] for day in design["days"] for item in day["items"]] == [other_role.id]
        await choose("Backend")
        restored = (await client.get(PLAN, headers=owner_header)).json()
        assert restored["progress"]["completed"] == 1
        assert restored["deadline"] == update["deadline"]

        peer = (await client.post("/api/v1/auth/register", json={
            "username": "learning_peer", "password": "synthetic learning password",
        })).json()["workspace_id"]
        peer_header = {"X-Expected-Workspace": str(peer)}
        assert (await client.get(PLAN, headers=owner_header)).status_code == 409
        assert (await record(first.id)).status_code == 409
        await client.put("/api/v1/library/role", headers=peer_header, json={"role": "Backend"})
        assert (await client.get(PLAN, headers=peer_header)).json()["progress"]["total"] == 0
        assert (await client.post(PLAN + "/evidence", headers=peer_header,
                                  json={**body, "item_key": f"question:{first.id}"})).status_code == 404
