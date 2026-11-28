import asyncio
import json
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from credential_fixtures import enroll_token
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_llm_stream_transport import DONE, frame

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Evidence, Question, Workspace
from app.models.question import STUDY_CHAT_REVIEW_META
from app.models.study_chat import StudyInterviewQuestion, StudyMessage
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
    for space, token in zip((test_workspace, other), tokens):
        await enroll_token(db_session, token, space.slug)
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
        return {"model": "fixture-model", "provider": "fixture", "api_key": "fixture", "base_url": "https://fixture.invalid"}

    async def chat_with(self, cfg, system, user, **kwargs):
        assert cfg["model"] == "fixture-model" and kwargs["json_mode"]
        calls.append((system, json.loads(user)))
        if outcomes:
            return await outcomes.pop(0)()
        return {"content": json.dumps(GUIDE, ensure_ascii=False)}

    monkeypatch.setattr(study_interview_questions, "get_llm_config", config)
    monkeypatch.setattr(study_notes.AgentAdapter, "chat_with", chat_with)
    return calls, outcomes


async def new_chat(client, headers, **body):
    response = await client.post("/study/sessions", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_switching_language_creates_one_question_per_language(chat_client, db_session, model):
    client, owner, other = chat_client
    calls, outcomes = model
    sid = await new_chat(client, owner)
    original = "我的原始回答"
    user, _ = await turn(db_session, sid, "bilingual", "如何处理重复投递？", original)
    path = f"/study/sessions/{sid}/interview-question"
    headers = {**owner, "Accept-Language": "en-US"}

    async def false_attribution():
        return {"content": json.dumps({**GUIDE, "project_context": [
            {"message_id": user.id, "quote": user.content},
        ]})}

    outcomes.append(false_attribution)
    assert (await client.post(path, headers=headers)).status_code == 502
    assert (await client.get(path, headers=headers)).json() is None

    chinese = await client.post(path, headers=owner)
    assert chinese.is_success, chinese.text

    async def english_guide():
        return {"content": json.dumps({
            **GUIDE, "title": "How do you handle duplicate deliveries?", "project_context": [],
        })}

    outcomes.append(english_guide)
    english = await client.post(path, headers=headers)
    assert english.is_success, english.text
    assert english.json()["id"] != chinese.json()["id"]
    assert english.json()["title"] == "How do you handle duplicate deliveries?"
    assert (await client.post(path, headers=headers)).json() == english.json()
    assert (await client.post(path, headers=owner)).json() == chinese.json()
    assert len(calls) == 3 and "in English" in calls[-1][0]
    assert "没有关联项目" in calls[1][0] and "no linked project" in calls[-1][0]
    for system, _ in calls:
        schema = json.loads(system.rsplit("\n", 1)[1])
        assert schema["properties"]["project_context"]["maxItems"] == 0
        assert "project_context" not in schema["required"]
    assert original in json.dumps(calls[-1][1], ensure_ascii=False)
    assert (await client.get(path, headers={**other, "Accept-Language": "en"})).status_code == 404


async def turn(db, sid, request, text, answer, *, status="completed", project=None, coach=None,
               reply_coach=None, reply_model=None):
    user = StudyMessage(session_id=sid, role="user", request_id=request, action="explain",
                        status="completed", content=text, project_id=project, coach=coach or {})
    db.add(user)
    await db.flush()
    reply = StudyMessage(session_id=sid, role="assistant", request_id=request, action="explain",
                         status=status, content=answer, project_id=project,
                         coach={"parent_message_id": user.id, **(reply_coach or {})}, model=reply_model,
                         sources=[{"kind": "project_code", "text": "RAW-PRIVATE-SOURCE"}])
    db.add(reply)
    await db.commit()
    return user, reply


@pytest.mark.parametrize(("tier", "tier_limit", "study_budget", "config_limit", "expected_limit"), [
    ("flash", 6000, 16000, None, 6000),
    ("strong", 12000, 16000, None, 12000),
    ("flash", 6000, 3200, None, 3200),
    ("strong", 2400, 16000, None, 2400),
    ("flash", 6000, 16000, 1800, 1800),
    ("strong", 12000, 0, None, 0),
])
@pytest.mark.asyncio
async def test_interview_question_respects_budget_and_rejects_truncation(
    chat_client, db_session, monkeypatch, tier, tier_limit, study_budget, config_limit, expected_limit,
):
    client, owner, _ = chat_client
    settings = get_settings()
    monkeypatch.setattr(settings, "study_flash_model", "flash-fixture")
    monkeypatch.setattr(settings, "study_flash_thinking", "enabled")
    monkeypatch.setattr(settings, "study_strong_model", "strong-fixture")
    for name in ("provider", "base_url", "api_key"):
        monkeypatch.setattr(settings, f"study_strong_{name}", "")
    monkeypatch.setattr(settings, f"study_{tier}_output_tokens", tier_limit)
    monkeypatch.setattr(settings, "study_output_budget", study_budget)

    async def config():
        base = {"model": "base-fixture", "provider": "fixture", "api_key": "fixture",
                "base_url": "https://fixture.invalid"}
        if config_limit is not None:
            base["output_limit"] = config_limit
        return base

    monkeypatch.setattr(study_interview_questions, "get_llm_config", config)
    sent = []
    title = "How would you make duplicate task delivery safe?"
    provider_requirement = 5000
    original_client = httpx.AsyncClient

    def event(delta, finish=None):
        return "data: " + json.dumps({
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }) + "\n\n"

    def upstream(request):
        payload = json.loads(request.content)
        sent.append(payload)
        finish = "stop" if payload["max_tokens"] >= provider_requirement else "length"
        # Even valid JSON is unusable when the provider marks the stream truncated.
        body = event({"content": json.dumps({**GUIDE, "title": title})}) + event({}, finish) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(upstream), **kwargs))
    headers = {**owner, "Accept-Language": "en"}
    sid = await new_chat(client, headers)
    await turn(db_session, sid, "budget-fixture", "How do retries affect this design?", "重试时使用相同的业务键。",
               reply_coach={"trace": {"tier": tier}})
    path = f"/study/sessions/{sid}/interview-question"

    response = await client.post(path, headers=headers)
    assert [payload["max_tokens"] for payload in sent] == ([expected_limit] if expected_limit else [])
    saved = await client.get(path, headers=headers)
    if expected_limit < provider_requirement:
        assert response.status_code == 502 and saved.json() is None
    else:
        assert response.status_code == 200, response.text
        assert response.json()["title"] == title
        assert saved.json() == response.json()
    if sent:
        assert sent[0]["model"] == f"{tier}-fixture"
        assert sent[0].get("thinking") == ({"type": "enabled"} if tier == "flash" else None)


@pytest.mark.parametrize(("context", "language", "project"), [
    (8192, "en", 15), (8192, "en", None), (512, "en", 15), (0, "zh-CN", 15), (-1, "en", 15),
])
@pytest.mark.asyncio
async def test_interview_question_fits_full_prompt_in_context(
    chat_client, db_session, monkeypatch, context, language, project,
):
    client, owner, _ = chat_client
    settings = get_settings()
    monkeypatch.setattr(settings, "study_flash_context", context)
    monkeypatch.setattr(settings, "study_flash_output_tokens", 3000)
    monkeypatch.setattr(settings, "study_output_budget", 16000)
    headers = {**owner, "Accept-Language": language}
    sid = await new_chat(client, headers)
    question = '原始引文包含 "café"。'
    answer = "原始回答第一行\n第二行 🧪"
    user, reply = await turn(db_session, sid, "context-fixture", question, answer, project=project)
    path = f"/study/sessions/{sid}/interview-question"
    title = "How do you preserve the original evidence?"

    async def config():
        return {"model": "fixture-model", "provider": "fixture", "api_key": "fixture",
                "base_url": "https://fixture.invalid"}

    sent = []
    original_client = httpx.AsyncClient

    def upstream(request):
        payload = json.loads(request.content)
        prompt_bytes = sum(len(item["content"].encode("utf-8")) for item in payload["messages"])
        sent.append((payload, prompt_bytes))
        if prompt_bytes + 64 + payload["max_tokens"] > context:
            return httpx.Response(400, json={"error": "Synthetic context limit exceeded"})
        guide = {**GUIDE, "title": title}
        if project is not None:
            guide["project_context"] = [{"message_id": user.id, "quote": question}]
        raw = frame(json.dumps(guide, ensure_ascii=False)) + frame(finish="stop") + DONE
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw)

    monkeypatch.setattr(study_interview_questions, "get_llm_config", config)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(upstream), **kwargs))

    response = await client.post(path, headers=headers)
    if context == 8192:
        assert response.status_code == 200, response.text
        [(payload, prompt_bytes)] = sent
        assert payload["max_tokens"] == 3000 <= context - prompt_bytes - 64
        system, transcript = [item["content"] for item in payload["messages"]]
        assert "in English" in system and '"project_context"' in system
        assert json.loads(transcript) == {"turns": [{
            "user": {"id": user.id, "text": question}, "assistant": {"id": reply.id, "text": answer},
            "project_id": project,
        }], "response_language": "en"}
        assert ("&quot;café&quot;" in response.json()["answer_html"]) == (project is not None)
    else:
        assert not sent
        assert response.status_code == 422
        expected = "没有足够的上下文空间" if language == "zh-CN" else "does not have enough context space"
        assert expected in response.json()["detail"]
        assert (await client.get(path, headers=headers)).json() is None


@pytest.mark.parametrize(("repeats", "provider_context", "expected_status"), [
    (700, 8192, 200), (3000, 8192, 422), (700, 3000, 502),
])
@pytest.mark.asyncio
async def test_interview_question_fits_reviewer_transcript(
    chat_client, db_session, monkeypatch, repeats, provider_context, expected_status,
):
    client, owner, _ = chat_client
    settings = get_settings()
    monkeypatch.setattr(settings, "study_flash_context", 8192)
    monkeypatch.setattr(settings, "study_flash_output_tokens", 6000)
    monkeypatch.setattr(settings, "study_output_budget", 16000)
    headers = {**owner, "Accept-Language": "en"}
    sid = await new_chat(client, headers)
    question, answer = "Fixture 中文", "Read the event. " * repeats
    user, _ = await turn(db_session, sid, "context-review", question, answer, project=17,
                         reply_coach={"mode": "auto", "trace": {"tier": "flash"}})
    path = f"/study/sessions/{sid}/interview-question"

    async def config():
        return {"model": "fixture-model", "provider": "fixture", "api_key": "fixture",
                "base_url": "https://fixture.invalid", "output_limit": 4500}

    requests = []
    original_client = httpx.AsyncClient

    def upstream(request):
        payload = json.loads(request.content)
        system, transcript = [item["content"] for item in payload["messages"]]
        # These are the independent reviewer's synthetic provider units, not a model tokenizer.
        units = len((system + transcript).split())
        ceiling = payload["max_tokens"]
        requests.append((len((system + transcript).encode("utf-8")), units, ceiling))
        [turn_input] = json.loads(transcript)["turns"]
        assert turn_input["user"]["text"] == question and turn_input["assistant"]["text"] == answer
        if units + 64 + ceiling > provider_context:
            return httpx.Response(400, json={"error": "Synthetic provider context exceeded"})
        guide = {**GUIDE, "project_context": [{"message_id": user.id, "quote": question}]}
        raw = frame(json.dumps(guide, ensure_ascii=False)) + frame(finish="stop") + DONE
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw)

    monkeypatch.setattr(study_interview_questions, "get_llm_config", config)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(upstream), **kwargs))
    response = await client.post(path, headers=headers)
    assert response.status_code == expected_status, response.text
    saved = await client.get(path, headers=headers)
    if expected_status == 422:
        assert not requests and saved.json() is None
        return
    [(byte_count, units, ceiling)] = requests
    assert byte_count > 8192
    assert 0 < ceiling <= 4500
    if expected_status == 200:
        assert units + 64 + ceiling <= provider_context
        assert question in response.json()["answer_html"]
        assert saved.json() == response.json()
        assert (await client.post(path, headers=headers)).json() == response.json()
        assert len(requests) == 1
    else:
        assert units + 64 + ceiling > provider_context and saved.json() is None


@pytest.mark.parametrize(("global_enabled", "strong_enabled", "session_mode", "latest_metadata", "expected"), [
    (False, True, "auto", {"mode": "auto", "trace": {"tier": "strong"}}, "strong"),
    (True, True, "flash", {"mode": "auto", "trace": {"tier": "strong"}}, "strong"),
    (True, True, "auto", {"mode": "strong", "trace": {"tier": "flash"}}, "strong"),
    (True, True, "strong", {"mode": "flash", "trace": {"tier": "strong"}}, "global"),
    (True, False, "auto", {}, "global"),
    (False, True, "auto", {}, "strong"),
    (True, True, "strong", {}, "strong"),
    (True, False, "auto", {"mode": "strong"}, None),
    (True, False, "auto", {"mode": "auto", "trace": {"tier": "strong"}}, None),
    (True, False, "strong", {}, None),
    (True, "partial", "auto", {"mode": "strong"}, None),
    (True, True, "auto", {"model": "strong-model"}, "strong"),
])
@pytest.mark.asyncio
async def test_organizer_routes_the_latest_completed_turn(
    chat_client, db_session, monkeypatch, global_enabled, strong_enabled, session_mode, latest_metadata, expected,
):
    client, owner, _ = chat_client
    global_cfg = {"provider": "global-fixture", "api_key": "global-key", "base_url": "https://global.invalid",
                  "model": "global-model"}
    strong_cfg = {"provider": "strong-fixture", "api_key": "strong-key", "base_url": "https://strong.invalid",
                  "model": "strong-model"}
    settings = get_settings()
    monkeypatch.setattr(settings, "study_flash_model", "")
    for name, value in strong_cfg.items():
        enabled = strong_enabled is True or (strong_enabled == "partial" and name in ("model", "provider"))
        monkeypatch.setattr(settings, f"study_strong_{name}", value if enabled else "")

    async def config():
        return global_cfg if global_enabled else {}

    calls = []

    async def chat_with(self, cfg, system, user, **kwargs):
        assert kwargs["json_mode"]
        calls.append((cfg, json.loads(user)))
        return {"content": json.dumps(GUIDE)}

    monkeypatch.setattr(study_interview_questions, "get_llm_config", config)
    monkeypatch.setattr(study_notes.AgentAdapter, "chat_with", chat_with)
    sid = await new_chat(client, owner, mode=session_mode)
    await turn(db_session, sid, "older", "较早的问题", "较早的回答", reply_coach={"trace": {"tier": "flash"}})
    _, latest = await turn(db_session, sid, "latest", "最新已完成的问题", "最新的回答",
                           reply_coach={key: value for key, value in latest_metadata.items() if key != "model"},
                           reply_model=latest_metadata.get("model"))
    await turn(db_session, sid, "pending", "正在生成的问题", "部分回答",
               status="generating", reply_coach={"mode": "flash"})
    response = await client.post(f"/study/sessions/{sid}/interview-question", headers=owner)
    if expected is None:
        assert response.status_code == 502, response.text
        assert not calls
        return
    assert response.status_code == 200, response.text
    [(cfg, payload)] = calls
    expected_cfg = strong_cfg if expected == "strong" else global_cfg
    assert {key: cfg[key] for key in expected_cfg} == expected_cfg
    assert payload["turns"][-1]["assistant"] == {"id": latest.id, "text": "最新的回答"}


@pytest.mark.asyncio
async def test_late_generation_cannot_hide_a_newer_snapshot(chat_client, db_session, model):
    client, owner, _ = chat_client
    calls, outcomes = model
    sid = await new_chat(client, owner)
    path = f"/study/sessions/{sid}/interview-question"
    await turn(db_session, sid, "first", "第一轮", "最初的回答")
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()
        return {"content": json.dumps({**GUIDE, "title": "最初的题目"})}

    outcomes.append(slow)
    old_request = asyncio.create_task(client.post(path, headers=owner))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await turn(db_session, sid, "correction", "纠正第一轮", "更正的回答")
        newer = await asyncio.wait_for(client.post(path, headers=owner), timeout=5)
        assert newer.status_code == 200, newer.text
        _, pending = await turn(db_session, sid, "third", "第三轮", "正在生成的回答", status="generating")
    finally:
        release.set()
        older = await old_request

    assert older.status_code == 200, older.text
    assert older.json()["id"] > newer.json()["id"]
    assert [len(payload["turns"]) for _, payload in calls] == [1, 2]
    assert (await client.get(path, headers=owner)).json() == newer.json()
    pending.status = "completed"
    await db_session.commit()
    # The current completed transcript has no saved question yet.
    assert (await client.get(path, headers=owner)).json() == newer.json()
    bank = await client.get("/bank", headers=owner, params={"scope": "mine"})
    assert {item["id"] for item in bank.json()["items"]} == {older.json()["id"], newer.json()["id"]}


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

    unlinked, _ = await turn(db_session, sid, "latest", "切回通用场景", "新解释")

    async def quote_from_unlinked_turn():
        return {"content": json.dumps({**GUIDE, "project_context": [
            {"message_id": unlinked.id, "quote": unlinked.content},
        ]}, ensure_ascii=False)}

    outcomes.append(quote_from_unlinked_turn)
    assert (await client.post(path, headers=owner)).status_code == 502
    assert (await client.get(path, headers=owner)).json() == body
    system, _ = calls[-1]
    assert "project_id 非 null" in system and "user.id" in system and "assistant.id" in system
    assert json.loads(system.rsplit("\n", 1)[1])["properties"]["project_context"]["maxItems"] == 5

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
    other_bank_before = (await client.get("/bank", headers=other)).json()["items"]
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
    assert (await client.get("/bank", headers=other)).json()["items"] == other_bank_before
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
async def test_archive_during_generation_preserves_saved_question(chat_client, db_session, model):
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
    assert (await client.get(f"/study/sessions/{sid}", headers=owner)).status_code == 404
    bank = await client.get("/bank", headers=owner, params={"scope": "mine"})
    assert [(q["id"], q["review_only"], q["study_session_id"]) for q in bank.json()["items"]] == [
        (saved["id"], True, None),
    ]
    detail = await client.get(f"/bank/{saved['short_code']}", headers=owner)
    assert detail.status_code == 200
    assert detail.json()["answer_html"] == saved["answer_html"]
    assert detail.json()["study_session_id"] is None
    assert (await db_session.get(StudyInterviewQuestion, saved["id"])).session_id == sid


@pytest.mark.asyncio
async def test_bank_links_to_visible_owned_related_chat(chat_client, db_session, test_workspace):
    client, owner, other = chat_client
    owned_sid = await new_chat(client, owner)
    unrelated_sid = await new_chat(client, owner)
    other_sid = await new_chat(client, other)
    fixtures = [(owned_sid, unrelated_sid, owned_sid), (other_sid, owned_sid, None), (None, owned_sid, None)]
    expected = {}
    for related_sid, metadata_sid, expected_sid in fixtures:
        question = Question(
            workspace_id=test_workspace.id, short_code=uuid4().hex, topic="agent", title="Review reference",
            generation_meta={**STUDY_CHAT_REVIEW_META, "study_session_id": metadata_sid},
        )
        db_session.add(question)
        await db_session.flush()
        if related_sid is not None:
            db_session.add(StudyInterviewQuestion(
                question_id=question.id, session_id=related_sid, snapshot_hash=uuid4().hex,
            ))
        expected[question.short_code] = expected_sid
    await db_session.commit()
    # Ended chats are still navigable until archived.
    ended = await client.patch(f"/study/sessions/{owned_sid}", headers=owner, json={"status": "ended"})
    assert ended.status_code == 200

    bank = await client.get("/bank", headers=owner, params={"scope": "mine"})
    assert bank.status_code == 200
    assert {q["short_code"]: q["study_session_id"] for q in bank.json()["items"]} == expected
    for short_code, expected_sid in expected.items():
        detail = await client.get(f"/bank/{short_code}", headers=owner)
        assert detail.status_code == 200
        assert detail.json()["study_session_id"] == expected_sid
    assert (await client.get(f"/study/sessions/{owned_sid}", headers=owner)).status_code == 200
