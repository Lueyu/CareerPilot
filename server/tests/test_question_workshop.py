"""Workflow contracts: isolated fixtures, never real model or personal database."""
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Evidence, ImportItem, Question, Skill, Workspace


def response(task, title="如何评估检索召回率？", **changes):
    item = {
        "title": title, "original_question": title, "skill_key": "rag",
        "question_mode": "general", "project_id": None, "fact_refs": [],
        "answer": {"tldr": "使用带相关性标注的查询集计算召回率。", "elevator": "固定查询和语料版本，计算 Recall@k。",
                   "analogy": "检查要找的书是否进入候选书架。", "examiner": ["检索质量评估"],
                   "steps": [{"title": "准备标注", "detail": "标出查询相关文档并固定语料版本。"}],
                   "how_to_answer": "解释分母是所有相关文档的数量。", "pitfalls": ["只用最终回答质量代替检索评估。"],
                   "followups": [{"q": "如何选择 k？", "a": "结合召回率曲线与上下文预算。"}],
                   "key_points": [{"title": "固定标注查询集", "detail": "计算前固定查询、相关文档及语料版本。"}], "terms": []},
        "project_answer": "", "hypothetical_answer": "", "missing_context": [], "source_question_id": None,
    }
    item.update(changes)
    return json.dumps({"schema_version": 1, "batch_id": task["id"], "items": [item]}, ensure_ascii=False)


async def create(session, ws, **changes):
    from app.api.v1 import question_generation as api
    if not await session.scalar(select(Skill).where(Skill.key == "rag")):
        session.add(Skill(key="rag", name="RAG"))
        await session.commit()
    args = dict(kind="supplement", mode="external", count=1, skill_keys=["rag"], project_ids=[], purpose="补充评估题")
    args.update(changes)
    return await api.create_task(api.GenerationIn(**args), ws=ws, session=session)


@pytest.mark.asyncio
async def test_create_is_idempotent_and_freezes_only_owned_context(db_session, test_workspace):
    from app.api.v1 import question_generation as api
    task = await create(db_session, test_workspace, request_key="one-click")
    again = await create(db_session, test_workspace, request_key="one-click")
    assert task["id"] == again["id"] and task["status"] == "READY"
    assert task["input_snapshot"]["project_context"] == []
    assert str(task["id"]) in task["prompt_text"]
    other = Workspace(slug="other-" + uuid4().hex, name="other")
    db_session.add(other)
    await db_session.flush()
    with pytest.raises(HTTPException) as exc:
        await api.get_task(task["id"], ws=other, session=db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_external_repair_edit_approve_preserves_answers_without_model_or_evidence(db_session, test_workspace, monkeypatch):
    from app.api.v1 import bank
    from app.api.v1 import question_generation as api
    from app.services import question_generation as protocol

    async def forbidden(*a, **k):
        pytest.fail("external channel must not call a model")
    monkeypatch.setattr(protocol, "generate_response", forbidden)
    monkeypatch.setattr(bank, "generate_answer", forbidden)
    task = await create(db_session, test_workspace)
    failed = await api.submit_result(task["id"], api.ResultIn(text="bad incomplete reply"), ws=test_workspace, session=db_session)
    assert failed["status"] == "FAILED" and failed["raw_response"] == "bad incomplete reply" and failed["error"]
    raw = response(task)
    result = await api.submit_result(task["id"], api.ResultIn(text=raw), ws=test_workspace, session=db_session)
    duplicate = await api.submit_result(task["id"], api.ResultIn(text=raw), ws=test_workspace, session=db_session)
    assert duplicate["import_session_id"] == result["import_session_id"]
    draft = result["review"]["items"][0]
    edited = draft["generation_meta"]["item"]
    edited["answer"]["tldr"] = "修改并保存的具体参考答案"
    saved = await api.edit_item(task["id"], draft["id"], api.ItemEdit(item=edited), ws=test_workspace, session=db_session)
    assert "修改并保存" in saved["draft_answer"]
    first = await bank.approve_import(result["import_session_id"], bank.ApproveItemReq(), ws=test_workspace, session=db_session)
    second = await bank.approve_import(result["import_session_id"], bank.ApproveItemReq(), ws=test_workspace, session=db_session)
    assert len(first["created"]) == 1 and second["created"] == []
    q = await db_session.scalar(select(Question).where(Question.short_code == first["created"][0]))
    assert q.workspace_id == test_workspace.id and q.source_type == "GENERATED"
    assert "修改并保存" in q.answer_html and q.generation_meta["item"]["answer"]["followups"]
    assert await db_session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == test_workspace.id)) == 0
    with pytest.raises(HTTPException) as exc:
        await api.edit_item(task["id"], draft["id"], api.ItemEdit(item=edited), ws=test_workspace, session=db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_same_original_import_variants_share_canonical(db_session, test_workspace):
    from app.api.v1 import bank
    from app.api.v1 import question_generation as api
    ids = []
    for title in ("项目 A 如何评估召回？", "项目 B 如何评估召回？"):
        task = await create(db_session, test_workspace, kind="import", source_text="原始面经：如何评估检索召回率？")
        result = await api.submit_result(task["id"], api.ResultIn(text=response(task, title, original_question="如何评估检索召回率？")), ws=test_workspace, session=db_session)
        approved = await bank.approve_import(result["import_session_id"], bank.ApproveItemReq(), ws=test_workspace, session=db_session)
        q = await db_session.scalar(select(Question).where(Question.short_code == approved["created"][0]))
        ids.append((q.id, q.canonical_question_id))
    assert ids[0][1] is None and ids[1][1] == ids[0][0]


@pytest.mark.asyncio
async def test_pasted_original_matches_existing_ordinary_question(db_session, test_workspace):
    from app.api.v1 import bank
    from app.api.v1 import question_generation as api
    task = await create(db_session, test_workspace, kind="import", source_text="如何评估检索召回率？")
    base = Question(short_code="F-" + uuid4().hex[:15], workspace_id=test_workspace.id, title="如何评估检索召回率？", topic="rag", qtype="open")
    db_session.add(base)
    await db_session.commit()
    result = await api.submit_result(task["id"], api.ResultIn(text=response(task, "新情境下如何评估召回？", original_question=base.title)), ws=test_workspace, session=db_session)
    approved = await bank.approve_import(result["import_session_id"], bank.ApproveItemReq(), ws=test_workspace, session=db_session)
    variant = await db_session.scalar(select(Question).where(Question.short_code == approved["created"][0]))
    assert variant.canonical_question_id == base.id


@pytest.mark.asyncio
async def test_generated_legacy_regeneration_cannot_drop_project_provenance(db_session, test_workspace):
    from app.api.v1 import bank
    from app.api.v1 import question_generation as api
    task = await create(db_session, test_workspace)
    result = await api.submit_result(task["id"], api.ResultIn(text=response(task)), ws=test_workspace, session=db_session)
    item = result["review"]["items"][0]
    with pytest.raises(HTTPException) as exc:
        await bank.regenerate_item_answer(result["import_session_id"], item["id"], ws=test_workspace, session=db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_missing_context_and_foreign_fact_rejected_before_approval(db_session, test_workspace):
    from app.api.v1 import bank, projects
    from app.api.v1 import question_generation as api
    project = await projects.create_project(projects.ProjectIn(name="未知职责项目", kind="manual"), ws=test_workspace, session=db_session)
    task = await create(db_session, test_workspace, project_ids=[project["id"]])
    raw = response(task, question_mode="needs_context", project_id=project["id"], missing_context=["请补充本人职责"])
    result = await api.submit_result(task["id"], api.ResultIn(text=raw), ws=test_workspace, session=db_session)
    assert result["status"] == "REVIEW", result["error"]
    with pytest.raises(HTTPException) as exc:
        await bank.approve_import(result["import_session_id"], bank.ApproveItemReq(), ws=test_workspace, session=db_session)
    assert exc.value.status_code == 422
    assert await db_session.scalar(select(func.count()).select_from(Question).where(Question.workspace_id == test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_api_mode_failure_retains_real_status_and_can_retry(db_session, test_workspace, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.v1 import question_generation as api
    from app.services import question_generation as protocol
    task = await create(db_session, test_workspace, mode="api")
    monkeypatch.setattr(api, "SessionLocal", async_sessionmaker(db_session.bind, expire_on_commit=False))
    calls = []
    async def fail(prompt):
        calls.append(prompt)
        raise RuntimeError("upstream failed")
    monkeypatch.setattr(protocol, "generate_response", fail)
    started = await api.run_task(task["id"], ws=test_workspace, session=db_session)
    assert started["status"] == "RUNNING"
    import asyncio
    await asyncio.gather(*list(api._running))
    failed = await api.get_task(task["id"], ws=test_workspace, session=db_session)
    assert failed["status"] == "FAILED" and failed["import_session_id"] is None
    async def succeed(prompt):
        calls.append(prompt)
        return {"text": response(task), "model": "test-fixture"}
    monkeypatch.setattr(protocol, "generate_response", succeed)
    await api.run_task(task["id"], ws=test_workspace, session=db_session)
    await asyncio.gather(*list(api._running))
    done = await api.get_task(task["id"], ws=test_workspace, session=db_session)
    assert done["status"] == "REVIEW" and len(calls) == 2


@pytest.mark.asyncio
async def test_selected_existing_source_preserves_canonical_and_exact_dedup(db_session, test_workspace):
    from app.api.v1 import bank
    from app.api.v1 import question_generation as api
    from app.models import ImportSession
    await create(db_session, test_workspace)
    skill = await db_session.scalar(select(Skill).where(Skill.key == "rag"))
    q = Question(short_code="F-" + uuid4().hex[:15], workspace_id=test_workspace.id, title="如何评估检索召回率？", qtype="open", topic="rag", skill_id=skill.id)
    db_session.add(q)
    await db_session.flush()
    imp = ImportSession(short_code="FI-" + uuid4().hex[:15], workspace_id=test_workspace.id, paste_text=q.title, status="APPROVED")
    db_session.add(imp)
    await db_session.flush()
    db_session.add(ImportItem(session_id=imp.id, title=q.title, question_id=q.id, status="APPROVED"))
    await db_session.commit()
    for title in ("召回结果如何度量？", q.title):
        task = await create(db_session, test_workspace, kind="import", source_import_id=imp.id)
        result = await api.submit_result(task["id"], api.ResultIn(text=response(task, title, original_question=q.title, source_question_id=q.id)), ws=test_workspace, session=db_session)
        done = await bank.approve_import(result["import_session_id"], bank.ApproveItemReq(), ws=test_workspace, session=db_session)
        if title != q.title:
            variant = await db_session.scalar(select(Question).where(Question.short_code == done["created"][0]))
            assert variant.canonical_question_id == q.id
        else:
            assert done["created"] == [] and done["merged"][0]["question_id"] == q.id


@pytest.mark.asyncio
async def test_approved_question_can_be_practiced_without_automatic_mastery(db_session, test_workspace):
    from app.api.v1 import bank, exams
    from app.api.v1 import question_generation as api
    task = await create(db_session, test_workspace)
    result = await api.submit_result(task["id"], api.ResultIn(text=response(task)), ws=test_workspace, session=db_session)
    await bank.approve_import(result["import_session_id"], bank.ApproveItemReq(), ws=test_workspace, session=db_session)
    completed = await api.get_task(task["id"], ws=test_workspace, session=db_session)
    assert completed["status"] == "COMPLETE"
    qid = completed["review"]["items"][0]["question_id"]
    exam = await exams.create_exam(exams.CreateExamReq(kind="bank_exam", question_ids=[qid], question_count=1), ws=test_workspace, session=db_session)
    question = await db_session.get(Question, qid)
    assert len(exam["questions"]) == 1 and exam["questions"][0]["short_code"] == question.short_code
    assert await db_session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_manual_selection_rejects_missing_and_retest_questions(db_session, test_workspace):
    from app.api.v1 import exams
    with pytest.raises(HTTPException) as exc:
        await exams.create_exam(exams.CreateExamReq(kind="bank_exam", question_ids=[999999999], question_count=1), ws=test_workspace, session=db_session)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await exams.create_exam(exams.CreateExamReq(kind="bank_exam", question_ids=[1], retest_task_id=9999), ws=test_workspace, session=db_session)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_source_import_preserves_full_text_and_refuses_lost_uploaded_context(db_session, test_workspace):
    from app.models import ImportSession
    full = "项目背景" * 3000 + "末尾的真实职责"
    imp = ImportSession(short_code="FI-"+uuid4().hex[:15], workspace_id=test_workspace.id, paste_text=full[:10000], status="REVIEW", meta={"pasted_text": full})
    db_session.add(imp)
    await db_session.commit()
    task = await create(db_session, test_workspace, kind="import", source_import_id=imp.id)
    assert task["input_snapshot"]["source_text"] == full
    imp2 = ImportSession(short_code="FI-"+uuid4().hex[:15], workspace_id=test_workspace.id, status="REVIEW", filename="historical.pdf", meta={"source":"files"})
    db_session.add(imp2)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await create(db_session, test_workspace, kind="import", source_import_id=imp2.id)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_bank_edit_cannot_change_shared_question(db_session, test_workspace):
    from app.api.v1 import bank
    q = Question(short_code="PUB-"+uuid4().hex[:15], workspace_id=None, title="共享题目", topic="rag", qtype="open")
    db_session.add(q)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await bank.update_question(q.short_code, bank.QuestionUpdate(title="跨空间修改"), ws=test_workspace, session=db_session)
    assert exc.value.status_code == 403
