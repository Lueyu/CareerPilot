import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import bank
from app.models import ImportItem, ImportSession, Project, Question, Skill, Workspace


async def make_import(session, workspace):
    skill = await session.scalar(select(Skill).where(Skill.key == 'agent'))
    if skill is None:
        session.add(Skill(key='agent', name='Agent'))
    imp = ImportSession(workspace_id=workspace.id, short_code='TEST-' + uuid4().hex[:20], status='REVIEW')
    session.add(imp)
    await session.flush()
    item = ImportItem(session_id=imp.id, title='How do queues preserve ordering?', suggested_skill_key='agent',
                      draft_answer='<p>Old answer</p>', draft_rubric={'points': [{'title': 'Old criterion'}]})
    session.add(item)
    await session.commit()
    return imp.id, item.id


@pytest.mark.asyncio
async def test_changed_import_title_regenerates_both_scoring_artifacts(db_session, test_workspace, monkeypatch):
    imp_id, item_id = await make_import(db_session, test_workspace)
    revised = 'How does a retry budget prevent overload?'

    async def fresh_answer(title, *args):
        assert title == revised
        return '<p>New answer</p>', {'points': [{'title': 'Retry budget'}]}, 'fixture'

    monkeypatch.setattr(bank, 'generate_answer', fresh_answer)
    request = bank.ApproveItemReq(item_ids=[item_id], edits={item_id: {'title': revised}})
    result = await bank.approve_import(imp_id, request, test_workspace, db_session)
    question = await db_session.scalar(select(Question).where(Question.short_code.in_(result['created'])))
    assert question.title == revised and question.answer_html == '<p>New answer</p>'
    assert question.rubric == {'points': [{'title': 'Retry budget'}], 'source': 'fixture'}
    assert (await bank.approve_import(imp_id, request, test_workspace, db_session))['created'] == []


@pytest.mark.asyncio
async def test_set_save_waits_for_and_observes_concurrent_rejection(db_session, test_workspace, monkeypatch):
    from app.services import study_project_evidence

    imp_id, item_id = await make_import(db_session, test_workspace)
    project = Project(workspace_id=test_workspace.id, name='Concurrency fixture', repo_url='fixture', owner='test', repo='fixture')
    db_session.add(project)
    await db_session.commit()
    request = bank.InterviewSetRequest(project_id=project.id, item_ids=[item_id], title='Fixture', request_id='reject-race')

    async def no_evidence(*args):
        return []

    async def no_rewrite(*args):
        return {}, 'Fixture'

    monkeypatch.setattr(study_project_evidence, 'collect_project_evidence', no_evidence)
    monkeypatch.setattr(bank, 'rewrite_interview_questions', no_rewrite)
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    async with sessions() as rejecting, sessions() as saving:
        rejecting_pid = await rejecting.scalar(text('SELECT pg_backend_pid()'))
        saving_pid = await saving.scalar(text('SELECT pg_backend_pid()'))
        selected = await rejecting.scalar(select(ImportItem).where(ImportItem.id == item_id).with_for_update())
        selected.status = 'REJECTED'
        await rejecting.flush()
        save = asyncio.create_task(bank.create_interview_set(imp_id, request, Workspace(id=test_workspace.id), saving))
        try:
            for _ in range(150):
                blocked = await db_session.scalar(text('SELECT :held = ANY(pg_blocking_pids(:waiting))'),
                                                  {'held': rejecting_pid, 'waiting': saving_pid})
                if blocked or save.done():
                    break
                await asyncio.sleep(.02)
            assert blocked, 'Saving should wait for the selected original to be unlocked'
        finally:
            await rejecting.commit()
        with pytest.raises(HTTPException) as conflict:
            await save
        assert conflict.value.status_code == 409
    assert (await bank.list_interview_sets(test_workspace, db_session))['items'] == []
