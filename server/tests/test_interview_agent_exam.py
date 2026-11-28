from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import exams
from app.config import get_settings
from app.main import app
from app.models import Evidence, Exam, ImportSession, Question, Skill, StudySession, Workspace
from app.services import assessment_jobs as jobs
from app.services import assessment_worker as worker
from app.services.assessment_rubric import AssessmentJudgeError


@pytest.fixture
def factory(db_session, monkeypatch):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def session_dependency():
        async with factory() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, exams.get_session, session_dependency)
    return factory


def client(workspace):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test/api/v1/",
                       headers={"X-Api-Token": get_settings().api_token, "X-Workspace": workspace.slug,
                                "Accept-Language": "en"})


def request_body(origin, *, source="bank", mode="practice", types=("mcq",), topic="synthetic-tail",
                 count=1, difficulty=4, set_id=None):
    return {
        "kind": "bank_exam", "idempotency_key": uuid4().hex, "question_count": count, "duration_minutes": 7,
        "interview_agent": {
            "mode": mode, "source": source, "topic": topic, "origin_session_id": origin,
            "question_types": types, "difficulty": difficulty, "interview_set_id": set_id,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["practice", "test"])
async def test_scored_report_keeps_origin_reference_and_evidence_boundary(db_session, test_workspace, factory, mode):
    study = StudySession(workspace_id=test_workspace.id, title="Synthetic conversation")
    skill = Skill(key=uuid4().hex[:20], name="Synthetic skill")
    db_session.add_all([study, skill])
    await db_session.flush()
    question = Question(workspace_id=test_workspace.id, title="Synthetic-tail choice", topic="synthetic",
                        short_code=uuid4().hex, skill_id=skill.id, qtype="mcq", difficulty=4,
                        options=["wrong", "original correct"], rubric={"answer_index": 1, "max_points": 5},
                        answer_html="<p>Original reference</p>")
    db_session.add(question)
    await db_session.commit()
    async with client(test_workspace) as http:
        body = request_body(study.id, mode=mode)
        created = (await http.post("exams", json=body)).raise_for_status().json()
        exam_id = created["id"]
        assert created["origin_session_id"] == study.id
        assert created["interview_agent"] is True
        assert created["kind"] == ("practice" if mode == "practice" else "bank_exam")
        assert datetime.fromisoformat(created["deadline_at"]) - datetime.fromisoformat(created["started_at"]) == timedelta(minutes=7)
        assert "reference_answer" not in created["questions"][0]
        assert (await http.get(f"exams/{exam_id}/report")).status_code == 409
        replay = (await http.post("exams", json=body)).raise_for_status().json()
        assert replay["id"] == exam_id and replay["deadline_at"] == created["deadline_at"]
        assert (await http.post("exams", json={**body, "duration_minutes": 8})).status_code == 409

        question.answer_html = "<p>Later reference</p>"
        question.options = ["later", "changed"]
        question.rubric = {"answer_index": 0, "max_points": 5}
        await db_session.commit()
        saved = (await http.post(f"exams/{exam_id}/answers",
                                json={"order_no": 1, "choice_index": 1, "expected_revision": 0})).raise_for_status().json()
        reloaded = (await http.get(f"exams/{exam_id}")).raise_for_status().json()
        assert reloaded["origin_session_id"] == study.id
        assert reloaded["questions"][0]["answer"]["revision"] == saved["revision"] == 1
        queued = (await http.post(f"exams/{exam_id}/submit", json={"answers": []})).raise_for_status().json()
        pending = (await http.get(f"exams/{exam_id}/report")).raise_for_status().json()
        assert pending["origin_session_id"] == study.id and pending["score"] is None
        assert pending["questions"][0]["correct_choice_index"] is None
        assert pending["scoring_task"]["id"] == queued["task_id"]
        claim = await jobs.claim_step(db_session, "synthetic-exam")
        assert claim.run_id == queued["task_id"]
        await jobs.complete_step(db_session, claim, await worker.evaluate(claim, {}))
        await worker.reconcile(factory)
        report = (await http.get(f"exams/{exam_id}/report")).raise_for_status().json()
        assert report["origin_session_id"] == study.id and report["score"] == 100
        scored, = report["questions"]
        assert scored["answer"]["choice_index"] == scored["correct_choice_index"] == 1
        assert scored["options"][1] == "original correct"
        assert scored["reference_answer"] == "<p>Original reference</p>"
        assert scored["provenance"]["answer_revision"] == 1
        assert scored["provenance"]["question_version"] == claim.payload["question_version"]
        assert bool(scored["answer"]["evidence_refs"]) == (mode == "test")
        evidence_count = await db_session.scalar(select(func.count()).select_from(Evidence)
                                                 .where(Evidence.workspace_id == test_workspace.id))
        assert evidence_count == (1 if mode == "test" else 0)


@pytest.mark.asyncio
async def test_configuration_and_exact_expiry(db_session, test_workspace, factory):
    other = Workspace(slug="test-foreign-" + uuid4().hex, name="Synthetic foreign workspace")
    db_session.add(other)
    await db_session.flush()
    foreign_study = StudySession(workspace_id=other.id, title="Foreign fixture")
    tagged = Question(workspace_id=test_workspace.id, title="Synthetic-tail code", topic="fixture",
                      short_code=uuid4().hex, qtype="open", difficulty=4, tags=["编程题"],
                      rubric={"max_points": 5, "points": [{"title": "Explain behavior"}]},
                      generation_meta={"assessment_use": "PRACTICE_ONLY", "interview_set_id": "sample-set", "source_item_id": 29})
    ordinary = Question(workspace_id=test_workspace.id, title="Synthetic-tail prose", topic="fixture",
                        short_code=uuid4().hex, qtype="open", difficulty=3,
                        generation_meta={"assessment_use": "PRACTICE_ONLY", "origin": "library_import"})
    db_session.add_all([foreign_study, tagged, ordinary])
    await db_session.flush()
    imported = ImportSession(workspace_id=test_workspace.id, short_code=uuid4().hex, filename="synthetic.txt", meta={"interview_sets": [
        {"id": "sample-set", "title": "Saved set", "project_name": "Synthetic project", "questions": [{"id": tagged.id}]},
    ]})
    db_session.add(imported)
    await db_session.commit()

    async with client(test_workspace) as http:
        body = request_body(None, source="interview_set", types=("code",), set_id="sample-set")
        assert (await http.post("exams", json=request_body(foreign_study.id))).status_code == 404
        assert (await http.post("exams", json=request_body(None, types=("multi",)))).status_code == 422
        assert (await http.post("exams", json={**body, "question_count": 2})).status_code == 409
        assert (await http.post("exams", json=request_body(None, source="interview_set", types=("open",), set_id="sample-set"))).status_code == 409
        code = (await http.post("exams", json=body)).raise_for_status().json()
        assert code["origin_session_id"] is None
        assert code["questions"][0]["qtype"] == "open" and code["questions"][0]["answer_format"] == "code"
        exam_id = code["id"]
        await http.post(f"exams/{exam_id}/answers", json={"order_no": 1, "text_answer": "  code()\n", "expected_revision": 0})
        queued = (await http.post(f"exams/{exam_id}/submit", json={"answers": []})).raise_for_status().json()
        claim = await jobs.claim_step(db_session, "synthetic-code")
        assert claim.run_id == queued["task_id"]
        assert claim.payload["submitted_answer"]["text_answer"] == "  code()\n"
        assert claim.payload["interview_reference"] == {"set_id": "sample-set", "project_name": "Synthetic project", "source_item_id": 29}
        with pytest.raises(AssessmentJudgeError) as unconfigured:
            await worker.evaluate(claim, {})
        assert unconfigured.value.category == "NOT_CONFIGURED"
        await jobs.fail_step(db_session, claim, unconfigured.value)
        await worker.reconcile(factory)
        failed = (await http.get(f"exams/{exam_id}/report")).raise_for_status().json()
        assert failed["status"] == "SCORING_FAILED" and failed["score"] is None
        assert failed["scoring_task"]["error"]["code"] == "NOT_CONFIGURED"

        prose = (await http.post("exams", json=request_body(None, source="library", types=("open",), difficulty=3))).raise_for_status().json()
        exam_id = prose["id"]
        await db_session.execute(update(Exam).where(Exam.id == exam_id).values(
            started_at=datetime.now(timezone.utc) - timedelta(minutes=7, milliseconds=5)))
        await db_session.commit()
        save = await http.post(f"exams/{exam_id}/answers", json={"order_no": 1, "text_answer": "too late"})
        assert save.status_code == 409
        assert (await http.get(f"exams/{exam_id}")).raise_for_status().json()["status"] == "EXPIRED"
        assert (await http.post(f"exams/{exam_id}/submit", json={"answers": []})).status_code == 409


pytestmark = pytest.mark.usefixtures("enrolled_default_owner")
