"""Revision, ownership, model routing, budget and cancellation regression checks."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_llm_stream_transport import CONFIG, DONE, Body, frame, install  # noqa: F401 -- 流式 fixture 经导入注册到本模块供 pytest 发现

from app.api.v1 import study_chat as api
from app.config import get_settings
from app.models.project import Project
from app.models.study_chat import StudyMessage
from app.services import study_chat as engine


@pytest.mark.asyncio
async def test_revision_excludes_old_branch_and_project_switch_cannot_revisit(db_session, test_workspace):
    db, ws = db_session, test_workspace
    project = Project(workspace_id=ws.id, name="CoachProject", owner="", repo="", repo_url="")
    db.add(project)
    await db.commit()
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    async def turn(content, **kw):
        ticket = await api.send(sid, api.NewMessage(content=content, request_id=uuid4().hex, **kw), BackgroundTasks(),db,ws)
        await db.execute(update(StudyMessage).where(StudyMessage.id == ticket["assistant_message_id"]).values(
            status="completed",content="answer "+content))
        await db.commit()
        return ticket
    original = await turn("旧版")
    revised = await turn("新版", revision_of=original["user_message_id"])
    next_turn = await turn("追问")
    reply = await db.get(StudyMessage, next_turn["assistant_message_id"])
    history = await engine.branch_history(db, reply)
    assert [m.content for m in history] == ["追问","answer 新版","新版"]
    assert (await db.get(StudyMessage, original["user_message_id"])).content == "旧版"
    grounded = await turn("结合 CoachProject 项目讲解",action="project")
    assert (await db.get(StudyMessage, grounded["user_message_id"])).project_id == project.id
    with pytest.raises(HTTPException,match="404"):
        await turn("跨版本", parent_message_id=revised["assistant_message_id"])
    await api.archive(sid, db, ws)
    assert not any(x["id"]==sid for x in (await api.history(db, ws, 0, 100))["items"])
    assert await db.get(StudyMessage, original["assistant_message_id"])


@pytest.mark.asyncio
async def test_manual_strong_unavailable_and_budget_do_not_fabricate_results(monkeypatch, db_session, test_workspace):
    db, ws = db_session, test_workspace
    monkeypatch.setattr(engine, "SessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    async def config():
        return CONFIG
    monkeypatch.setattr(engine, "get_llm_config", config)
    monkeypatch.setattr(get_settings(), "study_strong_model", "")
    sid = (await api.create(api.StartSession(),db,ws))["id"]
    async def ask(mode):
        q=BackgroundTasks()
        ticket=await api.send(sid, api.NewMessage(request_id=uuid4().hex, content="解释",mode=mode),q,db,ws)
        await q()
        last=(await api.detail(sid,db,ws))["messages"][-1]
        assert last["id"] == ticket["assistant_message_id"] and last["status"]=="failed" and not last["trace"]["calls"]
        return last["trace"]["error_kind"]
    assert await ask("strong") == "unavailable"
    monkeypatch.setattr(get_settings(), "study_output_budget", 0)
    assert await ask("flash") == "budget"


@pytest.mark.asyncio
async def test_structured_repair_records_each_generation_and_caps_calls(monkeypatch, db_session, test_workspace):
    from app.services.llm import AgentAdapter
    db, ws = db_session, test_workspace
    monkeypatch.setattr(engine, "SessionLocal", async_sessionmaker(db.bind, expire_on_commit=False))
    async def config():
        return CONFIG
    monkeypatch.setattr(engine, "get_llm_config", config)
    monkeypatch.setattr(get_settings(), "study_strong_model", "fixture-strong")
    responses = ["初稿，缺少图形", '静态解释\n```study-blocks\n[{"type":"demo","preset":"retry","title":"重试","summary":"假设"}]\n```']
    async def respond(self, cfg, system, user, **kw):
        return {"content": responses.pop(0)}
    monkeypatch.setattr(AgentAdapter, "chat_with", respond)
    sid = (await api.create(api.StartSession(), db, ws))["id"]
    pending = BackgroundTasks()
    await api.send(sid, api.NewMessage(content="演示重试", action="demo", request_id="demo"), pending,db,ws)
    await pending()
    m = (await api.detail(sid, db, ws))["messages"][-1]
    assert m["status"] == "completed" and m["blocks"][0]["preset"] == "retry"
    assert len(m["trace"]["calls"]) == 2
    assert m["trace"]["calls"][0]["saved_output"] == "初稿，缺少图形"
    assert "初稿" not in m["content"]


def test_pinned_remote_snapshot_does_not_confuse_changed_or_uncited_code():
    import hashlib

    from app.services.study_project_evidence import snapshot_excerpts
    value = "def release_job():\n    return True"
    source = {"content":value,"path":"worker.py","content_sha256":hashlib.sha256(value.encode()).hexdigest(),"source_revision":"a"*40}
    run = SimpleNamespace(id=3, generation=1,source_revision="a"*40,snapshot={"files":[source]})
    project = SimpleNamespace(id=5,generation=1,last_seen_sha=run.source_revision)
    verified = snapshot_excerpts(run, project, ["release_job"])
    assert verified[0]["symbol"]=="release_job" and verified[0]["revision"] == run.source_revision
    source["content"] += " corrected"
    assert not snapshot_excerpts(run, project, ["release_job"])
    project.generation = 2
    assert not snapshot_excerpts(run, project, ["release_job"])


def test_long_thread_budget_preserves_latest_question_and_older_source_ids():
    from app.services.study_chat import fit_request
    material = {"turns":[{"id":i,"speaker":"user","text":"重复的背景"*600} for i in range(10)]}
    fitted = fit_request(material,"这一步为什么重复？","中文教学",16000)
    assert fitted.endswith("这一步为什么重复？")
    assert "earlier_excerpts" in fitted and '"id": 9' in fitted
    assert len(fitted.encode()) <= 16000


def test_short_search_terms_are_identifier_words():
    from app.services.study_project_evidence import term_hit
    assert term_hit("rag", "get_rag_context")
    assert not term_hit("rag", "coverage_for_scan")
