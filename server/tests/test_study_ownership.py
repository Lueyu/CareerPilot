"""Exercise the authenticated ASGI application against two separate, token-bound tenants."""
import hashlib
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import ApiCategoryQuota, ApiRatePolicy, get_settings
from app.db import get_session
from app.main import app
from app.models import Exam, ExamQuestion, LearningTask, Question, Workspace
from app.models.study_chat import StudyMessage
from app.models.study_notes import StudyCard, StudyCardEvent, StudyNote, StudyNoteVersion


@pytest.mark.asyncio
async def test_two_bound_credentials_isolate_study_resources_over_http(db_session, test_workspace, monkeypatch):
    db, alice = db_session, test_workspace
    bob = Workspace(slug="test-" + uuid4().hex, name="second tenant")
    db.add(bob)
    await db.flush()
    secret = "private-" + uuid4().hex
    a_token, b_token, owner_token = [uuid4().hex for _ in range(3)]
    cfg = get_settings()
    # This test exercises many forbidden resource accesses, not quota exhaustion.
    monkeypatch.setattr(cfg, "api_rate_limits", ApiRatePolicy(
        llm=ApiCategoryQuota(burst=100, per_minute=100, daily=10000)))
    monkeypatch.setattr(cfg, "workspace_token_hashes", {
        alice.slug: hashlib.sha256(a_token.encode()).hexdigest(), bob.slug: hashlib.sha256(b_token.encode()).hexdigest()})
    monkeypatch.setattr(cfg, "api_token", owner_token)
    a, b = {"Authorization": "Bearer " + a_token}, {"X-Api-Token": b_token}
    # Separate request-scoped transactions on the isolated database, production auth unchanged.
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
    async def request_db():
        async with factory() as conn:
            yield conn
    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    question = Question(workspace_id=alice.id, short_code=uuid4().hex, title=secret, topic="backend")
    exam = Exam(workspace_id=alice.id, title=secret, kind="diagnosis", status="COMPLETED")
    task = LearningTask(workspace_id=alice.id, title=secret, kind="UNDERSTAND")
    db.add_all([question, exam, task])
    await db.flush()
    eq = ExamQuestion(exam_id=exam.id, question_id=question.id, order_no=1)
    db.add(eq)
    await db.commit()

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            async def checked(method, path, headers, status, body=None):
                response = await client.request(method, path, headers=headers, json=body)
                assert response.status_code == status, (method, path, response.status_code, response.text[:300])
                if status >= 400:
                    assert secret not in response.text
                return response.json()

            await checked("GET", "/study/sessions", {}, 401)
            await checked("GET", "/study/sessions", {"X-Api-Token": "invalid"}, 401)
            await checked("GET", "/study/sessions", {**b, "X-Workspace": alice.slug}, 404)
            await checked("GET", "/study/sessions", {"X-Api-Token": owner_token, "X-Workspace": alice.slug}, 404)
            assert [row["slug"] for row in (await checked("GET", "/settings/workspaces", b, 200))["workspaces"]] == [bob.slug]
            await checked("GET", "/settings/llm", b, 403)
            sid = (await checked("POST", "/study/sessions", a, 201,
                                 {"source_type": "question", "source_id": question.id}))["id"]
            other_sid = (await checked("POST", "/study/sessions", b, 201, {"topic": "other tenant's topic"}))["id"]
            assert [item["id"] for item in (await checked("GET", "/study/sessions", b, 200))["items"]] == [other_sid]
            for kind, identifier in (("question", question.id), ("exam_question", eq.id), ("task", task.id)):
                await checked("POST", "/study/sessions", b, 404, {"source_type": kind, "source_id": identifier})
            # Fixture model output is immutable source material; no external model is needed to test ownership.
            user, reply = [StudyMessage(session_id=sid, request_id="fixture-turn", role=role,
                                   action="explain", content=secret, status="completed") for role in ("user", "assistant")]
            db.add_all([user, reply])
            await db.commit()
            job = await checked("POST", f"/study/sessions/{sid}/note-jobs", a, 202, {"request_id": "prepare"})
            nid, jid = job["note_id"], job["id"]
            note = await db.get(StudyNote, nid)
            version = StudyNoteVersion(note_id=nid, title=secret, body=secret, author="USER", session_ids=[sid],
                coverage=[user.id, reply.id], sources=[{"kind": "message", "session_id": sid, "id": user.id}])
            db.add(version)
            await db.flush()
            note.current_version_id = version.id
            card = StudyCard(workspace_id=alice.id, note_id=nid, version_id=version.id, question=secret,
                             answer=secret, knowledge_point="原始知识点", status="ACTIVE")
            db.add(card)
            await db.commit()
            practice = await checked("POST", f"/study/sessions/{sid}/practices", a, 200,
                                     {"kind": "quiz", "request_key": "first", "card_id": card.id})
            pid = practice["id"]
            assert (await checked("GET", f"/study/sessions/{sid}", a, 200))["messages"][0]["content"] == secret
            assert (await checked("GET", f"/study/notes/{nid}/versions/{version.id}", a, 200))["sources"][0]["id"] == user.id
            assert (await checked("GET", f"/study/note-jobs/{jid}", a, 200))["id"] == jid
            assert (await checked("GET", f"/study/practices/{pid}", a, 200))["source"]["card_id"] == card.id
            for listing in ("/study/notes", "/study/cards", f"/study/sessions/{other_sid}/note-jobs",
                            f"/study/sessions/{other_sid}/practices"):
                assert (await checked("GET", listing, b, 200))["items"] == []
            cases = [
                ("GET", f"/sessions/{sid}", None),
                ("PATCH", f"/sessions/{sid}", {"title": "denied"}),
                ("POST", f"/sessions/{sid}/messages", {"content": "denied", "request_id": "x"}),
                ("POST", f"/sessions/{sid}/messages/{reply.id}/interrupt", None),
                ("POST", f"/sessions/{other_sid}/messages/{reply.id}/retry", None),
                ("GET", f"/sessions/{sid}/note-jobs", None),
                ("POST", f"/sessions/{sid}/note-jobs", {"request_id": "x"}),
                ("POST", f"/sessions/{other_sid}/note-jobs", {"request_id": "x", "note_id": nid}),
                ("GET", f"/notes/{nid}", None),
                ("GET", f"/notes/{nid}/versions/{version.id}", None),
                ("PATCH", f"/notes/{nid}", {"expected_version_id": version.id, "title": "x", "body": "x"}),
                ("POST", f"/notes/{nid}/cards", {"version_id": version.id}),
                ("GET", f"/note-jobs/{jid}", None), ("POST", f"/note-jobs/{jid}/retry", None),
                ("POST", f"/cards/{card.id}/reveal", None), ("DELETE", f"/cards/{card.id}", None),
                ("POST", f"/cards/{card.id}/review", {"rating": "good", "request_id": "x"}),
                ("GET", f"/sessions/{sid}/practices", None),
                ("POST", f"/sessions/{other_sid}/practices", {"kind": "quiz", "request_key": "x", "card_id": card.id}),
                ("GET", f"/practices/{pid}", None),
                ("POST", f"/practices/{pid}/answers", {"text_answer": "x", "request_key": "x"}),
                ("POST", f"/practices/{pid}/assistance", {"kind": "reference", "request_key": "x"}),
                ("POST", f"/practices/{pid}/skip", None), ("POST", f"/practices/{pid}/retest", None),
                ("GET", f"/practices/{pid}/retest", None),
            ]
            for method, path, body in cases:
                await checked(method, "/study" + path, b, 404, body)
            assert (await checked("GET", f"/study/notes/{nid}", a, 200))["body"] == secret
            assert (await checked("GET", f"/study/practices/{pid}", a, 200))["status"] == practice["status"]
            assert await db.scalar(select(func.count()).select_from(StudyCardEvent).where(StudyCardEvent.card_id == card.id)) == 0
    finally:
        await db.execute(delete(Workspace).where(Workspace.id == bob.id))
        await db.commit()
