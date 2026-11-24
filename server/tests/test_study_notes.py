from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import study_notes as api
from app.i18n import message as localized_message, using_language
from app.models import Evidence, Workspace
from app.models.study_chat import StudyMessage, StudySession
from app.models.study_notes import StudyCardEvent, StudyNoteVersion
from app.services import study_notes as service


def message(sid, role, text, status="completed"):
    return StudyMessage(session_id=sid, role=role, request_id=uuid4().hex, content=text, status=status,
                        action="explain")


async def conversation(db, ws):
    chat = StudySession(workspace_id=ws.id, title="Pod 探针")
    db.add(chat)
    await db.flush()
    question = message(chat.id, "user", "就绪探针失败会重启容器吗？")
    answer = message(chat.id, "assistant", "就绪失败会摘除服务流量。")
    db.add_all([question, answer])
    await db.commit()
    return chat, question, answer


async def generate(snapshot):
    m = snapshot[-1]
    return {"title": "探针", "model": "controlled-fixture", "body": m["text"],
            "sources": [{"session_id": m["session_id"], "message_id": m["id"],
                         "role": m["role"], "quote": m["text"], "materials": m["materials"]}]}


@pytest.mark.asyncio
async def test_snapshot_restart_and_racing_manual_edit(db_session, test_workspace, monkeypatch):
    db, ws = db_session, test_workspace
    chat, question, answer = await conversation(db, ws)
    pending = message(chat.id, "assistant", "", "generating")
    db.add(pending)
    await db.commit()
    job = await service.enqueue(db, ws.id, chat.id, "first", None)
    assert (await service.enqueue(db, ws.id, chat.id, "another-tab", None)).id == job.id
    assert [m["id"] for m in job.snapshot] == [question.id, answer.id]
    pending.content, pending.status = "晚到的回复", "completed"
    await db.commit()
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(service, "generate_note", generate)
    assert await service.process_next_note_job(factory)
    await db.refresh(job)
    assert job.status == "SUCCEEDED"
    view = await api.get_note(job.note_id, ws, db)
    assert view["body"] == answer.content
    assert pending.id not in view["message_ids"]

    newer = await service.enqueue(db, ws.id, chat.id, "update", job.note_id)
    await api.edit_note(job.note_id, api.EditIn(expected_version_id=view["version_id"],
                        title="我的笔记", body="手写补充"), ws, db)
    assert await service.process_next_note_job(factory)
    await db.refresh(newer)
    assert newer.conflict and newer.version_id != view["version_id"]
    assert (await api.get_note(job.note_id, ws, db))["body"] == "手写补充"
    draft = await api.get_version(job.note_id, newer.version_id, ws, db)
    assert draft["body"] == pending.content
    assert draft["sources"][0]["message_id"] == pending.id
    assert (await service.enqueue(db, ws.id, chat.id, "another-tab", None)).id == job.id
    assert not await service.finish_job(db, newer.id, 0, await generate(newer.snapshot))
    assert await db.scalar(select(func.count()).select_from(StudyNoteVersion)
                           .where(StudyNoteVersion.note_id == job.note_id)) == 3
    assert await db.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == ws.id)) == 0


@pytest.mark.asyncio
async def test_failed_job_keeps_snapshot_retry_fences_and_edits(db_session, test_workspace, monkeypatch):
    db, ws = db_session, test_workspace
    chat, _, _ = await conversation(db, ws)
    job = await service.enqueue(db, ws.id, chat.id, "job", None)
    job.status, job.generation, job.lease_until = "RUNNING", 1, service.utcnow() - timedelta(seconds=1)
    await db.commit()
    assert api.job_view(job)["status"] == "FAILED"
    await api.retry_job(job.id, ws, db)
    assert not await service.finish_job(db, job.id, 1, await generate(job.snapshot))
    await db.commit()

    async def broken(snapshot):
        raise service.GenerationError("模拟超时")

    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(service, "generate_note", broken)
    assert await service.process_next_note_job(factory)
    await db.refresh(job)
    assert job.status == "FAILED" and not job.version_id
    assert not (await api.list_notes("", ws, db))["items"]
    await api.retry_job(job.id, ws, db)
    monkeypatch.setattr(service, "generate_note", generate)
    assert await service.process_next_note_job(factory)
    await db.refresh(job)
    assert job.status == "SUCCEEDED"
    assert not await service.process_next_note_job(factory)


@pytest.mark.asyncio
@pytest.mark.parametrize(("admitted", "current", "composite"), [
    ("en", "zh-CN", True),
    ("zh-CN", "en", True),
    ("en", "zh-CN", False),
])
async def test_cards_use_admitted_language_only_for_composite_action(
    db_session, test_workspace, monkeypatch, admitted, current, composite,
):
    db, ws = db_session, test_workspace
    chat, question, _ = await conversation(db, ws)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    note_prompts = []
    card_prompts = []
    expected = admitted if composite else current

    async def config():
        return {}

    async def model(prompt, payload, schema, *, config):
        if schema is service.NoteOutput:
            note_prompts.append(prompt)
            assert localized_message("language", admitted) in prompt
            original = payload[0]
            assert original["text"] == question.content
            if len(note_prompts) == 1:
                raise service.GenerationError("Synthetic note failure")
            return schema(title="Probes", points=[service.NotePoint(
                kind="question", text="Readiness", citations=[
                    service.Citation(message_id=original["id"], quote=original["text"]),
                ],
            )]), "controlled-fixture"
        card_prompts.append(prompt)
        assert localized_message("language", expected) in prompt
        if len(card_prompts) == 1:
            raise service.GenerationError("Synthetic provider failure")
        return schema(cards=[service.CardOutput(
            question="Readiness?", answer="Service traffic", knowledge_point="Readiness",
        )]), "controlled-fixture"

    monkeypatch.setattr(service, "get_llm_config", config)
    monkeypatch.setattr(service, "read_json", model)
    admission = api.JobIn(request_id="organize")
    with using_language(admitted):
        queued = await api.enqueue_note(chat.id, admission, ws, db)

    with using_language(current):
        # Reload the durable job from a new session after switching languages.
        assert await service.process_next_note_job(factory)
        async with factory() as client:
            original_job = await api.enqueue_note(chat.id, admission, ws, client)
            assert original_job["id"] == queued["id"] and original_job["status"] == "FAILED"
            assert (await api.retry_job(original_job["id"], ws, client))["status"] == "QUEUED"
        assert await service.process_next_note_job(factory)
        async with factory() as client:
            job = (await api.session_jobs(chat.id, ws, client))["items"][0]
            assert job["id"] == queued["id"] and job["status"] == "SUCCEEDED"
            payload = {"version_id": job["version_id"]}
            if composite:
                payload["note_job_id"] = job["id"]
            with pytest.raises(HTTPException) as failed:
                await api.create_cards(job["note_id"], api.CardGenIn(**payload), ws, client)
            assert failed.value.status_code == 502

        async with factory() as client:
            request = api.CardGenIn(**payload)
            result = await api.create_cards(job["note_id"], request, ws, client)
            card = result["items"][0]
            await api.edit_card(card["id"], api.CardEditIn(question="My saved edit"), ws, client)
            replay = await api.create_cards(job["note_id"], request, ws, client)
            assert [(c["id"], c["question"]) for c in replay["items"]] == [(card["id"], "My saved edit")]
            note = await api.get_note(job["note_id"], ws, client)
            assert len(note["versions"]) == 1
            assert note["sources"][0]["quote"] == question.content
            assert (await client.get(StudyMessage, question.id)).content == question.content
    assert len(note_prompts) == len(card_prompts) == 2


@pytest.mark.asyncio
async def test_card_answer_is_recorded_separately_from_self_rating_and_evidence(db_session, test_workspace, monkeypatch):
    db, ws = db_session, test_workspace
    chat, _, _ = await conversation(db, ws)
    job = await service.enqueue(db, ws.id, chat.id, "job", None)
    monkeypatch.setattr(service, "generate_note", generate)
    assert await service.process_next_note_job(async_sessionmaker(db.bind, expire_on_commit=False))
    await db.refresh(job)

    async def cards(body):
        return [service.CardOutput(question="哪个探针？", answer="readiness", knowledge_point="健康检查")]

    monkeypatch.setattr(service, "generate_cards", cards)
    request = api.CardGenIn(version_id=job.version_id)
    card = (await api.create_cards(job.note_id, request, ws, db))["items"][0]
    assert "answer" not in card
    assert (await api.create_cards(job.note_id, request, ws, db))["items"][0]["id"] == card["id"]
    assert (await api.reveal(card["id"], ws, db))["answer"] == "readiness"
    await api.edit_card(card["id"], api.CardEditIn(status="ACTIVE", question="换个问法"), ws, db)
    assert (await api.list_cards("DUE", ws, db))["items"][0]["question"] == "换个问法"
    rating = api.ReviewIn(request_id="rated", rating="good")
    next_date = (await api.review_card(card["id"], rating, ws, db))["next_review_at"]
    assert (await api.review_card(card["id"], rating, ws, db))["next_review_at"] == next_date
    assert not (await api.list_cards("DUE", ws, db))["items"]
    events = (await db.scalars(select(StudyCardEvent.kind).where(StudyCardEvent.card_id == card["id"])
                               .order_by(StudyCardEvent.id))).all()
    assert events == ["REVEAL", "SELF_REVIEW"]
    assert await db.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == ws.id)) == 0

    other = Workspace(slug="test-" + uuid4().hex, name="隔离")
    db.add(other)
    await db.commit()
    try:
        for call in [api.get_note(job.note_id, other, db), api.get_version(job.note_id, job.version_id, other, db),
                     api.get_job(job.id, other, db), api.retry_job(job.id, other, db),
                     api.reveal(card["id"], other, db)]:
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 404
        assert not (await api.list_cards(None, other, db))["items"]
    finally:
        await db.delete(other)
        await db.commit()
    await api.edit_card(card["id"], api.CardEditIn(status="SKIPPED"), ws, db)
    assert (await api.list_cards("SKIPPED", ws, db))["items"][0]["id"] == card["id"]
    assert not (await api.list_cards("ACTIVE", ws, db))["items"]
    await api.delete_card(card["id"], ws, db)
    assert not (await api.create_cards(job.note_id, request, ws, db))["items"]


def test_hallucinated_quotes_and_message_ids_fail_instead_of_saving():
    snapshot = [{"id": 7, "session_id": 8, "role": "assistant", "text": "需要核实", "materials": []}]
    for bad_id, bad_quote in [(7, "已核实"), (8, "需要核实")]:
        output = service.NoteOutput(title="笔记", points=[service.NotePoint(kind="unresolved", text="待核实",
                                     citations=[service.Citation(message_id=bad_id, quote=bad_quote)])])
        with pytest.raises(service.GenerationError):
            service.render_note(output, snapshot)


def test_quote_matching_tolerates_whitespace_differences():
    # 模型引文常因换行/连续空格与原文不一致，仅空白差异应视为可接受的引用。
    snapshot = [{"id": 7, "session_id": 8, "role": "assistant",
                 "text": "第一行结论。\n\n第二行\n细节与结尾。", "materials": []}]
    for quote in ["第一行结论。", "第二行\n\n细节", "第一行结论。\n\n第二行", "  第二行\n细节  "]:
        output = service.NoteOutput(title="笔记", points=[service.NotePoint(kind="explanation", text="结论",
                                     citations=[service.Citation(message_id=7, quote=quote)])])
        body, sources = service.render_note(output, snapshot)
        assert sources and sources[0]["quote"] == quote
        assert "## 核心解释" in body
    # 顺序或内容不同的引文仍然拒绝。
    output = service.NoteOutput(title="笔记", points=[service.NotePoint(kind="explanation", text="结论",
                                 citations=[service.Citation(message_id=7, quote="结尾。第一行")])])
    with pytest.raises(service.GenerationError):
        service.render_note(output, snapshot)

@pytest.mark.asyncio
async def test_overlapping_card_requests_generate_once(db_session, test_workspace, monkeypatch):
    import asyncio

    db, ws = db_session, test_workspace
    chat, _, _ = await conversation(db, ws)
    job = await service.enqueue(db, ws.id, chat.id, "cards", None)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    monkeypatch.setattr(service, "generate_note", generate)
    await service.process_next_note_job(factory)
    await db.refresh(job)
    req = api.CardGenIn(version_id=job.version_id)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def overlapping(body):
        calls.append(body)
        entered.set()
        await release.wait()
        return [service.CardOutput(question="为什么？", answer="原因", knowledge_point="就绪")]

    async def client():
        async with factory() as client_db:
            return await api.create_cards(job.note_id, req, ws, client_db)

    monkeypatch.setattr(service, "generate_cards", overlapping)
    first = asyncio.create_task(client())
    await asyncio.wait_for(entered.wait(), 5)
    second = asyncio.create_task(client())
    release.set()
    a, b = await asyncio.wait_for(asyncio.gather(first, second), 8)
    assert a == b and len(a["items"]) == len(calls) == 1
