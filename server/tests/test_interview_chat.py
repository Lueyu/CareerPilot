"""Conversation roles and workflow state, using the existing isolated workspace fixture."""
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import delete

from app.api.v1 import study_chat as api
from app.models import Question, Workspace
from app.services import study_chat as worker
from app.services.interview_workflow import WorkflowChange as Change, change_workflow


def test_workflow_transitions_require_current_revision_and_keep_skips():
    run = change_workflow(None, Change(operation="start", workflow="mock", expected_revision=0))
    assert run["current"] == 0 and "系统设计" in run["steps"]
    paused = change_workflow(run, Change(operation="pause", expected_revision=1))
    with pytest.raises(ValueError):
        change_workflow(paused, Change(operation="advance", expected_revision=2))
    changed = change_workflow(paused, Change(operation="change", expected_revision=2, step=3, label="算法讨论"))
    assert changed["steps"][3] == "算法讨论" and run["steps"][3] == "系统设计"
    with pytest.raises(ValueError):
        change_workflow(changed, Change(operation="resume", expected_revision=2))
    resumed = change_workflow(changed, Change(operation="resume", expected_revision=3))
    skipped = change_workflow(resumed, Change(operation="skip", expected_revision=4))
    assert skipped["current"] == 1 and skipped["skipped"] == [0]
    while skipped["status"] != "completed":
        skipped = change_workflow(skipped, Change(operation="advance", expected_revision=skipped["revision"]))
    assert skipped["current"] == len(skipped["steps"]) and skipped["skipped"] == [0]


@pytest.mark.asyncio
async def test_durable_role_and_workflow_scope(db_session, test_workspace):
    db, ws = db_session, test_workspace
    foreign = Workspace(slug=uuid4().hex, name="Other test workspace")
    db.add(foreign)
    await db.commit()
    try:
        created = await api.create(api.StartSession(), db, ws)
        sid = created["id"]
        command = Change(operation="start", workflow="project", expected_revision=0)
        run = await api.edit_interview_workflow(sid, command, db, ws)
        with pytest.raises(HTTPException, match="409"):
            await api.edit_interview_workflow(sid, command, db, ws)
        with pytest.raises(HTTPException, match="404"):
            await api.edit_interview_workflow(sid, command, db, foreign)
        body = api.NewMessage(request_id="role", content="请按 STAR 解释这段经历", agent_role="resume")
        queue = BackgroundTasks()
        turn = await api.send(sid, body, queue, db, ws)
        detail = await api.detail(sid, db, ws)
        assert detail["context"]["interview_workflow"] == run
        assert [item["agent_role"] for item in detail["messages"]] == ["resume", "resume"]
        assert detail["messages"][0]["content"] == body.content
        loaded = await worker._load_turn(db, turn["assistant_message_id"], 1, {}, {})
        assert loaded is not None
        worker._compose_request(loaded, {}, "en")
        assert loaded["coach"]["agent_role"] == "resume"
        with pytest.raises(HTTPException, match="409"):
            await api.send(sid, body.model_copy(update={"agent_role": "coach"}), queue, db, ws)
    finally:
        await db.execute(delete(Workspace).where(Workspace.id == foreign.id))
        await db.commit()


@pytest.mark.asyncio
async def test_inline_question_keeps_reference_hidden_until_user_sends(db_session, test_workspace):
    db, ws = db_session, test_workspace
    question = Question(workspace_id=ws.id, short_code=uuid4().hex, title="幂等测试题", topic="agent",
                        answer_html="写入需要唯一键")
    db.add(question)
    await db.commit()
    assert await api.interview_question_topic(question.id, db, ws) == {"id": question.id, "title": question.title}
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    answer = "我的作答：使用事务。"
    body = api.NewMessage(request_id="question-feedback", content=answer, action="critique",
                          agent_role="interviewer", agent_question_id=question.id)
    await api.send(sid, body, BackgroundTasks(), db, ws)
    messages = (await api.detail(sid, db, ws))["messages"]
    assert messages[0]["content"] == answer
    assert any(source["id"] == question.id and "唯一键" in source["text"] for source in messages[1]["sources"])
