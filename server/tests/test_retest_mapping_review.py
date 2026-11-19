from uuid import uuid4

import httpx
import pytest

from app.api.deps import get_workspace
from app.db import get_session
from app.main import app
from app.models import Question, Workspace
from app.services.assessment_versions import question_snapshot
from app.services.learning_exposure import exposed_families


@pytest.mark.asyncio
async def test_only_confirmed_current_criteria_are_reviewed_in_the_active_workspace(db_session, test_workspace):
    other = Workspace(slug=f"test-{uuid4().hex}", name="Other workspace", is_demo=False)
    shared = Question(
        short_code=f"T-{uuid4().hex[:20]}", title="How does retrieval ranking work?",
        topic="rag", qtype="open",
        rubric={"points": [
            {"title": "Explain ranking", "detail": "Describe reranking retrieved passages"},
            "Explain recall",
        ]},
    )
    owned = Question(
        short_code=f"T-{uuid4().hex[:20]}", title="A private question", topic="rag",
        workspace_id=test_workspace.id, qtype="open", rubric={"points": ["A private criterion"]},
    )
    db_session.add_all([other, shared, owned])
    await db_session.commit()
    current_workspace = test_workspace
    app.dependency_overrides[get_workspace] = lambda: current_workspace
    app.dependency_overrides[get_session] = lambda: db_session

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            question_url = f"/api/v1/retest-mappings/{shared.id}"
            criterion_url = f"{question_url}/c1"
            review = await client.get(question_url)
            assert review.status_code == 200
            version = question_snapshot(shared)["rubric"]["rubric_version"]
            assert review.json()["rubric_version"] == version
            assert review.json()["criteria"][0] == {
                "criterion_id": "c1", "title": "Explain ranking",
                "behavior_key": None, "behavior_version": None, "reviewed_at": None,
            }
            assert review.json()["criteria"][0]["behavior_key"] is None
            approval = {"rubric_version": version, "behavior_key": "retrieval.rerank", "behavior_version": 2}
            assert (await client.put(criterion_url, json=approval)).status_code == 422
            assert (await client.put(criterion_url, json={**approval, "confirm": "true"})).status_code == 422
            assert (await client.put(criterion_url, json={**approval, "confirm": False})).status_code == 422
            approval["confirm"] = True
            assert (await client.put(criterion_url, json={**approval, "behavior_version": 0})).status_code == 422
            assert (await client.put(criterion_url, json={**approval, "rubric_version": "stale"})).status_code == 409
            assert (await client.put(f"{question_url}/c20", json=approval)).status_code == 422
            saved = await client.put(criterion_url, json=approval)
            assert saved.status_code == 200
            assert saved.json()["behavior_key"] == "retrieval.rerank"
            assert saved.json()["behavior_version"] == 2
            assert saved.json()["reviewed_at"]
            assert (await client.put(criterion_url, json=approval)).json() == saved.json()
            assert (await client.put(f"{question_url}/c2", json=approval)).status_code == 409
            assert await exposed_families(db_session, test_workspace.id) == set()

            current_workspace = other
            assert (await client.get(f"/api/v1/retest-mappings/{owned.id}")).status_code == 404
            owned_approval = {**approval, "rubric_version": question_snapshot(owned)["rubric"]["rubric_version"]}
            assert (await client.put(f"/api/v1/retest-mappings/{owned.id}/c1", json=owned_approval)).status_code == 404
            assert (await client.get(question_url)).json()["criteria"][0]["behavior_key"] is None
            other_approval = {**approval, "behavior_key": "ranking.quality"}
            other_saved = await client.put(criterion_url, json=other_approval)
            assert other_saved.status_code == 200
            current_workspace = test_workspace
            assert (await client.get(question_url)).json()["criteria"][0]["behavior_key"] == "retrieval.rerank"
            assert (await client.delete(criterion_url, params={"rubric_version": version})).status_code == 204
            assert (await client.get(question_url)).json()["criteria"][0]["behavior_key"] is None
            current_workspace = other
            assert (await client.get(question_url)).json()["criteria"][0]["behavior_key"] == "ranking.quality"

            shared.rubric = {"points": ["A changed criterion", "A new criterion"]}
            await db_session.commit()
            changed = (await client.get(question_url)).json()
            assert changed["rubric_version"] != version
            assert all(row["behavior_key"] is None for row in changed["criteria"])
            assert changed["historical_mappings"] == [{
                "criterion_id": "c1", "rubric_version": version, "behavior_key": "ranking.quality",
                "behavior_version": 2, "reviewed_at": other_saved.json()["reviewed_at"],
            }]
            assert (await client.put(criterion_url, json=approval)).status_code == 409
            shared.rubric = {**shared.rubric, "review_required": True}
            await db_session.commit()
            unreviewable = (await client.get(question_url)).json()
            assert unreviewable["reviewable"] is False
            assert unreviewable["historical_mappings"] == changed["historical_mappings"]
            assert (await client.delete(criterion_url, params={"rubric_version": version})).status_code == 204
            assert (await client.delete(criterion_url, params={"rubric_version": version})).status_code == 204
            assert (await client.get(question_url)).json()["historical_mappings"] == []
            assert (await client.put(criterion_url, json=approval)).status_code == 409
            shared.qtype = "mcq"
            await db_session.commit()
            assert (await client.get(question_url)).json()["reviewable"] is False
            assert (await client.put(criterion_url, json=approval)).status_code == 409
    finally:
        app.dependency_overrides.pop(get_workspace)
        app.dependency_overrides.pop(get_session)
        await db_session.rollback()
        await db_session.delete(other)
        await db_session.delete(shared)
        await db_session.commit()
