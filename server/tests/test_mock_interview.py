"""Real saved chat turns gate mock stages; model responses are controlled fixtures."""
import re
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_llm_stream_transport import CONFIG, DONE, Body, frame, install

from app.api.v1 import study_chat as api
from app.i18n import using_language
from app.models.study_chat import StudyMessage, StudySession
from app.services import study_chat as worker
from app.services.interview_workflow import WorkflowChange
from app.services.mock_interview import COMMAND


def test_all_displayed_mock_actions_are_navigation_not_answers():
    guide = (Path(__file__).resolve().parents[2] /
             "web/src/interview/chat/MockInterviewGuide.tsx").read_text(encoding="utf-8")
    keys = ("start", "ask", "submit", "finish", "generateReport", "pause", "resume", "skip", "retry",
            "reportLink", "viewFeedback")
    controls = re.findall(r'^\s+(' + "|".join(keys) + r'): "([^"]+)",$', guide, re.MULTILINE)
    assert all(sum(key == name for key, _ in controls) == 2 for name in keys)
    next_labels = re.findall(r'nextLabels: \[(.*?)\]', guide, re.DOTALL)
    assert len(next_labels) == 2
    labels = [label for _, label in controls]
    for block in next_labels:
        stages = re.findall(r'"([^"]+)"', block)
        assert len(stages) == 6
        labels.extend(stages)
    assert [label for label in labels if not COMMAND.fullmatch(label)] == []


async def change(db, ws, sid, run, operation, **fields):
    return await api.edit_interview_workflow(sid, WorkflowChange(operation=operation,
        expected_revision=run["revision"], **fields), db, ws)


async def begin(db, ws):
    sid = (await api.create(api.StartSession(topic="AI engineer practice"), db, ws))["id"]
    run = await api.edit_interview_workflow(sid, WorkflowChange(operation="start", workflow="mock", expected_revision=0), db, ws)
    return sid, run


async def send(db, ws, sid, run, purpose, content="I used a transaction and a unique key.", **overrides):
    body = api.NewMessage(request_id=uuid4().hex, content=content, agent_role="interviewer",
        purpose=purpose, workflow_run_id=run["run_id"], workflow_step=run["current"],
        workflow_revision=run["revision"], **overrides)
    queue = BackgroundTasks()
    ticket = await api.send(sid, body, queue, db, ws)
    return body, ticket, queue


async def finish(db, ticket, status="completed", content="Describe your own experience."):
    reply = await db.get(StudyMessage, ticket["assistant_message_id"], populate_existing=True)
    reply.status, reply.content, reply.expires_at = status, content, None
    await db.commit()
    return reply


@pytest.mark.asyncio
async def test_advance_requires_real_answer_and_completed_feedback(db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, run = await begin(db, ws)
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    # A command prompt and its completed assistant reply are not a candidate answer.
    body = api.NewMessage(request_id=uuid4().hex, content="继续当前步骤", agent_role="interviewer")
    command = await api.send(sid, body, BackgroundTasks(), db, ws)
    await finish(db, command)
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    _, question, _ = await send(db, ws, sid, run, "mock_question", "Ask one preparation question.")
    with pytest.raises(HTTPException, match="409"):
        await send(db, ws, sid, run, "mock_answer")
    await finish(db, question)
    for command_text in ("下一步", "next question", "next stage", "pause interview", "skip current stage",
                         "continue interview", "skip this question", "先暂停一下", "恢复流程", "跳过当前题",
                         "暂停面试", "继续面试", "恢复面试", "暂停模拟面试", "跳过本环节（会记录）",
                         "进入自我介绍", "进入技术问答", "进入系统设计", "进入行为问题", "进入复盘",
                         "开始面试准备", "请面试官提问", "生成复盘报告", "结束面试，查看复盘",
                         "Start interview preparation", "Ask the stage question", "Generate interview report",
                         "Skip this stage (recorded)", "Enter introduction", "Enter technical questions",
                         "Enter system design", "Enter behavioral questions", "Enter review",
                         "Finish interview and view review", "把系统设计改为架构设计", "完成本步", "/mock"):
        with pytest.raises(HTTPException, match="409"):
            await send(db, ws, sid, run, "mock_answer", command_text)
    body, answer, queue = await send(db, ws, sid, run, "mock_answer")
    duplicate = BackgroundTasks()
    assert await api.send(sid, body, duplicate, db, ws) == answer
    assert not duplicate.tasks
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    # A failed or interrupted reply remains retryable and never advances the stage.
    reply = await finish(db, answer, "failed", "partial feedback")
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    retry = await api.retry(sid, reply.id, BackgroundTasks(), db, ws)
    assert retry["attempt"] == 2
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    await finish(db, answer, content="You mentioned transactions. Clarify the uniqueness boundary.")
    resumed = await api.detail(sid, db, ws)
    assert resumed["context"]["interview_workflow"] == run
    saved = resumed["messages"][-2:]
    assert saved[0]["content"] == body.content and saved[0]["workflow"] == saved[1]["workflow"]
    assert saved[1]["status"] == "completed"
    advanced = await change(db, ws, sid, run, "advance")
    assert advanced["current"] == 1 and advanced["completed_steps"] == [0]
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, advanced, "advance")
    with pytest.raises(HTTPException, match="409"):
        await send(db, ws, sid, run, "mock_answer")


@pytest.mark.asyncio
async def test_pause_skip_restart_and_final_report_are_durable(db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, run = await begin(db, ws)
    _, question, _ = await send(db, ws, sid, run, "mock_question")
    await finish(db, question)
    _, answer, _ = await send(db, ws, sid, run, "mock_answer")
    await finish(db, answer)
    paused = await change(db, ws, sid, run, "pause")
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, paused, "advance")
    with pytest.raises(HTTPException, match="409"):
        await send(db, ws, sid, paused, "mock_answer")
    run = await change(db, ws, sid, paused, "resume")
    run = await change(db, ws, sid, run, "advance")
    run = await change(db, ws, sid, run, "skip")
    assert run["current"] == 2 and run["skipped"] == [1] and run["completed_steps"] == [0]
    assert (await api.detail(sid, db, ws))["context"]["interview_workflow"] == run
    # Same run and later step cannot reuse earlier answer/feedback.
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    while run["current"] < 5:
        _, question, _ = await send(db, ws, sid, run, "mock_question")
        await finish(db, question)
        _, answer, _ = await send(db, ws, sid, run, "mock_answer")
        await finish(db, answer, content="Feedback and grounded final report.")
        run = await change(db, ws, sid, run, "advance")
    # Review generates a report directly; it never asks for a fabricated final answer.
    _, report, _ = await send(db, ws, sid, run, "mock_report", "Generate the report from saved answers.")
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "skip")
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    loaded = await worker._load_turn(db, report["assistant_message_id"], 1, {}, {})
    worker._compose_request(loaded, {}, "en")
    assert "interview report" in loaded["instruction"]
    evidence = loaded["request_data"]["mock_interview_turn"]
    assert evidence["skipped_steps"] == [1]
    assert len(evidence["saved_answers"]) == 4
    assert "Do not ask another question" in loaded["instruction"]
    assert run["status"] == "running"
    await finish(db, report, content="Final report grounded in the four saved answers.")
    run = await change(db, ws, sid, run, "advance")
    assert run["status"] == "completed" and run["completed_steps"] == [0, 2, 3, 4, 5]
    assert run["skipped"] == [1]
    old_run = run
    run = await change(db, ws, sid, run, "start", workflow="mock")
    assert run["run_id"] != old_run["run_id"]
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")


@pytest.mark.asyncio
async def test_report_uses_only_the_accepted_replacement_answer(db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, run = await begin(db, ws)
    _, question, _ = await send(db, ws, sid, run, "mock_question")
    await finish(db, question)
    _, old_answer, _ = await send(db, ws, sid, run, "mock_answer", "Invalidated first answer.")
    await finish(db, old_answer, content="Invalidated feedback.")
    run = await change(db, ws, sid, run, "change", label="Replacement preparation")
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    _, question, _ = await send(db, ws, sid, run, "mock_question")
    await finish(db, question)
    _, replacement, _ = await send(db, ws, sid, run, "mock_answer", "Accepted replacement answer.")
    await finish(db, replacement, content="Accepted replacement feedback.")
    run = await change(db, ws, sid, run, "advance")
    while run["current"] < 5:
        run = await change(db, ws, sid, run, "skip")
    _, report, _ = await send(db, ws, sid, run, "mock_report")
    loaded = await worker._load_turn(db, report["assistant_message_id"], 1, {}, {})
    worker._compose_request(loaded, {}, "en")
    evidence = loaded["request_data"]["mock_interview_turn"]["saved_answers"]
    assert loaded["request_data"]["turns"] == []
    assert "earlier_excerpts" not in loaded["request_data"]
    assert evidence == [{"step": 0, "answer_message_id": replacement["user_message_id"],
        "feedback_message_id": replacement["assistant_message_id"],
        "answer": "Accepted replacement answer.", "feedback": "Accepted replacement feedback."}]
    row = await db.get(StudySession, sid)
    legacy = {key: value for key, value in run.items() if key != "accepted_answers"}
    row.context = {**row.context, "interview_workflow": legacy}
    await db.commit()
    loaded = await worker._load_turn(db, report["assistant_message_id"], 1, {}, {})
    assert loaded["report_evidence"] == []


@pytest.mark.asyncio
async def test_stale_generation_and_question_prompt_are_bound_to_step(db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, run = await begin(db, ws)
    _, question, _ = await send(db, ws, sid, run, "mock_question")
    loaded = await worker._load_turn(db, question["assistant_message_id"], 1, {}, {})
    worker._compose_request(loaded, {}, "en")
    assert "Ask exactly ONE question" in loaded["instruction"]
    assert loaded["request_data"]["mock_interview_turn"]["purpose"] == "mock_question"
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "skip")
    await finish(db, question, "failed")
    skipped = await change(db, ws, sid, run, "skip")
    with pytest.raises(HTTPException, match="409"):
        await api.retry(sid, question["assistant_message_id"], BackgroundTasks(), db, ws)
    with using_language("en"), pytest.raises(HTTPException, match="Submit your answer"):
        await change(db, ws, sid, skipped, "advance")
    # Even forged run metadata cannot bind a message to another step/revision.
    with pytest.raises(HTTPException, match="409"):
        await send(db, ws, sid, {**skipped, "run_id": uuid4().hex}, "mock_question")


@pytest.mark.asyncio
async def test_model_lifecycle_preserves_scope_and_failure_never_advances(monkeypatch, db_session, test_workspace):
    db, ws = db_session, test_workspace
    monkeypatch.setattr(worker, "SessionLocal", async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False))

    async def config():
        return CONFIG

    monkeypatch.setattr(worker, "get_llm_config", config)
    sid, run = await begin(db, ws)
    _, question, queue = await send(db, ws, sid, run, "mock_question", "Ask about my target role.")
    with monkeypatch.context() as transport:
        requests = install(transport, Body([frame("What role do you want to practise for?") + frame(finish="stop") + DONE]))
        await queue()
    saved = (await api.detail(sid, db, ws))["messages"][-1]
    assert saved["status"] == "completed" and saved["workflow"]["purpose"] == "mock_question"
    assert "Ask exactly ONE question" in requests[0]["messages"][0]["content"]
    _, answer, queue = await send(db, ws, sid, run, "mock_answer", "AI Agent engineer, focusing on system design.")
    with monkeypatch.context() as transport:
        install(transport, Body([frame("Incomplete feedback")]))
        await queue()
    detail = await api.detail(sid, db, ws)
    assert detail["messages"][-1]["status"] == "failed"
    assert detail["context"]["interview_workflow"]["current"] == 0
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, run, "advance")
    retry_queue = BackgroundTasks()
    await api.retry(sid, answer["assistant_message_id"], retry_queue, db, ws)
    with monkeypatch.context() as transport:
        install(transport, Body([frame("Your goal is clear. Use one concrete project to practise design tradeoffs.") + frame(finish="stop") + DONE]))
        await retry_queue()
    detail = await api.detail(sid, db, ws)
    assert detail["messages"][-1]["status"] == "completed"
    assert detail["messages"][-1]["workflow"]["run_id"] == run["run_id"]
    advanced = await change(db, ws, sid, run, "advance")
    assert advanced["current"] == 1


@pytest.mark.asyncio
async def test_legacy_mock_rows_resume_at_current_step_without_reusing_old_chat(db_session, test_workspace):
    db, ws = db_session, test_workspace
    sid, run = await begin(db, ws)
    old = {k: run[k] for k in ("id", "revision", "current", "status", "steps", "skipped")}
    old.update(current=2, revision=7, status="paused")
    row = await db.get(StudySession, sid)
    row.context = {**row.context, "interview_workflow": old}
    await db.commit()
    first = await api.detail(sid, db, ws)
    initialized = first["context"]["interview_workflow"]
    assert initialized["current"] == 2 and initialized["status"] == "paused"
    assert initialized["run_id"] and initialized["step_revision"] == 7
    assert (await api.detail(sid, db, ws))["context"]["interview_workflow"] == initialized
    resumed = await change(db, ws, sid, initialized, "resume")
    with pytest.raises(HTTPException, match="409"):
        await change(db, ws, sid, resumed, "advance")
    _, question, _ = await send(db, ws, sid, resumed, "mock_question")
    assert question["status"] == "generating"

pytestmark = pytest.mark.usefixtures("mock_private_billing")
