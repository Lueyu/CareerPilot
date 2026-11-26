"""聊天引用资料库：提问指向资料库时，题库题目/面经套题/学习资料按检索词进入对话来源。"""
import json
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select, update

from app.api.v1 import study_chat as api
from app.models import ImportSession, Question, Skill, StudyMessage, StudyNote, StudyNoteVersion, StudySession
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
