"""Cross-workspace and overlapping-review regressions on a disposable PostgreSQL DB."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import bank, projects
from app.api.v1 import question_generation as generation
from app.models import (
    Evidence,
    ImportItem,
    ImportSession,
    Question,
    QuestionGeneration,
    Skill,
    Workspace,
)


@pytest_asyncio.fixture
async def other_workspace(db_session):
    row = Workspace(slug="workshop-other-" + uuid4().hex, name="Other workspace", is_demo=False)
    db_session.add(row)
    await db_session.commit()
    row_id = row.id
    yield row
    await db_session.rollback()
    await db_session.execute(delete(Workspace).where(Workspace.id == row_id))
    await db_session.commit()


async def create_task(session, workspace, **changes):
    if not await session.scalar(select(Skill).where(Skill.key == "rag")):
        session.add(Skill(key="rag", name="RAG"))
        await session.commit()
    arguments = dict(kind="supplement", mode="external", count=1, skill_keys=["rag"])
    arguments.update(changes)
    return await generation.create_task(generation.GenerationIn(**arguments), ws=workspace, session=session)


def model_reply(task, title):
    # Deliberate model-output fixture. No configured model or network is used.
    item = {
        "title": title, "original_question": "", "skill_key": "rag", "question_mode": "general",
        "project_id": None, "fact_refs": [], "project_answer": "", "hypothetical_answer": "",
        "missing_context": [], "source_question_id": None,
        "answer": {
            "tldr": "固定相关性标注集后评估 Recall@k。", "elevator": "先固定查询与文档版本，再比较召回曲线。",
            "analogy": "检查候选书架是否包含需要的书。", "examiner": ["召回质量"],
            "steps": [{"title": "固定评估集", "detail": "标出每条查询对应的相关文档。"}],
            "how_to_answer": "说明召回率分子和分母的定义。", "pitfalls": ["只看答案质量而不评估召回。"],
            "followups": [{"q": "如何选 k？", "a": "兼顾召回率和上下文预算。"}],
            "key_points": [{"title": "固定相关性标注集", "detail": "保持查询及语料版本可比较。"}], "terms": [],
        },
    }
    return json.dumps({"schema_version": 1, "batch_id": task["id"], "items": [item]}, ensure_ascii=False)


async def wait_for_database_lock(observer, process_id):
    """Wait for actual PostgreSQL contention, not an arbitrary scheduling sleep."""
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        waiting = await observer.scalar(text(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid = :process_id AND NOT granted)"
        ), {"process_id": process_id})
        if waiting:
            return
        await asyncio.sleep(0.02)
    pytest.fail(f"database operation {process_id} did not reach the held lock")


@pytest.mark.asyncio
async def test_overlapping_generated_approval_and_rejection_leave_one_consistent_outcome(db_session, test_workspace):
    task = await create_task(db_session, test_workspace)
    result = await generation.submit_result(task["id"], generation.ResultIn(text=model_reply(task, "并发审核案例 " + uuid4().hex)), ws=test_workspace, session=db_session)
    import_id, item_id = result["import_session_id"], result["review"]["items"][0]["id"]
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    workspace = SimpleNamespace(id=test_workspace.id, is_demo=False)
    processes = {}
    connected = {action: asyncio.Event() for action in ("approve", "reject")}

    async def act(action):
        async with factory() as session:
            processes[action] = await session.scalar(text("SELECT pg_backend_pid()"))
            connected[action].set()
            try:
                if action == "approve":
                    return await bank.approve_import(import_id, bank.ApproveItemReq(), ws=workspace, session=session)
                return await bank.reject_import_item(import_id, item_id, ws=workspace, session=session)
            except HTTPException as exc:
                await session.rollback()
                return exc

    # Put approval in the row-lock queue first. Rejection must not read a stale
    # DRAFT and later overwrite APPROVED when this transaction releases the row.
    await db_session.scalar(select(ImportItem).where(ImportItem.id == item_id).with_for_update())
    jobs = []
    try:
        jobs.append(asyncio.create_task(act("approve")))
        await asyncio.wait_for(connected["approve"].wait(), 5)
        await wait_for_database_lock(db_session, processes["approve"])
        jobs.append(asyncio.create_task(act("reject")))
        await asyncio.wait_for(connected["reject"].wait(), 5)
        await wait_for_database_lock(db_session, processes["reject"])
        await db_session.commit()
        approved, rejected = await asyncio.wait_for(asyncio.gather(*jobs), 10)
    finally:
        await db_session.rollback()
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
    assert not isinstance(approved, HTTPException)
    assert len(approved["created"]) == 1
    assert isinstance(rejected, HTTPException) and rejected.status_code == 409
    item = await db_session.scalar(select(ImportItem).where(ImportItem.id == item_id).execution_options(populate_existing=True))
    assert item.status == "APPROVED" and item.question_id is not None
    assert await db_session.scalar(select(func.count()).select_from(Question).where(Question.workspace_id == test_workspace.id)) == 1
    assert await db_session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == test_workspace.id)) == 0
    assert (await db_session.get(ImportSession, import_id, populate_existing=True)).status == "APPROVED"


@pytest.mark.asyncio
async def test_public_question_detail_never_lists_another_workspaces_private_variants(db_session, test_workspace, other_workspace):
    suffix = uuid4().hex[:12]
    canonical = Question(short_code="ISO-P-" + suffix, title="公共原题", workspace_id=None, topic="rag", qtype="open")
    db_session.add(canonical)
    await db_session.flush()
    own = Question(short_code="ISO-O-" + suffix, title="本人项目改写", workspace_id=test_workspace.id, topic="rag", qtype="open", canonical_question_id=canonical.id)
    foreign = Question(short_code="ISO-F-" + suffix, title="另一个空间的私有改写", workspace_id=other_workspace.id, topic="rag", qtype="open", canonical_question_id=canonical.id)
    db_session.add_all([own, foreign])
    await db_session.commit()
    cleanup_ids = [canonical.id, own.id, foreign.id]
    try:
        result = await bank.bank_detail(canonical.short_code, ws=test_workspace, session=db_session)
        assert own.short_code in result["variants"]
        assert foreign.short_code not in result["variants"]
        reverse = await bank.bank_detail(canonical.short_code, ws=other_workspace, session=db_session)
        assert reverse["variants"] == [foreign.short_code]
        with pytest.raises(HTTPException) as exc:
            await bank.bank_detail(foreign.short_code, ws=test_workspace, session=db_session)
        assert exc.value.status_code == 404
    finally:
        await db_session.execute(delete(Question).where(Question.id.in_(cleanup_ids)))
        await db_session.commit()


@pytest.mark.asyncio
async def test_merged_import_item_keeps_existing_question_in_generation_snapshot(db_session, test_workspace):
    suffix = uuid4().hex[:12]
    question = Question(short_code="ISO-C-" + suffix, workspace_id=test_workspace.id, title="如何评估检索召回率？", topic="rag", qtype="open")
    source = ImportSession(short_code="ISO-I-" + suffix, workspace_id=test_workspace.id, paste_text="如何评估检索召回率？", status="APPROVED")
    db_session.add_all([question, source])
    await db_session.flush()
    db_session.add(ImportItem(session_id=source.id, title=question.title, status="MERGED", question_id=None, merged_into=question.id, is_duplicate_of=question.id))
    await db_session.commit()
    task = await create_task(db_session, test_workspace, kind="import", source_import_id=source.id)
    assert task["input_snapshot"]["source_questions"] == [{"id": question.id, "title": question.title, "canonical_question_id": None}]


@pytest.mark.asyncio
async def test_generation_rejects_foreign_project_before_persisting_context(db_session, test_workspace, other_workspace):
    foreign = await projects.create_project(projects.ProjectIn(name="Only other workspace", kind="manual", profile={"role": "PRIVATE-ROLE", "responsibilities": "PRIVATE-WORK"}), ws=other_workspace, session=db_session)
    baseline = await db_session.scalar(select(func.count()).select_from(QuestionGeneration).where(QuestionGeneration.workspace_id == test_workspace.id))
    with pytest.raises(HTTPException) as exc:
        await create_task(db_session, test_workspace, project_ids=[foreign["id"]])
    assert exc.value.status_code == 422
    assert "PRIVATE" not in str(exc.value.detail)
    after = await db_session.scalar(select(func.count()).select_from(QuestionGeneration).where(QuestionGeneration.workspace_id == test_workspace.id))
    assert after == baseline
    await db_session.rollback()
    await db_session.refresh(test_workspace)
    owned = await projects.create_project(projects.ProjectIn(name="Owned project", kind="manual", profile={"role": "开发", "responsibilities": "实现接口"}), ws=test_workspace, session=db_session)
    task = await create_task(db_session, test_workspace, project_ids=[owned["id"]])
    assert [context["id"] for context in task["input_snapshot"]["project_context"]] == [owned["id"]]
    assert "PRIVATE-WORK" not in task["prompt_text"]
