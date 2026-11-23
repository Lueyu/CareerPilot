import asyncio
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Evidence, Question, Workspace
from app.models.study_chat import StudyMessage
from app.services import study_interview_questions, study_notes


GUIDE = {
    "title": "如何让队列任务的重复投递安全？",
    "plain": "超时后，同一个任务可能被送来两次。",
    "spoken": "用相同的业务键去重，并让检查与结果写入保持原子性。",
    "analogy": "取餐单上的编号能区分不同订单，但不能保证后厨已经做完。",
    "steps": [{"step": "读取业务键", "relation": "将业务键交给结果写入步骤"},
              {"step": "在同一个事务中检查并写结果", "relation": "提交后才确认任务"}],
    "comparison": [["提交后确认", "允许重投", "结果需要去重"],
                   ["提前确认", "减少重投", "可能丢任务"]],
    "example": "教学反例：第一次写入之后进程退出，第二次若无唯一键会再次扣款。",
    "followups": [{"question": "如果外部副作用无法加入事务，怎么办？",
                   "answer": "使用同一个业务键调用支持幂等的外部接口。"}],
}


@pytest_asyncio.fixture
async def chat_client(db_session, test_workspace, monkeypatch):
    other = Workspace(slug=uuid4().hex, name="Other tenant")
    db_session.add(other)
    await db_session.commit()
    other_id = other.id
    tokens = [uuid4().hex for _ in range(2)]
    monkeypatch.setattr(get_settings(), "workspace_token_hashes", {
        space.slug: hashlib.sha256(token.encode()).hexdigest()
        for space, token in zip((test_workspace, other), tokens)
    })
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            yield client, *({"Authorization": "Bearer " + token} for token in tokens)
    finally:
        await db_session.rollback()
        await db_session.execute(delete(Workspace).where(Workspace.id == other_id))
        await db_session.commit()


@pytest.fixture
def model(monkeypatch):
    calls, outcomes = [], []

    async def config():
        return {"model": "fixture-model"}

    async def chat_with(self, cfg, system, user, **kwargs):
        assert cfg["model"] == "fixture-model" and kwargs["json_mode"]
        calls.append((system, json.loads(user)))
        if outcomes:
            return await outcomes.pop(0)()
        return {"content": json.dumps(GUIDE, ensure_ascii=False)}

    monkeypatch.setattr(study_interview_questions, "get_llm_config", config)
    monkeypatch.setattr(study_notes.AgentAdapter, "chat_with", chat_with)
    return calls, outcomes


async def new_chat(client, headers):
    response = await client.post("/study/sessions", headers=headers, json={})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def turn(db, sid, request, text, answer, *, status="completed", project=None, coach=None):
    user = StudyMessage(session_id=sid, role="user", request_id=request, action="explain",
                        status="completed", content=text, project_id=project, coach=coach or {})
    db.add(user)
    await db.flush()
    reply = StudyMessage(session_id=sid, role="assistant", request_id=request, action="explain",
                         status=status, content=answer, project_id=project,
                         coach={"parent_message_id": user.id},
                         sources=[{"kind": "project_code", "text": "RAW-PRIVATE-SOURCE"}])
    db.add(reply)
    await db.commit()
    return user, reply


@pytest.mark.asyncio
async def test_chat_question_recovery_uses_all_complete_turns(chat_client, db_session, model):
    client, owner, other = chat_client
    calls, outcomes = model
    sid = await new_chat(client, owner)
    path = f"/study/sessions/{sid}/interview-question"
    initial = await client.get(path, headers=owner)
    assert (initial.status_code, initial.json()) == (200, None)
    assert (await client.post(path, headers=owner)).status_code == 409
    assert (await client.post(path, headers=owner, json={"title": "Caller-controlled"})).status_code == 422
    first, reply = await turn(db_session, sid, "first", "最初的背景", "早期解释", project=15)
    for i in range(14):
        await turn(db_session, sid, f"middle-{i}", f"追问 {i}", f"解释 {i}", project=15)
    correction, _ = await turn(db_session, sid, "correction", "纠正先前说法", "更正后的解释", project=15,
                               coach={"revision_of": reply.id, "parent_message_id": first.id})
    await turn(db_session, sid, "failed", "FAILED-QUESTION", "FAILED-PARTIAL", status="failed")
    await turn(db_session, sid, "pending", "PENDING-QUESTION", "PENDING-PARTIAL", status="generating")
    await turn(db_session, await new_chat(client, other), "other", "OTHER-WORKSPACE", "其他空间资料")

    for method in ("GET", "POST"):
        denied = await client.request(method, path, headers=other)
        assert denied.status_code == 404
    assert not calls

    async def grounded_project_quote():
        return {"content": json.dumps({**GUIDE, "project_context": [
            {"message_id": correction.id, "quote": "纠正先前说法"},
        ]}, ensure_ascii=False)}

    outcomes.append(grounded_project_quote)
    saved = await client.post(path, headers=owner)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert set(body) == {"id", "short_code", "title", "answer_html"}
    assert body["title"] == GUIDE["title"]
    for caption in ("题干", "先说人话", "面试口述", "通俗类比", "步骤拆解", "对照表", "算例或反例", "面试追问"):
        assert caption in body["answer_html"]
    assert "<table>" in body["answer_html"]
    assert '<ol class="answer-flow">' in body["answer_html"]
    assert GUIDE["steps"][0]["relation"] in body["answer_html"]
    assert "<details><summary>" + GUIDE["followups"][0]["question"] + "</summary>" in body["answer_html"]
    assert GUIDE["followups"][0]["answer"] in body["answer_html"]
    assert "未验证的练习" in body["answer_html"] and "项目 15 · 学习者陈述" in body["answer_html"]
    assert "<blockquote>纠正先前说法</blockquote>" in body["answer_html"]
    assert (await client.post(path, headers=owner)).json() == body
    assert (await client.get(path, headers=owner)).json() == body
    assert len(calls) == 1
    system, payload = calls[0]
    assert "实绩" in system and "纠正" in system
    assert len(payload["turns"]) == 16
    assert payload["turns"][0]["user"]["text"] == "最初的背景"
    assert payload["turns"][-1]["user"] == {
        "id": correction.id, "text": "纠正先前说法", "parent_message_id": first.id, "revision_of": reply.id,
    }
    assert payload["turns"][-1]["project_id"] == 15
    transcript = json.dumps(payload, ensure_ascii=False)
    assert all(secret not in transcript for secret in ("OTHER-WORKSPACE", "FAILED-", "PENDING-", "RAW-PRIVATE-SOURCE"))

    await turn(db_session, sid, "latest", "切回通用场景", "新解释")

    async def ungrounded_project_quote():
        return {"content": json.dumps({**GUIDE, "project_context": [
            {"message_id": correction.id, "quote": "我负责了团队所有交付"},
        ]}, ensure_ascii=False)}

    outcomes.append(ungrounded_project_quote)
    assert (await client.post(path, headers=owner)).status_code == 502
    assert (await client.get(path, headers=owner)).json() == body
    new = await client.post(path, headers=owner)
    assert new.status_code == 200 and new.json()["id"] != body["id"]
    assert calls[-1][1]["turns"][-1]["project_id"] is None
    assert (await client.get(path, headers=owner)).json() == new.json()


@pytest.mark.asyncio
async def test_failed_generation_retries_and_result_stays_private_reference(chat_client, db_session, test_workspace, model):
    client, owner, other = chat_client
    calls, outcomes = model
    sid = await new_chat(client, owner)
    path = f"/study/sessions/{sid}/interview-question"
    await turn(db_session, sid, "first", "业务键怎么用？", "业务键标识重复任务。")

    async def outage():
        return None

    async def wrong_shape():
        return {"content": json.dumps({**GUIDE, "html": "<script>forged</script>"})}

    async def injected_text():
        unsafe = '<img src="bad" onerror="boom()"> & <script>unsafe</script>'
        return {"content": json.dumps({**GUIDE, "title": unsafe, "plain": unsafe,
                                       "steps": [{"step": unsafe, "relation": unsafe}] * 2,
                                       "followups": [{"question": unsafe, "answer": unsafe}],
                                       "comparison": [[unsafe] * 3] * 2})}

    outcomes.extend([outage, wrong_shape, injected_text])
    before = await db_session.scalar(select(func.count()).select_from(Evidence))
    for _ in range(2):
        failure = await client.post(path, headers=owner)
        assert failure.status_code == 502, failure.text
        assert (await client.get(path, headers=owner)).json() is None
        assert await db_session.scalar(select(func.count()).select_from(Question).where(
            Question.workspace_id == test_workspace.id)) == 0

    response = await client.post(path, headers=owner)
    assert response.status_code == 200 and len(calls) == 3, response.text
    saved = response.json()
    assert "<img" not in saved["answer_html"] and "<script>" not in saved["answer_html"]
    assert "&lt;img" in saved["answer_html"] and "&amp;" in saved["answer_html"]
    assert "&quot;" in saved["answer_html"]
    bank = await client.get("/bank", headers=owner, params={"scope": "mine"})
    assert [(q["id"], q["review_only"]) for q in bank.json()["items"]] == [(saved["id"], True)]
    uri = f"/bank/{saved['short_code']}"
    detail = await client.get(uri, headers=owner)
    assert detail.status_code == 200 and detail.json()["review_only"] is True
    assert detail.json()["answer_html"] == saved["answer_html"]
    assert (await client.get("/bank", headers=other, params={"scope": "mine"})).json()["items"] == []
    assert (await client.get("/bank", headers=other)).json()["items"] == []
    assert (await client.get(uri, headers=other)).status_code == 404

    pick = {"kind": "bank_exam", "question_ids": [saved["id"]]}
    assert (await client.post("/exams", headers=owner, json=pick)).status_code == 409
    assert (await client.post("/exams", headers=other, json=pick)).status_code == 404
    assert (await client.post("/exams", headers=owner, json={"kind": "diagnosis"})).status_code == 409
    assert (await client.get(f"/retest-mappings/{saved['id']}", headers=owner)).status_code == 404
    assert await db_session.scalar(select(func.count()).select_from(Evidence)) == before

    normal = Question(workspace_id=test_workspace.id, short_code=uuid4().hex, topic="agent",
                      title="Which option prevents duplicate delivery?", qtype="mcq",
                      options=["Stable request key", "Random key on every retry"], rubric={"answer_index": 0})
    db_session.add(normal)
    await db_session.commit()
    assert (await client.patch(f"/bank/questions/{saved['short_code']}", headers=owner,
                               json={"title": "Changed"})).status_code == 409
    assert (await client.post(f"/bank/questions/{normal.short_code}/merge", headers=owner,
                              json={"target_short_code": saved["short_code"]})).status_code == 409
    exam = await client.post("/exams", headers=owner, json={"kind": "diagnosis"})
    assert exam.status_code == 200, exam.text
    assert [question["short_code"] for question in exam.json()["questions"]] == [normal.short_code]


@pytest.mark.asyncio
async def test_same_snapshot_concurrent_requests_share_one_saved_question(chat_client, db_session, test_workspace, model):
    client, owner, _ = chat_client
    calls, outcomes = model
    sid = await new_chat(client, owner)
    path = f"/study/sessions/{sid}/interview-question"
    await turn(db_session, sid, "first", "入口是什么？", "先从第一个步骤讲起。")
    other_sid = await new_chat(client, owner)
    await turn(db_session, other_sid, "first", "其他会话的问题", "其他会话已完成。")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()
        return {"content": json.dumps(GUIDE, ensure_ascii=False)}

    outcomes.append(slow)
    one = asyncio.create_task(client.post(path, headers=owner))
    two = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        two = asyncio.create_task(client.post(path, headers=owner))
        during = await client.get(path, headers=owner)
        assert (during.status_code, during.json()) == (200, None)
        other_result = await asyncio.wait_for(
            client.post(f"/study/sessions/{other_sid}/interview-question", headers=owner), timeout=5)
        assert other_result.status_code == 200
    finally:
        release.set()
        results = await asyncio.gather(one, *([two] if two else []))
    assert [r.status_code for r in results] == [200, 200]
    assert results[0].json() == results[1].json() and len(calls) == 2
    assert await db_session.scalar(select(func.count()).select_from(Question).where(
        Question.workspace_id == test_workspace.id)) == 2


@pytest.mark.asyncio
async def test_archive_during_generation_preserves_saved_question(chat_client, db_session, test_workspace, model):
    client, owner, _ = chat_client
    _, outcomes = model
    sid = await new_chat(client, owner)
    path = f"/study/sessions/{sid}/interview-question"
    await turn(db_session, sid, "first", "第一轮", "完整回复")
    saved = (await client.post(path, headers=owner)).json()
    await turn(db_session, sid, "second", "第二轮", "新的完整回复")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()
        return {"content": json.dumps(GUIDE, ensure_ascii=False)}

    outcomes.append(slow)
    request = asyncio.create_task(client.post(path, headers=owner))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        archived = await asyncio.wait_for(client.delete(f"/study/sessions/{sid}", headers=owner), timeout=5)
        assert archived.status_code == 200, archived.text
    finally:
        release.set()
        abandoned = await request
    assert abandoned.status_code == 404
    assert (await client.get(path, headers=owner)).status_code == 404
    bank = await client.get("/bank", headers=owner, params={"scope": "mine"})
    assert [question["id"] for question in bank.json()["items"]] == [saved["id"]]
