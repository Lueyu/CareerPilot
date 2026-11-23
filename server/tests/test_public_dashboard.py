from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.public_dashboard import dashboard, router
from app.api.public_library import ORIGIN
from app.db import get_session
from app.models import ImportSession, Question, Workspace
from app.models.public_coach import CoachThread, CoachTurn
from app.models.public_library import PublicLibraryRole


def thread(workspace, mode, title, updated_at):
    return CoachThread(workspace_id=workspace.id, mode=mode, role="Backend",
                       title=title, updated_at=updated_at)


@pytest.mark.asyncio
async def test_dashboard_rejects_anonymous_workspace_selection(monkeypatch):
    origin = "https://dashboard.test"
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("SEED_SESSION_SECRET", token_urlsafe(48))
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_session] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as client:
        response = await client.get("/api/v1/dashboard", headers={"X-Expected-Workspace": "42"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_required"


@pytest.mark.asyncio
async def test_dashboard_counts_and_resume_are_workspace_scoped(db_session, test_workspace):
    owner = test_workspace
    empty = await dashboard(db_session, owner)
    assert empty.selected_role is None
    assert empty.question_count == empty.practice_count == 0
    assert empty.resume_study is None and empty.resume_interview is None

    peer = Workspace(slug="peer-" + uuid4().hex, name="Peer", target_role="Other")
    db_session.add(peer)
    await db_session.flush()
    owner.target_role = "Backend"
    now = datetime.now(UTC)
    unfinished = thread(owner, "interview", "Unfinished", now - timedelta(days=2))
    finished = thread(owner, "interview", "Finished", now)
    study = thread(owner, "study", "Study", now - timedelta(days=1))
    db_session.add_all([
        unfinished, finished, study, thread(peer, "interview", "Private", now + timedelta(days=1)),
        PublicLibraryRole(workspace_id=owner.id, name="Backend", directions=[]),
        PublicLibraryRole(workspace_id=owner.id, name="Frontend", directions=[]),
        PublicLibraryRole(workspace_id=peer.id, name="Other", directions=[]),
    ])
    for workspace, demo in [(owner, False), (owner, True), (peer, False), (None, False)]:
        db_session.add(Question(workspace_id=workspace.id if workspace else None, is_demo=demo,
                                short_code=uuid4().hex, title="Question", topic="testing"))
    for workspace, status, origin in [
        (owner, "REVIEW", ORIGIN), (owner, "APPROVED", ORIGIN),
        (owner, "REVIEW", "personal"), (peer, "REVIEW", ORIGIN),
    ]:
        db_session.add(ImportSession(workspace_id=workspace.id, short_code=uuid4().hex,
                                     filename=workspace.name, status=status, meta={"origin": origin}))
    await db_session.flush()
    db_session.add_all([
        CoachTurn(thread_id=finished.id, request_id=str(uuid4()), action="finish", status="completed"),
        CoachTurn(thread_id=unfinished.id, request_id=str(uuid4()), action="finish", status="failed"),
    ])
    await db_session.flush()

    result = await dashboard(db_session, owner)
    assert result.selected_role == "Backend"
    assert (result.role_count, result.question_count, result.practice_count) == (2, 1, 3)
    assert (result.import_count, result.pending_import_count) == (2, 1)
    assert [item.filename for item in result.pending_imports] == [owner.name]
    assert result.latest_activity.id == finished.id
    assert result.resume_interview.id == unfinished.id
    assert result.resume_study.id == study.id
    other_result = await dashboard(db_session, peer)
    assert other_result.latest_activity.title == "Private"
    assert other_result.resume_study is None
