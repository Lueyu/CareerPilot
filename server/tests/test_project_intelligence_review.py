"""Independent regression review of issue provenance and memory consistency."""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import bank, exams, memory, question_generation as generation
from app.models import Exam, MemoryItem, Question, QuestionGeneration, Skill
from test_project_intelligence import api_module, finish, project
from test_question_workshop import response


async def backend_skill(session):
    skill = await session.scalar(select(Skill).where(Skill.key == 'backend'))
    if skill is None:
        skill = Skill(key='backend', name='后端工程')
        session.add(skill)
        await session.commit()
    return skill


async def issue_batch(session, ws, row, run, title, source_id=None):
    await backend_skill(session)
    task = await generation.create_task(generation.GenerationIn(
        mode='external', count=1, skill_keys=['backend'], project_ids=[row.id],
        source_analysis_id=run.id, source_issue_ids=['I1']), ws=ws, session=session)
    target = task['input_snapshot']['project_issue_targets'][0]
    raw = response(task, title=title, original_question=title, skill_key='backend',
        question_mode='project', project_id=row.id,
        fact_refs=[target['fact_ref'], f'P{row.id}-responsibilities'],
        project_answer='当前项目有明确的任务入口；本人负责接口。执行契约仍需核实。',
        source_question_id=source_id)
    stored = await generation.submit_result(task['id'], generation.ResultIn(text=raw), ws=ws, session=session)
    approved = await bank.approve_import(stored['import_session_id'], bank.ApproveItemReq(), ws=ws, session=session)
    return task, approved


@pytest.mark.asyncio
async def test_same_title_general_question_cannot_discard_project_issue_answer(db_session, test_workspace):
    row = await project(db_session, test_workspace)
    run = await finish(db_session, test_workspace, row)
    skill = await backend_skill(db_session)
    title = '项目任务契约 ' + uuid4().hex
    ordinary = Question(short_code='GEN-' + uuid4().hex[:16], workspace_id=test_workspace.id,
        title=title, qtype='open', topic='sys', skill_id=skill.id,
        answer_html='<p>普通任务调度知识</p>', rubric={'max_points': 5, 'points': [{'title':'任务调度契约','detail':'解释任务输入、状态转换和失败恢复边界。'}]})
    db_session.add(ordinary)
    await db_session.commit()
    old_exam = await exams.create_exam(exams.CreateExamReq(kind='bank_exam', question_ids=[ordinary.id],
        question_count=1), ws=test_workspace, session=db_session)
    task, approved = await issue_batch(db_session, test_workspace, row, run, title, ordinary.id)
    assert len(approved['created']) == 1, 'project-specific content must survive a title collision'
    variant = await db_session.scalar(select(Question).where(Question.short_code == approved['created'][0]))
    assert variant.id != ordinary.id and variant.canonical_question_id == ordinary.id
    assert variant.generation_meta['input_snapshot']['source_analysis_id'] == run.id
    assert '当前项目有明确的任务入口' in variant.answer_html
    new_exam = await exams.create_exam(exams.CreateExamReq(kind='bank_exam', question_ids=[variant.id],
        question_count=1), ws=test_workspace, session=db_session)
    detail = await api_module().get_analysis(row.id, run.id, ws=test_workspace, session=db_session)
    batch = next(b for b in detail['generations'] if b['id'] == task['id'])
    assert [e['id'] for e in batch['assessments']] == [new_exam['id']]
    assert old_exam['id'] not in [e['id'] for e in batch['assessments']]


@pytest.mark.asyncio
async def test_merged_same_context_batches_do_not_claim_each_others_exams(db_session, test_workspace):
    row = await project(db_session, test_workspace)
    run = await finish(db_session, test_workspace, row)
    title = '同来源项目问题 ' + uuid4().hex
    first, first_approved = await issue_batch(db_session, test_workspace, row, run, title)
    question = await db_session.scalar(select(Question).where(Question.short_code == first_approved['created'][0]))
    old_exam = await exams.create_exam(exams.CreateExamReq(kind='bank_exam', question_ids=[question.id],
        question_count=1, project_generation_id=first['id']), ws=test_workspace, session=db_session)
    second, second_approved = await issue_batch(db_session, test_workspace, row, run, title)
    assert second_approved['created'] == [] and second_approved['merged'][0]['question_id'] == question.id
    before = await api_module().get_analysis(row.id, run.id, ws=test_workspace, session=db_session)
    second_before = next(b for b in before['generations'] if b['id'] == second['id'])
    assert second_before['assessments'] == [], 'an earlier exam must not be attributed to the new generation'
    new_exam = await exams.create_exam(exams.CreateExamReq(kind='bank_exam', question_ids=[question.id],
        question_count=1, project_generation_id=second['id']), ws=test_workspace, session=db_session)
    detail = await api_module().get_analysis(row.id, run.id, ws=test_workspace, session=db_session)
    by_batch = {b['id']: [e['id'] for e in b['assessments']] for b in detail['generations']}
    assert by_batch[first['id']] == [old_exam['id']]
    assert by_batch[second['id']] == [new_exam['id']]
    persisted = await db_session.get(Exam, new_exam['id'])
    provenance = getattr(persisted, 'project_assessment', None)
    assert provenance and provenance['sources'][0]['generation_id'] == second['id']


@pytest.mark.asyncio
async def test_changed_personal_responsibility_is_not_merged_into_old_project_answer(db_session, test_workspace):
    from app.api.v1 import projects
    row = await project(db_session, test_workspace)
    run = await finish(db_session, test_workspace, row)
    title = '本人职责边界 ' + uuid4().hex
    first, approved = await issue_batch(db_session, test_workspace, row, run, title)
    original = await db_session.scalar(select(Question).where(Question.short_code == approved['created'][0]))
    await projects.update_project_profile(row.id, projects.ProjectProfileIn(profile={
        'responsibilities': '仅设计接口契约，执行引擎由同事实现，不能归为本人贡献。',
    }), ws=test_workspace, session=db_session)
    second, new_approved = await issue_batch(db_session, test_workspace, row, run, title)
    assert len(new_approved['created']) == 1, 'changed personal facts require a fresh project answer'
    variant = await db_session.scalar(select(Question).where(Question.short_code == new_approved['created'][0]))
    assert variant.canonical_question_id == original.id
    new_context = variant.generation_meta['input_snapshot']['project_context'][0]
    assert '同事实现' in new_context['profile']['responsibilities']
    assert new_context['snapshot_fingerprint'] != first['input_snapshot']['project_context'][0]['snapshot_fingerprint']


@pytest.mark.asyncio
async def test_pre_upgrade_generation_request_keeps_idempotency(db_session, test_workspace):
    await backend_skill(db_session)
    req = generation.GenerationIn(mode='external', count=1, skill_keys=['backend'], request_key=uuid4().hex)
    task = await generation.create_task(req, ws=test_workspace, session=db_session)
    saved = await db_session.get(QuestionGeneration, task['id'])
    saved.request_json = {k: v for k, v in saved.request_json.items() if k not in ('source_analysis_id', 'source_issue_ids')}
    await db_session.commit()
    repeated = await generation.create_task(req, ws=test_workspace, session=db_session)
    assert repeated['id'] == task['id']


@pytest.mark.asyncio
async def test_generation_freezes_consistent_issue_refs_during_memory_correction(db_session, test_workspace, monkeypatch):
    row = await project(db_session, test_workspace)
    run = await finish(db_session, test_workspace, row)
    await backend_skill(db_session)
    original_issue = await db_session.scalar(select(MemoryItem).where(
        MemoryItem.workspace_id == test_workspace.id, MemoryItem.mtype == 'ProjectIssue'))
    original_id = original_issue.id
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    snapshot_ready, continue_generation, correction_called = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_builder = generation.build_project_context
    async def pause_after_snapshot(*args, **kwargs):
        result = await original_builder(*args, **kwargs)
        snapshot_ready.set()
        await continue_generation.wait()
        return result
    monkeypatch.setattr(generation, 'build_project_context', pause_after_snapshot)
    req = generation.GenerationIn(mode='external', count=1, skill_keys=['backend'], project_ids=[row.id],
        source_analysis_id=run.id, source_issue_ids=['I1'])
    async def create():
        async with factory() as session:
            return await generation.create_task(req, ws=test_workspace, session=session)
    async def correct():
        async with factory() as session:
            correction_called.set()
            return await memory.correct_memory(original_id, memory.CorrectReq(content='并发纠正后的项目理解'),
                ws=test_workspace, session=session)
    creation = asyncio.create_task(create())
    await asyncio.wait_for(snapshot_ready.wait(), 5)
    correction = asyncio.create_task(correct())
    try:
        await correction_called.wait()
        # The read transaction must serialize the correction until its immutable
        # context and selected issue references have both been captured.
        done, _ = await asyncio.wait({correction}, timeout=0.2)
        assert not done, 'memory mutation must not split generation context from issue references'
    finally:
        continue_generation.set()
        results = await asyncio.gather(creation, correction, return_exceptions=True)
    assert all(not isinstance(result, BaseException) for result in results)
    frozen, changed = results
    target = frozen['input_snapshot']['project_issue_targets'][0]
    facts = frozen['input_snapshot']['project_context'][0]['facts']
    assert target['memory_id'] == original_id
    assert target['fact_ref'] in {f['id'] for f in facts}
    monkeypatch.setattr(generation, 'build_project_context', original_builder)
    later = await generation.create_task(req, ws=test_workspace, session=db_session)
    assert later['input_snapshot']['project_issue_targets'][0]['memory_id'] == changed['id']
    assert '并发纠正后的项目理解' in later['prompt_text']
