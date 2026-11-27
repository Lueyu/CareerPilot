"""Relational integration tests use explicit fixtures, never a real model."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from app.api.v1.learning import complete_task, learning_center
from app.api.v1.memory import confirm_memory, delete_memory, list_memory
from app.models import (
    Event,
    Evidence,
    Exam,
    ExamAnswer,
    ExamQuestion,
    LearningTask,
    MemoryItem,
    Question,
    Skill,
    SkillState,
    Workspace,
)


async def task_fixture(session, ws, kind="UNDERSTAND"):
    skill = Skill(key="test-rag-" + uuid4().hex[:12], name="Fixture RAG")
    session.add(skill)
    await session.flush()
    task = LearningTask(workspace_id=ws.id, skill_id=skill.id, kind=kind,
                        title="Fixture: retrieval measurement", status="PENDING",
                        retest_round=1 if kind == "RETEST" else 0,
                        due_date=date.today() + timedelta(days=7) if kind == "RETEST" else None,
                        content={"steps": ["Measure retrieval recall"]})
    session.add(task)
    await session.commit()
    return task, skill


@pytest.mark.asyncio
async def test_retest_cannot_be_completed_without_an_answer(db_session, test_workspace):
    task, _ = await task_fixture(db_session, test_workspace, "RETEST")
    with pytest.raises(HTTPException) as error:
        await complete_task(task.id, ws=test_workspace, session=db_session)
    assert error.value.status_code == 409
    assert task.status == "PENDING"


@pytest.mark.asyncio
async def test_learning_complete_schedules_once_without_mastery(db_session, test_workspace):
    task, _ = await task_fixture(db_session, test_workspace)
    await complete_task(task.id, ws=test_workspace, session=db_session)
    first_completed = task.completed_at
    await complete_task(task.id, ws=test_workspace, session=db_session)
    retests = (await db_session.execute(select(LearningTask).where(LearningTask.retest_of == task.id))).scalars().all()
    assert len(retests) == 3
    assert {t.due_date for t in retests} == {date.today() + timedelta(days=n) for n in (1, 3, 7)}
    assert all(t.status == "PENDING" for t in retests)
    assert task.completed_at == first_completed
    assert (await db_session.scalar(select(func.count(Evidence.id)).where(Evidence.workspace_id == test_workspace.id))) == 0
    assert (await db_session.scalar(select(func.count(Event.id)).where(Event.workspace_id == test_workspace.id, Event.kind == "learning.task_done"))) == 1


@pytest.mark.asyncio
async def test_unassessed_workspace_does_not_invent_gap_tasks(db_session, test_workspace):
    skill = Skill(key="test-unknown-" + uuid4().hex[:12], name="Unassessed")
    db_session.add(skill)
    await db_session.flush()
    db_session.add(SkillState(workspace_id=test_workspace.id, skill_id=skill.id,
                              estimated_level=None, status="UNKNOWN", evidence_coverage=0, confidence=0))
    await db_session.commit()
    result = await learning_center(ws=test_workspace, session=db_session)
    assert result["today"] == []
    assert result["retest_plan"] == []


@pytest.mark.asyncio
async def test_task_completion_memory_reviews_are_private_and_repeatable(db_session, test_workspace):
    task, _ = await task_fixture(db_session, test_workspace)
    assert (await list_memory(ws=test_workspace, session=db_session))["items"] == []

    await complete_task(task.id, ws=test_workspace, session=db_session)
    await complete_task(task.id, ws=test_workspace, session=db_session)
    candidate, = (await list_memory(ws=test_workspace, session=db_session))["items"]
    assert candidate["status"] == "CANDIDATE"
    assert candidate["source_ref"] == f"learning_task:{task.id}"
    assert candidate["meta"]["completed_at"] == task.completed_at.isoformat()

    other = Workspace(slug="memory-test-" + uuid4().hex, name="Other workspace")
    db_session.add(other)
    await db_session.flush()
    assert (await list_memory(ws=other, session=db_session))["items"] == []
    for action in (confirm_memory, delete_memory):
        with pytest.raises(HTTPException) as error:
            await action(candidate["id"], ws=other, session=db_session)
        assert error.value.status_code == 404
    workspace_id = test_workspace.id
    await db_session.rollback()

    workspace = await db_session.get(Workspace, workspace_id)
    for _ in range(2):
        await confirm_memory(candidate["id"], ws=workspace, session=db_session)
    approved, = (await list_memory(ws=workspace, session=db_session))["items"]
    assert approved["status"] == "ACTIVE"
    assert approved["revision"] == candidate["revision"] + 1
    assert approved["eligible_for_personalization"] is False

    for _ in range(2):
        await delete_memory(candidate["id"], ws=workspace, session=db_session)
    assert (await list_memory(ws=workspace, session=db_session))["items"] == []
    dismissed = await db_session.get(MemoryItem, candidate["id"])
    assert dismissed.revision == approved["revision"] + 1


@pytest.mark.asyncio
async def test_old_completed_tasks_backfill_without_reviving_ignored_records(db_session, test_workspace):
    completed, _ = await task_fixture(db_session, test_workspace)
    pending, _ = await task_fixture(db_session, test_workspace)
    completed.status = "DONE"
    completed.completed_at = datetime.now(timezone.utc) - timedelta(days=7)
    await db_session.commit()
    migration = (Path(__file__).resolve().parents[1] / "migrations/016_task_completion_memory.sql").read_text(encoding="utf-8")
    insert_backfill = text(migration.split(";")[1].strip())
    await db_session.execute(insert_backfill)
    await db_session.execute(insert_backfill)
    await db_session.commit()
    candidate, = (await list_memory(ws=test_workspace, session=db_session))["items"]
    assert candidate["meta"]["source_task_id"] == completed.id != pending.id
    assert datetime.fromisoformat(candidate["meta"]["completed_at"]) == completed.completed_at
    assert candidate["status"] == "CANDIDATE"
    await delete_memory(candidate["id"], ws=test_workspace, session=db_session)
    await db_session.execute(insert_backfill)
    await db_session.commit()
    assert (await list_memory(ws=test_workspace, session=db_session))["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["WRONG", "CORRECT"])
async def test_task_links_actual_rubric_answer_and_evidence_once(db_session, test_workspace, verdict):
    from app.services import planner
    skill = Skill(key="test-source-" + uuid4().hex[:12], name="Fixture RAG")
    q = Question(short_code="fixture-" + uuid4().hex[:12], workspace_id=test_workspace.id,
                 title="How do you measure retrieval recall?", topic="rag", skill=skill,
                 qtype="open", rubric={"points": [{"title": "检索评估", "detail": "标注集与召回率"}]})
    eq = ExamQuestion(question=q, order_no=1,
                      frozen_snapshot={"title": q.title, "rubric": q.rubric, "skill_key": skill.key,
                                       "submitted_answer": {"text_answer":"No measurement is needed."}},
                      answer=ExamAnswer(points=0 if verdict == "WRONG" else 4, max_points=5, verdict=verdict, judge_version="judge-v1",
                                        text_answer="No measurement is needed.",
                                        feedback={"missed_points": ["检索评估"], "advice": "Missing recall measurement."}))
    exam = Exam(workspace_id=test_workspace.id, kind="diagnosis", title="Fixture diagnosis",
                status="COMPLETED", questions=[eq])
    db_session.add(exam)
    await db_session.flush()
    ev = Evidence(short_code=f"EV-X{exam.id}-1", workspace_id=test_workspace.id, skill_id=skill.id,
                  kind="EXAM_ANSWER", title="Fixture observation", source_ref=f"exam:{exam.id}#q1",
                  grade="LOW", support=-.4)
    db_session.add(ev)
    await db_session.flush()
    tasks = await planner.plan_tasks_for_exam(db_session, test_workspace.id, exam)
    assert len(tasks) == 1
    task = tasks[0]
    assert (task.source_exam_id, task.source_exam_question_id, task.source_evidence_id) == (exam.id, eq.id, ev.id)
    assert task.skill_id == skill.id and task.source_criterion == "检索评估"
    assert task.content["observation"]["answer_excerpt"] == eq.answer.text_answer
    repeated = await planner.plan_tasks_for_exam(db_session, test_workspace.id, exam)
    assert [t.id for t in repeated] == [task.id]
    assert not (await db_session.execute(select(LearningTask).where(LearningTask.retest_of == task.id))).scalars().all()


@pytest.mark.asyncio
async def test_future_retest_cannot_claim_delayed_retention_early(db_session, test_workspace):
    from app.api.v1 import learning
    task, _ = await task_fixture(db_session, test_workspace, "RETEST")
    with pytest.raises(HTTPException) as error:
        await learning.start_retest(task.id, ws=test_workspace, session=db_session)
    assert error.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("minutes_past_duration", "expired"),
    [(3, True), (-1, False), (1, False)],
    ids=["expired", "within-duration", "within-grace"],
)
async def test_due_retest_reopens_only_expired_attempts(
    db_session, test_workspace, monkeypatch, minutes_past_duration, expired
):
    from app.api.deps import get_workspace
    from app.db import get_session
    from app.main import app

    parent, skill = await task_fixture(db_session, test_workspace)
    task, _ = await task_fixture(db_session, test_workspace, "RETEST")
    parent.status = "DONE"
    task.retest_of = parent.id
    task.skill_id = skill.id
    task.due_date = date.today() - timedelta(days=1)
    db_session.add_all([
        Question(short_code="retry-" + uuid4().hex[:12], workspace_id=test_workspace.id,
                 skill_id=skill.id, title=title, qtype="open", topic="rag",
                 rubric={"max_points": 5, "points": [
                     {"title": title, "detail": "Describe a labeled evaluation set."}
                 ]})
        for title in ("Measure retrieval recall", "Measure retrieval precision")
    ])
    await db_session.commit()

    monkeypatch.setitem(app.dependency_overrides, get_workspace, lambda: test_workspace)
    monkeypatch.setitem(app.dependency_overrides, get_session, lambda: db_session)
    retest_url = f"/api/v1/learning/tasks/{task.id}/retest"
    saved_answer = "Compare retrieval results against a labeled set."
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as client:
        initial = await client.post(retest_url)
        assert initial.status_code == 200, initial.text
        prior_id = initial.json()["exam_id"]
        prior_url = f"/api/v1/exams/{prior_id}"
        saved = await client.post(
            f"{prior_url}/answers", json={"order_no": 1, "text_answer": saved_answer}
        )
        assert saved.status_code == 200, saved.text

        prior = await db_session.get(Exam, prior_id)
        prior.started_at = datetime.now(timezone.utc) - timedelta(
            minutes=prior.duration_minutes + minutes_past_duration
        )
        await db_session.commit()

        reopened = await client.post(retest_url)
        assert reopened.status_code == 200, reopened.text
        reopened_id = reopened.json()["exam_id"]
        assert reopened.json()["mode"] == "delayed"
        if expired:
            assert reopened_id != prior_id
        else:
            assert reopened_id == prior_id

        reopened_url = f"/api/v1/exams/{reopened_id}"
        detail = await client.get(reopened_url)
        assert detail.status_code == 200
        assert detail.json()["status"] == "ACTIVE"
        repeat = await client.post(retest_url)
        assert repeat.status_code == 200
        assert repeat.json()["exam_id"] == reopened_id

        old_detail = await client.get(prior_url)
        assert old_detail.status_code == 200
        assert old_detail.json()["status"] == ("EXPIRED" if expired else "ACTIVE")
        old_question, = old_detail.json()["questions"]
        assert old_question["answer"]["text_answer"] == saved_answer
        if expired:
            new_question, = detail.json()["questions"]
            assert new_question["short_code"] != old_question["short_code"]
            assert new_question["answer"] is None
            saved = await client.post(
                f"{reopened_url}/answers", json={"order_no": 1, "text_answer": "Use labeled queries."}
            )
            assert saved.status_code == 200, saved.text


@pytest.mark.asyncio
async def test_immediate_check_requires_learning_progress(db_session, test_workspace):
    from app.api.v1 import learning
    task, _ = await task_fixture(db_session, test_workspace)
    with pytest.raises(HTTPException) as error:
        await learning.start_retest(task.id, ws=test_workspace, session=db_session)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_new_question_check_is_linked_and_request_is_idempotent(db_session, test_workspace):
    from app.api.v1 import learning
    task, skill = await task_fixture(db_session, test_workspace)
    original = Question(short_code="fixture-old-" + uuid4().hex[:10], workspace_id=test_workspace.id,
                        skill_id=skill.id, title="Original retrieval question", qtype="open", topic="rag")
    fresh = Question(short_code="fixture-new-" + uuid4().hex[:10], workspace_id=test_workspace.id,
                     skill_id=skill.id, title="New retrieval transfer question", qtype="open", topic="rag",
                     rubric={"max_points":5,"points":[{"title":"Explain retrieval grounding","detail":"Explain how retrieved sources support an answer."}]})
    db_session.add_all([original, fresh])
    await db_session.flush()
    eq = ExamQuestion(question_id=original.id, order_no=1)
    source = Exam(workspace_id=test_workspace.id, kind="diagnosis", title="Fixture source",
                  status="COMPLETED", questions=[eq])
    db_session.add(source)
    await db_session.flush()
    task.source_exam_id, task.source_exam_question_id = source.id, eq.id
    await db_session.commit()
    await complete_task(task.id, ws=test_workspace, session=db_session)
    check = await learning.start_retest(task.id, ws=test_workspace, session=db_session)
    same = await learning.start_retest(task.id, ws=test_workspace, session=db_session)
    assert check["exam_id"] == same["exam_id"]
    exam = await db_session.get(Exam, check["exam_id"])
    assert [q.question_id for q in exam.questions] == [fresh.id]
    retest = await db_session.get(LearningTask, exam.retest_task_id)
    assert retest.retest_of == task.id and retest.content["verification_mode"] == "immediate"
    assert retest.status != "DONE"
    exam.status = "ABORTED"
    db_session.add(Question(short_code="retry-"+uuid4().hex[:10], workspace_id=test_workspace.id,
        skill_id=skill.id, title="Another new question", qtype="open", topic="rag",
        rubric={"max_points":5,"points":[{"title":"Explain retrieval grounding","detail":"Verify source support in a different scenario."}]}))
    await db_session.commit()
    replacement = await learning.start_retest(task.id, ws=test_workspace, session=db_session)
    assert replacement["exam_id"] != exam.id


@pytest.mark.asyncio
async def test_teaching_uses_source_observation_and_keeps_private_material_scoped(db_session, test_workspace):
    import json

    from app.models import Workspace
    from app.services.teaching import build_teaching_content
    task, skill = await task_fixture(db_session, test_workspace)
    other = Workspace(slug="other-" + uuid4().hex[:10], name="Other private workspace")
    db_session.add(other)
    await db_session.flush()
    db_session.add(Question(short_code="private-" + uuid4().hex[:10], workspace_id=other.id,
        skill_id=skill.id, title="OTHER_PRIVATE_SENTINEL", topic="rag", qtype="open",
        answer_html="Other user's private answer", difficulty=1))
    task.content = {"observation": {"answer_excerpt": "SOURCE_ANSWER_SENTINEL",
        "criterion": "检索评估", "criterion_detail": "SOURCE_RUBRIC_SENTINEL"}}
    await db_session.flush()
    class CapturingAdapter:
        active = True
        user = ""
        async def chat(self, system, user, **kwargs):
            self.user = user
            return {"content": json.dumps({"analogy":"a", "example":"b", "principles":["c"],
                                           "steps":["d"], "verify":"e"})}
    adapter = CapturingAdapter()
    await build_teaching_content(db_session, adapter, task=task, skill_name=skill.name, skill_key=skill.key)
    assert "SOURCE_ANSWER_SENTINEL" in adapter.user
    assert "SOURCE_RUBRIC_SENTINEL" in adapter.user
    assert "OTHER_PRIVATE_SENTINEL" not in adapter.user


@pytest.mark.asyncio
@pytest.mark.parametrize("matching_title", [True, False])
async def test_retest_pass_does_not_verify_legacy_title_only_gap(db_session, test_workspace, matching_title):
    import datetime as dt

    from app.services.learning_loop import complete_retest_for_exam
    task, skill = await task_fixture(db_session, test_workspace, "RETEST")
    task.source_criterion = "检索评估"
    task.content = {"source_criterion_covered": matching_title, "assessment_scope": "Fixture scope"}
    question = Question(short_code="outcome-"+uuid4().hex[:10], title="Fixture new question", skill_id=skill.id,
                        workspace_id=test_workspace.id, qtype="open", topic="rag", rubric={"points":[{"title":"检索评估" if matching_title else "其他评分项"}]})
    eq = ExamQuestion(question=question, order_no=1, frozen_snapshot={'rubric':question.rubric}, answer=ExamAnswer(points=4, max_points=5,
        verdict="CORRECT", feedback={"hit_points":["检索评估" if matching_title else "其他评分项"],
                                     "missed_points":[]}))
    exam = Exam(workspace_id=test_workspace.id, title="Fixture retest", kind="focused", status="COMPLETED",
        score=80, completed_at=dt.datetime.now(dt.timezone.utc), retest_task_id=task.id, questions=[eq])
    db_session.add(exam)
    await db_session.flush()
    await complete_retest_for_exam(db_session, exam)
    assert task.content["retest_result"]["passed"] is True
    assert task.content["retest_result"]["source_criterion_passed"] is None
    center = await learning_center(ws=test_workspace, session=db_session)
    assert task.id in [item["id"] for item in center["completed"]]
