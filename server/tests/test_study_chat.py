"""Persistent chat state, source ownership and SSE lifecycle against isolated PostgreSQL."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import study_chat as api
from app.models import Exam, ExamAnswer, ExamQuestion, LearningGoal, LearningTask, Question, Skill, SkillState, Workspace
from app.models.project import Project
from app.models.study_chat import StudyMessage
from app.services import study_chat as worker
from test_llm_stream_transport import Body, CONFIG, DONE, frame, install


@pytest.mark.asyncio
async def test_owned_context_uses_frozen_question_original_answer_goal_and_scoped_project(db_session, test_workspace):
    db, ws = db_session, test_workspace
    other = Workspace(slug=uuid4().hex, name="Other")
    skill = Skill(key=uuid4().hex, name="测试领域 " + uuid4().hex)
    db.add_all([other, skill])
    await db.flush()
    question = Question(workspace_id=ws.id, short_code=uuid4().hex, title="今天的题目", topic="agent",
                        skill_id=skill.id)
    task = LearningTask(workspace_id=ws.id, skill_id=skill.id, title="计划中的学习任务", kind="UNDERSTAND",
                        source_criterion="解释幂等约束")
    exam = Exam(workspace_id=ws.id, title="已评阅", kind="diagnosis", status="COMPLETED")
    project = Project(workspace_id=ws.id, name="我的示例", repo_url="", owner="", repo="",
                      meta={"kind": "manual", "profile": {"role": "我维护作业调度器"}})
    db.add_all([question, exam, task, project])
    await db.flush()
    eq = ExamQuestion(exam_id=exam.id, question_id=question.id, order_no=1, frozen_snapshot={
        "title": "当时的原题", "submitted_answer": {"text_answer": "我的首次原答"}})
    db.add_all([eq, LearningGoal(workspace_id=ws.id, source_task_id=task.id, origin_key=uuid4().hex,
        skill_ids=[skill.id], target_behavior="用唯一键防止重复提交", success_criteria_version="v1")])
    await db.flush()
    db.add(ExamAnswer(exam_question_id=eq.id, text_answer="后来改写", points=0.0,
                      feedback={"缺口": "没有提到重复提交"}, judge_version="verified-test"))
    await db.commit()
    try:
        for kind, identifier in (("question", question.id), ("exam_question", eq.id), ("task", task.id)):
            request = api.StartSession(source_type=kind, source_id=identifier)
            created = await api.create(request, db, ws)
            sources = {item["kind"]: item["text"] for item in created["context"]["sources"]}
            if kind == "task":
                assert "解释幂等约束" in sources["task"] and "用唯一键防止重复提交" in sources["goal"]
            else:
                assert "当时的原题" in sources["question"] and sources["user_answer"] == "我的首次原答"
                assert "没有提到重复提交" in sources["assessment"]
            assert created["context"]["skill_ids"] == [skill.id]
            with pytest.raises(HTTPException, match="404"):
                await api.create(request, db, other)
        sid = created["id"]
        with pytest.raises(HTTPException, match="404"):
            await api.detail(sid, db, other)
        with pytest.raises(HTTPException, match="404"):
            await api.edit(sid, api.EditSession(title="unauthorized"), db, other)
        foreign_sid = (await api.create(api.StartSession(), db, other))["id"]
        body = api.NewMessage(request_id="project", content="结合项目说明", action="project", project_id=project.id)
        with pytest.raises(HTTPException, match="404"):
            await api.send(foreign_sid, body, BackgroundTasks(), db, other)
        assert (await api.detail(foreign_sid, db, other))["messages"] == []
        assert (await api.project_sources(db, ws.id, [], project.id))[0]["text"] == "我维护作业调度器"
    finally:
        await db.execute(delete(Workspace).where(Workspace.id == other.id))
        await db.commit()


@pytest.mark.asyncio
async def test_durable_stream_replay_failure_interrupt_retry_fences_stale_attempt(monkeypatch, db_session, test_workspace):
    db, ws = db_session, test_workspace
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)

    async def config():
        return CONFIG

    async def no_open_practice(session, ws, row, question_text):
        # No active exercise in this fixture. Its help ledger is covered by practice tests.
        pass

    monkeypatch.setattr(worker, "get_llm_config", config)
    monkeypatch.setattr(api, "mark_help", no_open_practice)
    created = await api.create(api.StartSession(), db, ws)
    sid = created["id"]
    body = api.NewMessage(request_id="turn-1", content="为什么要使用幂等键")
    queue = BackgroundTasks()
    ticket = await api.send(sid, body, queue, db, ws)
    duplicate_queue = BackgroundTasks()
    assert await api.send(sid, body, duplicate_queue, db, ws) == ticket and not duplicate_queue.tasks
    with pytest.raises(HTTPException, match="409"):
        await api.send(sid, body.model_copy(update={"content": "不同内容"}), duplicate_queue, db, ws)
    rid = ticket["assistant_message_id"]
    before = await api.detail(sid, db, ws)
    assert before["title"] == body.content
    assert [(m["role"], m["status"]) for m in before["messages"]] == [("user", "completed"), ("assistant", "generating")]

    first_chunk = asyncio.Event()
    release = asyncio.Event()

    class Incomplete(Body):
        async def __aiter__(self):
            yield frame(reasoning_content="hidden") + frame("已保存的开头")
            first_chunk.set()
            await release.wait()

    with monkeypatch.context() as transport:
        requests = install(transport, Incomplete([]))
        generating = asyncio.create_task(queue())
        try:
            await asyncio.wait_for(first_chunk.wait(), timeout=10)
            assert (await api.detail(sid, db, ws))["messages"][-1]["content"] == "已保存的开头"
        finally:
            release.set()
            await generating
    failed = (await api.detail(sid, db, ws))["messages"][-1]
    assert failed["status"] == "failed" and failed["content"] == "已保存的开头" and failed["error"]
    assert requests[0]["model"] == CONFIG["model"] and "为什么" in requests[0]["messages"][1]["content"]

    queue = BackgroundTasks()
    await api.retry(sid, rid, queue, db, ws)
    assert (await api.interrupt(sid, rid, db, ws))["status"] == "interrupted"
    assert not await worker.publish(rid, 2, content="旧任务", status="completed")
    queue = BackgroundTasks()
    await api.retry(sid, rid, queue, db, ws)
    # Simulate process loss by expiring the persisted lease, without sleeping.
    await db.execute(update(StudyMessage).where(StudyMessage.id == rid).values(
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db.commit()
    await worker.recover_interrupted()
    assert (await api.detail(sid, db, ws))["messages"][-1]["status"] == "interrupted"
    queue = BackgroundTasks()
    await api.retry(sid, rid, queue, db, ws)
    with monkeypatch.context() as transport:
        install(transport, Body([frame("成功恢复") + frame(finish="stop") + DONE]))
        await queue()
    final = (await api.detail(sid, db, ws))["messages"]
    assert len(final) == 2 and final[0]["content"] == body.content
    assert (final[1]["status"], final[1]["content"], final[1]["attempt"]) == ("completed", "成功恢复", 4)
    assert final[1]["model"] == CONFIG["model"]
    assert not await worker.publish(rid, 3, status="failed")
    followup = api.NewMessage(request_id="turn-2", action="example", content="换一个例子")
    queue = BackgroundTasks()
    await api.send(sid, followup, queue, db, ws)
    with monkeypatch.context() as transport:
        requests = install(transport, Body([frame("例子") + frame(finish="stop") + DONE]))
        await queue()
    turns = requests[0]["messages"][1]["content"]
    assert all(text in turns for text in (body.content, "成功恢复", followup.content))
    assert "换一个不同" in requests[0]["messages"][0]["content"]
    assert await db.scalar(select(func.count()).select_from(SkillState).where(SkillState.workspace_id == ws.id)) == 0


@pytest.mark.asyncio
async def test_two_tabs_submit_once_and_owner_checks_cover_reply_actions(monkeypatch, db_session, test_workspace):
    ws = test_workspace
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    sid = (await api.create(api.StartSession(topic="RAG"), db_session, ws))["id"]
    async def no_open_practice(session, owner, row, question_text):
        pass
    monkeypatch.setattr(api, "mark_help", no_open_practice)
    body = api.NewMessage(content="检索为何失效", request_id="same-browser-turn")

    async def submit():
        queue = BackgroundTasks()
        async with factory() as connection:
            result = await api.send(sid, body, queue, connection, ws)
            return result, len(queue.tasks)

    (first, n1), (second, n2) = await asyncio.gather(submit(), submit())
    assert first == second and n1 + n2 == 1
    rid = first["assistant_message_id"]
    other = Workspace(slug=uuid4().hex, name="Other")
    db_session.add(other)
    await db_session.commit()
    try:
        foreign_sid = (await api.create(api.StartSession(), db_session, other))["id"]
        for session_id in (sid, foreign_sid):
            with pytest.raises(HTTPException, match="404"):
                await api.interrupt(session_id, rid, db_session, other)
            with pytest.raises(HTTPException, match="404"):
                await api.retry(session_id, rid, BackgroundTasks(), db_session, other)
        state = await api.detail(sid, db_session, ws)
        assert len(state["messages"]) == 2 and state["messages"][1]["status"] == "generating"
    finally:
        await db_session.execute(delete(Workspace).where(Workspace.id == other.id))
        await db_session.commit()
