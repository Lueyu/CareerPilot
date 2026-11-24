"""Public coach API tests with isolated PostgreSQL and scripted model responses."""

import asyncio
import json
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import public_coach as api, public_memory
from app.db import get_session
from app.i18n import LanguageMiddleware, message
from app.models import Question, Workspace
from app.services import public_coach as service

from app.models.public_library import PublicLibraryRole

pytestmark = pytest.mark.asyncio


async def unavailable(*args, **kwargs):
    raise AssertionError("Configure the model fixture before calling it")


@pytest_asyncio.fixture
async def coach(monkeypatch, db_session):
    async def identity(x_test_workspace: int = Header(), db=Depends(get_session)):
        workspace = await db.get(Workspace, x_test_workspace)
        if workspace is None:
            raise HTTPException(401)
        return workspace

    memories = {}

    async def confirmed(db, workspace_id):
        return memories.get(workspace_id, [])

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(service, "complete_text", unavailable)
    monkeypatch.setattr(service, "stream_text", unavailable)
    monkeypatch.setattr(public_memory, "confirmed_context", confirmed)

    async def session():
        async with factory() as db:
            yield db

    app = FastAPI()
    app.add_middleware(LanguageMiddleware)
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[api.current_workspace] = identity
    workspaces = [
        Workspace(slug=uuid4().hex, name="运营测试", target_role="产品运营", is_demo=False),
        Workspace(slug=uuid4().hex, name="后端测试", target_role="后端开发", is_demo=False),
    ]
    db_session.add_all(workspaces)
    await db_session.commit()
    async with (
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test/api/v1",
            headers={"X-Test-Workspace": str(workspaces[0].id)},
        ) as owner,
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test/api/v1",
            headers={"X-Test-Workspace": str(workspaces[1].id)},
        ) as other,
    ):
        yield owner, other, service, api, workspaces, memories


async def make_thread(client, **body):
    response = await client.post("/coach/threads", json=body)
    assert response.status_code == 201, response.text
    return f"/coach/threads/{response.json()['id']}"


async def submit(client, path, action, **body):
    response = await client.post(path + "/turns", json={"request_id": str(uuid4()), "action": action, **body})
    assert response.status_code == 200, response.text
    return response.json()


async def detail(client, path):
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def dump(value):
    return json.dumps(value, ensure_ascii=False)


async def test_stream_visible_before_completion_and_reload(coach, monkeypatch):
    owner, other, service, _, workspaces, _ = coach
    path = await make_thread(owner, mode="study", direction="用户增长")
    stream_started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def stream(workspace_id, request_key, messages, *, max_tokens):
        calls.append((workspace_id, request_key))
        stream_started.set()
        yield "先定义目标。"
        await release.wait()
        yield "再验证数据。"

    monkeypatch.setattr(service, "stream_text", stream)
    task = asyncio.create_task(submit(owner, path, "chat", text="怎么设计增长实验？"))
    frames = None
    try:
        await asyncio.wait_for(stream_started.wait(), timeout=5)
        running = await detail(owner, path)
        turn = running["turns"][0]
        assert (turn["status"], turn["user_text"]) == ("generating", "怎么设计增长实验？")
        turn_id = turn["id"]
        stream_path = path + f"/turns/{turn_id}/events"
        assert (await other.get(stream_path)).status_code == 404
        frames = service.turn_events(workspaces[0].id, int(path.split("/")[-1]), turn_id)
        frame = ""
        while "先定义目标。" not in frame:
            frame = await asyncio.wait_for(anext(frames), timeout=3)
        assert '"status": "generating"' in frame
        assert (await detail(owner, path))["turns"][0]["reply"] == "先定义目标。"
    finally:
        release.set()
        if frames is not None:
            await frames.aclose()
        await task
    terminal = await owner.get(stream_path)
    assert terminal.headers["x-accel-buffering"] == "no"
    assert "text/event-stream" in terminal.headers["content-type"]
    assert "先定义目标。再验证数据。" in terminal.text
    saved = (await detail(owner, path))["turns"][0]
    assert saved["status"] == "completed" and saved["reply"] == "先定义目标。再验证数据。"
    assert saved["user_origin"] == "user_statement" and saved["reply_origin"] == "model_explanation"
    assert calls == [(workspaces[0].id, f"coach:{turn_id}:1")]


async def test_revision_retry_collection_and_tenant_isolation(coach, monkeypatch, db_session):
    owner, other, service, api, workspaces, _ = coach
    path = await make_thread(owner, mode="study", direction="用户增长")
    fenced = chr(96) * 3
    outputs = iter(
        [
            "",
            f"  {fenced}JSON\r\n"
            + dump(
                {
                    "reply": "这三道题可用于练习",
                    "questions": [
                        {"title": "活动如何定目标？"},
                        {"title": "怎么区分自然用户？"},
                        {"title": "如何解释活动效果？"},
                    ],
                }
            )
            + f"\r\n{fenced}  ",
            dump(
                {
                    "reply": "已修改第二题",
                    "questions": [
                        {"title": "在实习中如何判断渠道贡献？", "replace_number": 2, "reference": "先核对 <渠道> 数据"},
                    ],
                }
            ),
        ]
    )
    calls = []

    async def complete(workspace_id, request_key, messages, *, max_tokens):
        assert max_tokens >= 4000
        calls.append((workspace_id, request_key, messages))
        return next(outputs)

    monkeypatch.setattr(service, "complete_text", complete)
    request = {"request_id": str(uuid4()), "action": "generate", "text": "我想练三道活动复盘题"}
    initial = await owner.post(path + "/turns", json=request)
    assert initial.status_code == 200, initial.text
    turn_id = initial.json()["id"]
    failed = (await detail(owner, path))["turns"][0]
    assert failed["status"] == "failed" and "没有返回正文" in failed["error"]
    assert (await owner.post(path + "/turns", json=request)).json()["id"] == turn_id
    retry = path + f"/turns/{turn_id}/retry"
    results = await asyncio.gather(*[owner.post(retry, json={"attempt": 1}) for _ in range(2)])
    assert all(response.status_code == 200 for response in results)
    assert len(calls) == 2
    assert (await owner.post(path + "/turns", json={**request, "text": "不同消息"})).status_code == 409

    first = (await detail(owner, path))["candidates"]
    assert [item["number"] for item in first] == [1, 2, 3]
    await submit(owner, path, "generate", text="第二题换成实习场景", target_number=2)
    revised = await detail(owner, path)
    current = [item for item in revised["candidates"] if not item["superseded"]]
    assert [item["number"] for item in current] == [1, 2, 3]
    assert current[0]["id"] == first[0]["id"] and current[2]["id"] == first[2]["id"]
    assert current[1]["title"] == "在实习中如何判断渠道贡献？"
    assert current[1]["id"] != first[1]["id"]
    prompt = json.loads(calls[-1][2][-1]["content"])
    assert [item["题号"] for item in prompt["当前候选题"]] == [1, 2, 3]
    assert prompt["明确指定的修改题号"] == 2
    collect = path + f"/candidates/{current[1]['id']}/collect"
    assert (await other.post(collect)).status_code == 404
    responses = await asyncio.gather(owner.post(collect), owner.post(collect))
    assert all(response.status_code == 200 for response in responses)
    question_id = responses[0].json()["question_id"]
    assert responses[1].json()["question_id"] == question_id
    q = await db_session.get(Question, question_id)
    assert (q.workspace_id, q.role, q.topic) == (workspaces[0].id, "产品运营", "用户增长")
    assert q.generation_meta["assessment_use"] == "PRACTICE_ONLY"
    assert q.generation_meta["library_answer"] == "先核对 <渠道> 数据"
    assert q.answer_html == "<p>先核对 &lt;渠道&gt; 数据</p>"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Question)
            .where(
                Question.workspace_id == workspaces[0].id,
            )
        )
        == 1
    )
    assert (await owner.post(path + f"/candidates/{first[1]['id']}/collect")).status_code == 409
    assert (
        await other.post("/coach/threads", json={"mode": "interview", "question_id": question_id})
    ).status_code == 404
    assert (await other.get(path)).status_code == 404
    assert (await other.get("/coach/threads?mode=study")).json()["threads"] == []
    assert (await other.post(path + "/turns", json=request)).status_code == 404
    assert (await other.post(retry, json={"attempt": 1})).status_code == 404
    assert await api.export_coach(db_session, workspaces[1].id) == {"threads": []}
    exported = (await api.export_coach(db_session, workspaces[0].id))["threads"][0]
    assert len(exported["candidates"]) == 4
    assert len(exported["turns"]) == 2


async def test_interview_preserves_hints_original_answers_feedback_and_memories(coach, monkeypatch, db_session):
    owner, other, service, api, workspaces, memories = coach
    role = "产品运营"
    q = Question(
        short_code="TEST-" + uuid4().hex[:10],
        workspace_id=workspaces[0].id,
        role=role,
        topic="用户增长",
        title="如何复盘拉新活动？",
        answer_html="<p>私有参考内容</p>",
    )
    db_session.add(q)
    await db_session.commit()
    assert (await owner.post("/coach/threads", json={"mode": "interview"})).status_code == 422
    path = await make_thread(owner, mode="interview", question_id=q.id)
    calls = []
    memories[workspaces[0].id] = ["我负责社群拉新"]
    memories[workspaces[1].id] = ["另一个测试用户的经历"]

    async def stream(workspace_id, key, messages, *, max_tokens):
        calls.append(json.loads(messages[-1]["content"]))
        yield "先确认活动目标对应的指标。"

    outputs = iter(
        [
            {"quote": "并未作答的引文", "question": "无依据追问"},
            {"quote": "按渠道区分", "question": "如何核对渠道贡献？"},
            {
                "covered": [
                    {"answer": "initial", "quote": "对比上一周", "comment": "错误来源"},
                    *[{"answer": "initial", "quote": quote, "comment": quote}
                      for quote in ("按渠道区分", "自然流量", "活动流量")],
                    *[{"answer": "followup", "quote": quote, "comment": quote}
                      for quote in ("对比上一周", "同期指标", "上一周的同期指标")],
                ],
                "gaps": ["缺少转化率数据"],
                "next_steps": ["说明基线选择依据"],
            },
            {"quote": "设定对照组", "question": "对照组如何分配？"},
        ]
    )

    async def complete(workspace_id, key, messages, *, max_tokens):
        assert all("私有参考内容" not in message["content"] for message in messages)
        calls.append(json.loads(messages[-1]["content"]))
        return dump(next(outputs))

    monkeypatch.setattr(service, "stream_text", stream)
    monkeypatch.setattr(service, "complete_text", complete)
    assert set((await detail(owner, path))["actions"]) == {"hint", "answer"}
    invalid = await owner.post(path + "/turns", json={"request_id": str(uuid4()), "action": "finish"})
    assert invalid.status_code == 409
    await submit(owner, path, "hint")
    answer = await submit(owner, path, "answer", text="按渠道区分自然流量和活动流量")
    after_answer = await detail(owner, path)
    failed = after_answer["turns"][-1]
    assert (failed["status"], failed["assisted"], failed["user_origin"]) == ("failed", True, "assisted_answer")
    assert failed["user_text"] == "按渠道区分自然流量和活动流量"
    assert after_answer["actions"] == []
    assert (await owner.post(path + f"/turns/{answer['id']}/retry", json={"attempt": 1})).status_code == 200
    assert (await detail(owner, path))["turns"][-1]["result"]["quote"] == "按渠道区分"
    await submit(owner, path, "followup_answer", text="对比上一周的同期指标")
    await submit(owner, path, "finish")
    finished = await detail(owner, path)
    assert finished["actions"] == [] and finished["ability_status"] == "unknown"
    assert finished["turns"][2]["assisted"] is False
    assert "score" not in finished["turns"][3]["result"]
    assert [point["quote"] for point in finished["turns"][3]["result"]["covered"]] == [
        "按渠道区分", "自然流量", "活动流量", "对比上一周", "同期指标",
    ]
    assert all(call["用户确认的经历"] == ["我负责社群拉新"] for call in calls)
    assert (await other.get("/coach/threads?mode=interview")).json()["threads"] == []

    memories[workspaces[0].id] = []
    next_path = await make_thread(owner, mode="interview", question_id=q.id)
    await submit(owner, next_path, "answer", text="设定对照组评估差异")
    assert calls[-1]["用户确认的经历"] == []
    again = (await detail(owner, next_path))["turns"][0]
    assert again["user_origin"] == "independent_answer" and again["assisted"] is False
    export = await api.export_coach(db_session, workspaces[0].id)
    assert export["threads"][0]["turns"] == finished["turns"]


async def test_quota_recovery_keeps_the_submitted_message(coach, monkeypatch):
    owner, _, service, _, _, _ = coach
    path = await make_thread(owner, mode="study", direction="用户增长")
    recovery = "模型额度已用完，预计北京时间 2026-11-24 00:00 恢复。"

    async def complete(*args, **kwargs):
        raise service.PublicModelError(429, recovery, headers={"Retry-After": "600"})

    monkeypatch.setattr(service, "complete_text", complete)
    await submit(owner, path, "generate", text="出两道用户增长题")
    saved = (await detail(owner, path))["turns"][0]
    assert (saved["status"], saved["error"], saved["user_text"]) == ("failed", recovery, "出两道用户增长题")


async def test_retry_reports_gateway_failure_in_the_admitted_language(coach, monkeypatch):
    owner, _, service, _, _, _ = coach
    owner.headers["Accept-Language"] = "en-US"
    path = await make_thread(owner, mode="study")

    async def unavailable_model(*args, **kwargs):
        raise service.PublicModelError(503, message("errors.the_model_service_is_not_configured_yet_try_again"))

    monkeypatch.setattr(service, "complete_text", unavailable_model)
    await submit(owner, path, "generate", text="请保留我的问题")
    saved = (await detail(owner, path))["turns"][0]
    owner.headers["Accept-Language"] = "zh-CN"
    assert (await owner.post(path + f"/turns/{saved['id']}/retry", json={"attempt": 1})).is_success
    retried = (await detail(owner, path))["turns"][0]
    assert retried["attempt"] == 2 and retried["user_text"] == "请保留我的问题"
    assert retried["error"] == saved["error"] == message("errors.the_model_service_is_not_configured_yet_try_again", "en")


async def test_questionless_coach_preserves_the_selected_library_direction(coach, db_session):
    owner, _, _, _, workspaces, _ = coach
    db_session.add_all(
        [
            PublicLibraryRole(workspace_id=workspaces[0].id, name="产品运营", selected_topic="用户增长", directions=[]),
            PublicLibraryRole(workspace_id=workspaces[0].id, name="旧岗位", selected_topic="内容审核", directions=[]),
            PublicLibraryRole(workspace_id=workspaces[1].id, name="后端开发", selected_topic="接口设计", directions=[]),
        ]
    )
    await db_session.commit()
    path = await make_thread(owner, mode="study")
    row = await detail(owner, path)
    assert (row["role"], row["direction"]) == ("产品运营", "用户增长")


async def test_public_note_snapshot_edit_history_and_isolation(coach, monkeypatch, db_session):
    owner, other, service, api, workspaces, _ = coach
    thread_path = await make_thread(owner, mode="study", direction="学习笔记")

    async def stream(*args, **kwargs):
        yield "笔记原文"

    monkeypatch.setattr(service, "stream_text", stream)
    turn = await submit(owner, thread_path, "chat", text="请解释观察指标")
    save_path = f"{thread_path}/turns/{turn['id']}/note"
    assert (await other.post(save_path)).status_code == 404
    snapshot = await owner.post(save_path)
    assert snapshot.status_code == 200, snapshot.text
    assert (await owner.post(save_path)).json() == snapshot.json()
    path = f"/coach/notes/{snapshot.json()['id']}"
    first = await detail(owner, path)
    assert first["body"] == "笔记原文"
    assert (await other.get("/coach/notes")).json() == {"items": []}
    assert (await other.get(path)).status_code == 404
    version = path + f"/versions/{first['version_id']}"
    assert (await other.get(version)).status_code == 404
    edit = {"expected_version_id": first["version_id"], "title": "我的版本", "body": "我自己的总结"}
    assert (await other.patch(path, json=edit)).status_code == 404
    response = await owner.patch(path, json=edit)
    assert response.status_code == 200, response.text
    assert (await owner.patch(path, json=edit)).status_code == 409
    assert (await detail(owner, version))["body"] == "笔记原文"
    assert len((await detail(owner, "/coach/notes?q=我的版本"))["items"]) == 1

    exported = await api.export_coach(db_session, workspaces[0].id)
    assert [version["body"] for version in exported["notes"][0]["versions"]] == ["笔记原文", "我自己的总结"]
    assert "notes" not in await api.export_coach(db_session, workspaces[1].id)
