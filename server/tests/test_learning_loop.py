"""Relational integration tests use explicit fixtures, never a real model."""
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v1.learning import complete_task, learning_center
from app.models import Evidence, Event, Exam, ExamAnswer, ExamQuestion, LearningTask, Question, Skill, SkillState


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
@pytest.mark.parametrize("covered,hit,expected", [(True,False,False), (False,False,None), (True,True,True)])
async def test_retest_pass_does_not_hide_original_criterion_gap(db_session, test_workspace, covered, hit, expected):
    import datetime as dt
    from app.services.learning_loop import complete_retest_for_exam
    task, skill = await task_fixture(db_session, test_workspace, "RETEST")
    task.source_criterion = "检索评估"
    task.content = {"source_criterion_covered": covered, "assessment_scope": "Fixture scope"}
    question = Question(short_code="outcome-"+uuid4().hex[:10], title="Fixture new question", skill_id=skill.id,
                        workspace_id=test_workspace.id, qtype="open", topic="rag", rubric={"points":[{"title":"检索评估" if covered else "其他评分项"}]})
    eq = ExamQuestion(question=question, order_no=1, frozen_snapshot={'rubric':question.rubric}, answer=ExamAnswer(points=4, max_points=5,
        verdict="CORRECT", feedback={"hit_points":["检索评估"] if hit else ["其他评分项"],
                                     "missed_points":[] if hit else ["检索评估"]}))
    exam = Exam(workspace_id=test_workspace.id, title="Fixture retest", kind="focused", status="COMPLETED",
        score=80, completed_at=dt.datetime.now(dt.timezone.utc), retest_task_id=task.id, questions=[eq])
    db_session.add(exam)
    await db_session.flush()
    await complete_retest_for_exam(db_session, exam)
    assert task.content["retest_result"]["source_criterion_passed"] is expected
    center = await learning_center(ws=test_workspace, session=db_session)
    assert task.id in [item["id"] for item in center["completed"]]
