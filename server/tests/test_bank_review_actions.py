import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_question_workshop import create, response

from app.api.v1 import bank
from app.api.v1 import question_generation as workshop
from app.models import Evidence, ImportItem, ImportSession, MemoryItem, Question, Skill
from app.services import bank_review_actions as review
from app.services import question_generation as protocol


async def raw_draft(session, workspace, *, answer="<p>标注文档后计算召回率。</p>", rubric=None):
    await create(session, workspace)
    imported = ImportSession(short_code="IM-" + uuid4().hex[:16], workspace_id=workspace.id,
                             status="REVIEW", meta={"source": "files"})
    session.add(imported)
    await session.flush()
    item = ImportItem(session_id=imported.id, title="评估检索召回 " + uuid4().hex,
                      suggested_skill_key="rag", draft_answer=answer,
                      draft_rubric=rubric or {"points": [{"title": "标注文档", "detail": "固定测试查询与相关文档。"}]},
                      generation_meta={"source_files": ["面经.md"], "extraction_method": "text"})
    session.add(item)
    await session.commit()
    return imported, item


async def submit(session, workspace, imported, item, *, action="approve", revision=0, request_id="review", feedback=None):
    request = review.ReviewRequest(action=action, revision=revision, request_id=request_id, feedback=feedback)
    return await bank.review_import_item(imported.id, item.id, request, ws=workspace, session=session)


@pytest.mark.asyncio
async def test_approve_saves_visible_content_and_replays_once(db_session, test_workspace, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Review approval never invokes generation")

    monkeypatch.setattr(bank, "generate_answer", forbidden)
    monkeypatch.setattr(protocol, "generate_response", forbidden)
    imported, item = await raw_draft(db_session, test_workspace)
    approval = {"feedback": "以后附带召回率分母的例子。"}
    result = await submit(db_session, test_workspace, imported, item, **approval)
    repeated = await submit(db_session, test_workspace, imported, item, **approval)
    assert result == repeated
    assert result["outcome"] == "approved"
    assert result["item"]["review_revision"] == 1
    saved = await db_session.get(Question, result["item"]["question_id"])
    assert (saved.answer_html, saved.rubric) == (item.draft_answer, item.draft_rubric)
    assert saved.generation_meta["source_files"] == ["面经.md"]
    assert "bank_review" not in saved.generation_meta
    memory = await db_session.get(MemoryItem, result["memory_id"])
    assert memory.content == approval["feedback"]
    assert await db_session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == test_workspace.id)) == 0
    with pytest.raises(HTTPException) as reused:
        await submit(db_session, test_workspace, imported, item, feedback="不同的意见")
    assert reused.value.status_code == 409


@pytest.mark.asyncio
async def test_approval_checks_visible_answer_and_recognizes_duplicate(db_session, test_workspace):
    imported, item = await raw_draft(db_session, test_workspace, answer="<p>&nbsp;</p><script>invisible answer</script><svg><text>also invisible</text></svg><select><option>hidden</option></select>")
    with pytest.raises(HTTPException) as incomplete:
        await submit(db_session, test_workspace, imported, item)
    assert incomplete.value.status_code == 422
    item.draft_answer = "<![unexpected declaration]><p>遗留答案</p>"
    await db_session.commit()
    view = await bank.import_status(imported.id, ws=test_workspace, session=db_session)
    assert view["items"][0]["has_reviewable_answer"] is False
    item.draft_answer = "<p>用固定标注检查召回率。</p>"
    item.draft_rubric = {"points": [{"title": "要点", "keywords": [item.title]}]}
    await db_session.commit()
    with pytest.raises(HTTPException) as placeholder:
        await submit(db_session, test_workspace, imported, item)
    assert placeholder.value.status_code == 422
    item.draft_rubric = {"points": [{"title": "标注查询", "detail": "先固定相关文档。"}]}
    skill = await db_session.scalar(select(Skill.id).where(Skill.key == "rag"))
    duplicate = Question(short_code="DU-" + uuid4().hex[:16], workspace_id=test_workspace.id,
                         title=item.title, qtype="open", topic="rag", skill_id=skill, difficulty=3,
                         source_type="USER_PRIVATE", is_demo=False)
    db_session.add(duplicate)
    await db_session.commit()
    result = await submit(db_session, test_workspace, imported, item)
    assert (result["outcome"], result["created"], result["item"]["merged_into"]) == ("merged", [], duplicate.id)


@pytest.mark.asyncio
async def test_revise_commits_intent_before_model_and_checks_stale_commands(db_session, test_workspace, monkeypatch):
    imported, item = await raw_draft(db_session, test_workspace)
    entered, release = asyncio.Event(), asyncio.Event()
    revised = json.loads(response({"id": 1}))["items"][0]["answer"]
    revised["tldr"] = "加入小样本例子。<script>literal</script>"

    async def model(prompt, *, stream_timeout):
        assert 0 < stream_timeout < review.LEASE_SECONDS
        assert "加一个反例" in prompt and "标注文档" in prompt
        entered.set()
        await release.wait()
        return {"text": json.dumps({"answer": revised}, ensure_ascii=False)}

    monkeypatch.setattr(protocol, "generate_response", model)
    task = asyncio.create_task(submit(db_session, test_workspace, imported, item, action="revise", feedback="加一个反例"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
        async with factory() as peer:
            view = await bank.import_status(imported.id, ws=test_workspace, session=peer)
            assert view["items"][0]["review_state"] == "running"
            memory = await peer.get(MemoryItem, view["items"][0]["memory_id"])
            assert memory.content == "加一个反例"
            replay = await asyncio.wait_for(submit(peer, test_workspace, imported, item, action="revise", feedback="加一个反例"), timeout=5)
            assert replay["outcome"] == "running"
            with pytest.raises(HTTPException) as busy:
                await submit(peer, test_workspace, imported, item, request_id="conflicting", revision=1)
            assert busy.value.status_code == 409
    finally:
        release.set()
        finished = await task
    assert finished["outcome"] == "revised" and finished["item"]["status"] == "DRAFT"
    assert finished["item"]["review_revision"] == 2
    assert "&lt;script&gt;literal&lt;/script&gt;" in finished["item"]["draft_answer"]
    assert finished["item"]["generation_meta"]["source_files"] == ["面经.md"]
    with pytest.raises(HTTPException) as stale:
        await submit(db_session, test_workspace, imported, item, request_id="stale", revision=1)
    assert stale.value.status_code == 409


@pytest.mark.asyncio
async def test_generated_revision_only_updates_answer_fields_and_failure_preserves_result(db_session, test_workspace, monkeypatch):
    draft_task = await create(db_session, test_workspace)
    raw = response(draft_task, "检索召回测试 " + uuid4().hex)
    submitted = await workshop.submit_result(draft_task["id"], workshop.ResultIn(text=raw),
                                             ws=test_workspace, session=db_session)
    imported = await db_session.get(ImportSession, submitted["import_session_id"])
    item = await db_session.get(ImportItem, submitted["review"]["items"][0]["id"])
    original = json.loads(raw)["items"][0]
    revised = {"answer": {**original["answer"], "tldr": "显式写出召回率分母。"}, "project_answer": "", "hypothetical_answer": ""}

    async def model(prompt, *, stream_timeout):
        return {"text": json.dumps(revised, ensure_ascii=False)}

    monkeypatch.setattr(protocol, "generate_response", model)
    first = await submit(db_session, test_workspace, imported, item, action="revise", feedback="补上召回率分母")
    assert first["outcome"] == "revised"
    assert first["item"]["generation_meta"]["item"] == {**original, **revised}
    revised["title"] = "不得覆盖题目"
    failed = await submit(db_session, test_workspace, imported, item, action="revise", revision=2,
                          request_id="bad-model", feedback="增加算例")
    assert failed["outcome"] == "failed" and failed["item"]["review_state"] == "failed"
    assert failed["item"]["status"] == "DRAFT"
    assert failed["item"]["draft_answer"] == first["item"]["draft_answer"]
    assert failed["item"]["generation_meta"]["item"] == first["item"]["generation_meta"]["item"]


@pytest.mark.asyncio
async def test_interrupted_revision_can_be_replaced_by_explicit_reject(db_session, test_workspace):
    imported, item = await raw_draft(db_session, test_workspace)
    item.generation_meta = {**item.generation_meta, "bank_review": {"revision": 1, "request_id": "interrupted", "receipts": {
        "interrupted": {"outcome": "running", "lease_until": 0, "action": "revise", "revision": 0, "feedback": "补算例"},
    }}}
    await db_session.commit()
    prior = await submit(db_session, test_workspace, imported, item, action="revise", request_id="interrupted", feedback="补算例")
    assert prior["outcome"] == prior["item"]["review_state"] == "failed"
    with pytest.raises(HTTPException) as missing:
        await submit(db_session, test_workspace, imported, item, action="reject", revision=1, feedback="  ")
    assert missing.value.status_code == 422
    reject = await submit(db_session, test_workspace, imported, item, action="reject", revision=1, feedback="题意与材料不符")
    assert reject["outcome"] == "rejected" and reject["item"]["status"] == "REJECTED"
    assert reject["item"]["draft_answer"] == prior["item"]["draft_answer"]
    assert imported.status == "APPROVED"


@pytest.mark.asyncio
async def test_missing_structured_origin_keeps_feedback_and_failed_receipt(db_session, test_workspace, monkeypatch):
    imported, item = await raw_draft(db_session, test_workspace)
    item.generation_meta = {**item.generation_meta, "task_id": 123}
    await db_session.commit()

    async def forbidden(*args):
        pytest.fail("Cannot revise without the frozen structured origin")

    monkeypatch.setattr(protocol, "generate_response", forbidden)
    request = {"action": "revise", "feedback": "补充一个例子"}
    result = await submit(db_session, test_workspace, imported, item, **request)
    assert result == await submit(db_session, test_workspace, imported, item, **request)
    assert result["outcome"] == "failed" and result["item"]["status"] == "DRAFT"
    assert result["item"]["draft_answer"] == item.draft_answer and result["item"]["review_error"]
    assert (await db_session.get(MemoryItem, result["memory_id"])).content == request["feedback"]
