"""聊天引用资料库：提问指向资料库时，题库题目/面经套题/学习资料按检索词进入对话来源。"""
import json
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select, update

from app.api.v1 import study_chat as api
from app.i18n import using_language
from app.models import ImportSession, Question, Skill, StudyMessage, StudyNote, StudyNoteVersion, StudySession, Workspace
from app.services import study_chat as worker


async def ensure_rag_skill(session):
    skill = await session.scalar(select(Skill).where(Skill.key == "rag"))
    if skill is None:
        skill = Skill(key="rag", name="RAG")
        session.add(skill)
        await session.commit()
    return skill


def kinds_of(sources):
    return [source["kind"] for source in sources]


@pytest.mark.asyncio
async def test_library_mention_attaches_bank_sets_and_notes(db_session, test_workspace, track_public_question):
    db, ws = db_session, test_workspace
    skill = await ensure_rag_skill(db)
    own = Question(workspace_id=ws.id, short_code="lib-" + uuid4().hex[:8], title="RAG 高并发检索架构设计",
                   qtype="open", topic="rag", skill_id=skill.id, difficulty=4, tags=["RAG", "检索"])
    public = track_public_question(Question(
        workspace_id=None, short_code="pub-" + uuid4().hex[:8], title="混合检索与重排在 RAG 中的作用",
        qtype="mcq", topic="rag", difficulty=3))
    db.add_all([own, public])
    db.add(ImportSession(workspace_id=ws.id, short_code="IM-" + uuid4().hex[:8], status=ImportSession.STATUS_APPROVED,
                         meta={"interview_sets": [{
                             "id": uuid4().hex, "title": "字节跳动 RAG 面经", "project_name": "CareerPilot",
                             "questions": [{"id": 9001, "title": "如何优化向量检索延迟"}],
                         }]}))
    session_row = StudySession(workspace_id=ws.id, title="RAG 复习", status="active",
                               context={"topic": "rag", "skill_ids": [], "sources": []})
    db.add(session_row)
    await db.flush()
    note = StudyNote(workspace_id=ws.id, origin_session_id=session_row.id, title="RAG 复习笔记")
    db.add(note)
    await db.flush()
    version = StudyNoteVersion(note_id=note.id, title=note.title, author="model",
                               body="混合检索：BM25 与向量召回并行，再统一重排。", sources=[], session_ids=[], coverage=[])
    db.add(version)
    await db.flush()
    note.current_version_id = version.id
    await db.commit()

    sid = (await api.create(api.StartSession(), db, ws))["id"]
    body = api.NewMessage(request_id="library-list", content="看下资料库里都有哪些和rag相关的面试题")
    turn = await api.send(sid, body, BackgroundTasks(), db, ws)
    sources = (await api.detail(sid, db, ws))["messages"][1]["sources"]
    summaries = [source for source in sources if source["kind"] == "library_summary"]
    assert len(summaries) == 1
    assert {kind for kind in kinds_of(sources)} >= {"library_question", "library_interview_set", "library_note"}
    questions = [source for source in sources if source["kind"] == "library_question"]
    assert any("RAG 高并发检索架构设计" in source["text"] for source in questions)
    assert any("混合检索与重排" in source["text"] for source in questions)
    assert any("字节跳动 RAG 面经" in source["text"] for source in sources if source["kind"] == "library_interview_set")
    assert any("RAG 复习笔记" in source["text"] for source in sources if source["kind"] == "library_note")
    summary = json.loads(summaries[0]["text"])
    assert summary["题库命中"] >= 2 and summary["面经套题命中"] == 1 and summary["学习资料命中"] == 1

    # 生成阶段的请求 materials 携带同一批资料库来源。
    loaded = await worker._load_turn(db, turn["assistant_message_id"], 1, {}, {})
    assert loaded is not None
    worker._compose_request(loaded, {}, "zh-CN")
    materials = loaded["request_data"]["materials"]
    assert "library_summary" in [item["kind"] for item in materials]

    # 第一轮回复已持久化来源；将其标记完成后继续第二轮普通提问。
    await db.execute(update(StudyMessage).where(StudyMessage.id == turn["assistant_message_id"])
                     .values(status="completed", content="已检索资料库。"))
    await db.commit()

    # 不指向资料库的普通提问不注入资料库材料。
    await api.send(sid, api.NewMessage(request_id="plain-why", content="为什么向量检索会丢召回？"),
                   BackgroundTasks(), db, ws)
    plain = (await api.detail(sid, db, ws))["messages"][-1]["sources"]
    assert all(not kind.startswith("library_") for kind in kinds_of(plain))


@pytest.mark.asyncio
async def test_library_mention_without_hits_still_reports_empty_result(db_session, test_workspace):
    db, ws = db_session, test_workspace
    await ensure_rag_skill(db)
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    body = api.NewMessage(request_id="library-miss", content="看看资料库里都有哪些和k8s调度相关的面试题")
    await api.send(sid, body, BackgroundTasks(), db, ws)
    sources = (await api.detail(sid, db, ws))["messages"][1]["sources"]
    summaries = [source for source in sources if source["kind"] == "library_summary"]
    assert len(summaries) == 1 and kinds_of(sources) == ["library_summary"]
    summary = json.loads(summaries[0]["text"])
    assert summary["题库命中"] == 0 and "提示" in summary

@pytest.mark.asyncio
async def test_inventory_returns_the_matching_questions_in_the_users_language(db_session, test_workspace):
    db, ws = db_session, test_workspace
    skill = await ensure_rag_skill(db)
    other = Workspace(slug="library-hidden-" + uuid4().hex, name="Hidden fixture", is_demo=False)
    db.add(other)
    await db.flush()
    try:
        for index in range(30):
            db.add(Question(workspace_id=ws.id, short_code=f"rag-{uuid4().hex[:8]}", title=f"检索面试题 {index}",
                            qtype="open", topic="rag", difficulty=3, skill_id=skill.id))
        db.add(Question(workspace_id=other.id, short_code="secret-" + uuid4().hex[:8], title="不可见的 RAG 题",
                        qtype="open", topic="rag", difficulty=3, skill_id=skill.id))
        db.add(Question(workspace_id=ws.id, short_code="unrelated-" + uuid4().hex[:8], title="项目里都用了哪些并发方案？",
                        qtype="open", topic="concurrency", difficulty=3))
        await db.commit()

        sid = (await api.create(api.StartSession(), db, ws))["id"]
        tasks = BackgroundTasks()
        body = api.NewMessage(request_id="full-library", agent_role="coach",
                              content="看下资料库里都有哪些和rag相关的面试题")
        with using_language("en"):
            ticket = await api.send(sid, body, tasks, db, ws)
        assert ticket["status"] == "completed"
        assert not tasks.tasks
        saved = (await api.detail(sid, db, ws))["messages"][-1]
        inventory = saved["library_inventory"]
        assert inventory["total"] == 30
        assert [item["title"] for item in inventory["items"]] == [f"检索面试题 {i}" for i in range(30)]
        assert len({item["id"] for item in inventory["items"]}) == 30
        assert "30 道" in saved["content"]
        assert chr(96) * 3 not in saved["content"]
        assert await api.send(sid, body, BackgroundTasks(), db, ws) == ticket
    finally:
        await db.delete(other)
        await db.commit()


@pytest.mark.parametrize("prompt", ["看看我的资料库里有什么", "show me question bank."])
def test_library_browser_has_no_spurious_topic(prompt):
    from app.services.study_chat_context import library_terms

    assert library_terms(prompt, [], set()) == []



@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", [
    "列出题库里的 RAG 面试题，并解释每道题的考点",
    "请解释 RAG，并列出题库里的 RAG 面试题",
    "列出题库里的 RAG 面试题，解释每道题的考点",
    "List RAG questions from the question bank, briefly explain each question",
    "Explain RAG and list RAG questions from the question bank",
    "Can you explain RAG and list RAG questions from the question bank?",
    "List RAG questions from the question bank. Explain each question.",
])
async def test_inventory_and_explanation_request_still_queues_a_model_reply(db_session, test_workspace, prompt):
    db, ws = db_session, test_workspace
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    tasks = BackgroundTasks()
    body = api.NewMessage(request_id="mixed-library", agent_role="coach", content=prompt)
    ticket = await api.send(sid, body, tasks, db, ws)
    assert ticket["status"] == "generating"
    assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_inventory_http_language_switch_overrides_prompt_and_preserves_history(db_session, test_workspace, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.i18n import LanguageMiddleware

    db, ws = db_session, test_workspace
    app = FastAPI()
    app.add_middleware(LanguageMiddleware)
    app.include_router(api.router, prefix="/api/v1")

    async def session_dependency():
        yield db

    async def owner_dependency():
        return ws

    async def no_provider(*args, **kwargs):
        pytest.fail("A question inventory must not call a provider")

    app.dependency_overrides[api.get_session] = session_dependency
    app.dependency_overrides[api.get_workspace] = owner_dependency
    monkeypatch.setattr(api, "generate_reply", no_provider)
    title = "原始 inventory-language-fixture 题目"
    db.add(Question(workspace_id=ws.id, short_code="lang-" + uuid4().hex[:8], title=title,
                    qtype="open", topic="inventory-language-fixture", difficulty=3))
    await db.commit()
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    path = f"/api/v1/study/sessions/{sid}"
    chinese = "列出题库里 inventory-language-fixture 的题目"
    english = "List inventory-language-fixture questions from my question bank"
    saved_contents = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        for language, prompt in [("en", chinese), ("zh-CN", english), ("en", english), ("zh-CN", chinese)]:
            body = {"request_id": uuid4().hex, "agent_role": "collect", "content": prompt}
            response = await http.post(path + "/messages", json=body, headers={"Accept-Language": language})
            assert response.status_code == 202, response.text
            assert response.headers["Content-Language"] == language
            assert "Accept-Language" in response.headers["Vary"]
            assert response.json()["status"] == "completed"
            detail = (await http.get(path)).raise_for_status().json()
            assert detail["messages"][-2]["content"] == prompt
            replies = [message for message in detail["messages"] if message["role"] == "assistant"]
            assert [reply["content"] for reply in replies[:-1]] == saved_contents
            reply = replies[-1]
            assert reply["library_inventory"]["language"] == language
            assert [item["title"] for item in reply["library_inventory"]["items"]] == [title]
            assert ("question" in reply["content"].lower()) == (language == "en")
            stored = await db.get(StudyMessage, reply["id"])
            assert stored.coach["response_language"] == language
            saved_contents.append(reply["content"])
            replay = await http.post(path + "/messages", json=body,
                                     headers={"Accept-Language": "zh-CN" if language == "en" else "en"})
            assert replay.raise_for_status().json() == response.json()
        for prompt, inferred in [(english, "en"), (chinese, "zh-CN")]:
            response = await http.post(path + "/messages", json={
                "request_id": uuid4().hex, "agent_role": "collect", "content": prompt,
            })
            assert response.raise_for_status().json()["status"] == "completed"
            detail = (await http.get(path)).raise_for_status().json()
            assert detail["messages"][-1]["library_inventory"]["language"] == inferred
