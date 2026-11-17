"""Project analysis is persistent context, never personal mastery evidence."""
import importlib
import importlib.util
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Evidence, MemoryItem, Project, Skill, Workspace


def api_module():
    assert importlib.util.find_spec('app.api.v1.project_analysis'), 'persistent project analysis API is required'
    return importlib.import_module('app.api.v1.project_analysis')


async def project(session, ws):
    row = Project(workspace_id=ws.id, name='Fixture project', owner='example', repo='repo',
                  branch='main', repo_url='https://github.com/example/repo',
                  meta={'kind': 'github', 'profile': {'role': '后端开发', 'responsibilities': '负责接口'}})
    session.add(row)
    await session.commit()
    return row


def analyzed(revision='a' * 40):
    citation = {'path': 'app.py', 'start_line': 1, 'end_line': 2}
    return {'source_revision': revision, 'snapshot': {'files': [{'path': 'app.py', 'content': 'def run():\n    pass\n'}],
             'coverage': {'files_read': 1, 'eligible_files': 2, 'unread_files': ['test.py'], 'tree_truncated': False}},
            'report': {'schema_version': 1, 'summary': '处理任务的后端服务',
                       'architecture': [{'title': '入口', 'detail': 'run 是任务入口', 'citations': [citation]}],
                       'issues': [{'id': 'I1', 'title': '任务处理仍为空实现', 'skill_key': 'backend',
                                   'severity': 'medium', 'status': 'observed', 'detail': 'run 直接返回',
                                   'impact': '任务未处理', 'recommendation': '定义任务执行契约并测试',
                                   'citations': [citation], 'interview_questions': ['如何保证任务只执行一次？']}],
                       'unknowns': ['未阅读测试文件，不能断言没有测试']}, 'result_meta': {'model': 'fixture'}}


async def finish(session, ws, row, revision='a' * 40):
    # Explicit report fixture plus simulated user review; never real model evidence.
    api = api_module()
    task = await api.create_analysis(row.id, api.AnalysisIn(request_key=uuid4().hex), ws=ws, session=session)
    from app.models.project_analysis import ProjectAnalysis
    run = await session.get(ProjectAnalysis, task['id'])
    result=analyzed(revision)
    run.status='COMPLETED';run.report_status='VALID';run.coverage_status='COMPLETE'
    run.source_revision=revision;run.snapshot=result['snapshot'];run.report=result['report']
    citation=run.report['issues'][0]['citations'][0]
    run.report={**run.report,'claims':[{'id':'c1','citations':[citation]}]}
    row.latest_valid_analysis_id=run.id
    await session.flush()
    from app.services.project_candidates import publish_candidates,review_candidates,invalidate_changed_sources
    await invalidate_changed_sources(session,row,None,revision)
    candidates=await publish_candidates(session,run,[{'fact_key':'issue-I1','mtype':'ProjectIssue','kind':'issue',
        'issue_id':'I1','content':run.report['issues'][0]['title'],'claim_ids':['c1'],'fact_type':'CODE_OBSERVED'}])
    await session.commit()
    await review_candidates(session,ws.id,[{'candidate_id':m.id,'expected_revision':m.revision,'action':'confirm'} for m in candidates])
    return run


@pytest.mark.asyncio
async def test_analysis_create_idempotency_ownership_and_no_evidence(db_session, test_workspace):
    api = api_module()
    row = await project(db_session, test_workspace)
    req = api.AnalysisIn(focus='任务可靠性', request_key='same-click')
    one = await api.create_analysis(row.id, req, ws=test_workspace, session=db_session)
    two = await api.create_analysis(row.id, req, ws=test_workspace, session=db_session)
    assert one['id'] == two['id'] and one['status'] == 'QUEUED'
    repeated=await api.create_analysis(row.id, api.AnalysisIn(focus='changed', request_key='same-click'), ws=test_workspace, session=db_session)
    assert repeated['id']==one['id']  # obsolete focus text never changes built-in instructions
    other = Workspace(id=-902, slug='unowned')
    with pytest.raises(HTTPException) as exc:
        await api.get_analysis(row.id, one['id'], ws=other, session=db_session)
    assert exc.value.status_code == 404
    assert await db_session.scalar(select(func.count(Evidence.id)).where(Evidence.workspace_id == test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_analysis_versions_publish_scoped_memory_not_mastery(db_session, test_workspace):
    row = await project(db_session, test_workspace)
    first = await finish(db_session, test_workspace, row)
    from app.services.project_context import build_project_context
    frozen = (await build_project_context(db_session, test_workspace.id, [row.id]))[0]
    assert any(f.get('analysis_id') == first.id for f in frozen['facts'])
    assert all(f['source'] == 'repository_observation' for f in frozen['facts'] if f.get('analysis_id'))
    second = await finish(db_session, test_workspace, row, 'b' * 40)
    memories = (await db_session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == test_workspace.id))).all()
    assert all(m.status == 'SUPERSEDED' for m in memories if m.meta['analysis_id'] == first.id)
    assert any(m.status == 'ACTIVE' and m.meta['analysis_id'] == second.id for m in memories)
    assert first.id != second.id and frozen['facts'] != (await build_project_context(db_session, test_workspace.id, [row.id]))[0]['facts']
    assert await db_session.scalar(select(func.count(Evidence.id)).where(Evidence.workspace_id == test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_memory_correction_keeps_provenance_and_dismissal_removes_prompt_context(db_session, test_workspace):
    row = await project(db_session, test_workspace)
    run = await finish(db_session, test_workspace, row)
    from app.api.v1 import memory
    from app.services.project_context import build_project_context
    issue = await db_session.scalar(select(MemoryItem).where(MemoryItem.workspace_id == test_workspace.id, MemoryItem.mtype == 'ProjectIssue'))
    corrected = await memory.correct_memory(issue.id, memory.CorrectReq(content='用户纠正：这是刻意保留的扩展点，需要追问设计取舍。'), ws=test_workspace, session=db_session)
    new = await db_session.get(MemoryItem, corrected['id'])
    assert new.meta['analysis_id'] == run.id and new.meta['project_id'] == row.id
    assert new.source == 'USER' and issue.status == 'SUPERSEDED'
    with pytest.raises(HTTPException) as exc:
        await memory.confirm_memory(issue.id, ws=test_workspace, session=db_session)
    assert exc.value.status_code == 409
    context = (await build_project_context(db_session, test_workspace.id, [row.id]))[0]
    assert any('刻意保留' in f['text'] for f in context['facts'])
    await memory.delete_memory(new.id, ws=test_workspace, session=db_session)
    context = (await build_project_context(db_session, test_workspace.id, [row.id]))[0]
    assert not any('刻意保留' in f['text'] for f in context['facts'])


@pytest.mark.asyncio
async def test_issue_bound_generation_freezes_memory_and_rejects_dismissed_or_foreign_source(db_session, test_workspace):
    row = await project(db_session, test_workspace)
    run = await finish(db_session, test_workspace, row)
    from app.api.v1 import question_generation as generation, memory
    if not await db_session.scalar(select(Skill).where(Skill.key == 'backend')):
        db_session.add(Skill(key='backend', name='后端工程'))
        await db_session.commit()
    req = generation.GenerationIn(mode='external', count=1, skill_keys=['backend'], project_ids=[row.id], source_analysis_id=run.id, source_issue_ids=['I1'])
    task = await generation.create_task(req, ws=test_workspace, session=db_session)
    suggestions = await generation.suggestions(ws=test_workspace, session=db_session)
    assert any(s.get('source_analysis_id') == run.id and s['source_issue_ids'] == ['I1'] for s in suggestions['items'])
    target = task['input_snapshot']['project_issue_targets'][0]
    assert target['analysis_id'] == run.id and target['issue_id'] == 'I1' and target['memory_id']
    assert 'def run()' in target['excerpts'][0]['excerpt']
    assert '任务处理' in task['prompt_text'] and '个人能力' in task['prompt_text']
    await memory.delete_memory(target['memory_id'], ws=test_workspace, session=db_session)
    with pytest.raises(HTTPException) as exc:
        await generation.create_task(req, ws=test_workspace, session=db_session)
    assert exc.value.status_code == 422
    assert '任务处理' in task['prompt_text']  # immutable older task
    other = await project(db_session, test_workspace)
    with pytest.raises(HTTPException) as exc:
        await generation.create_task(generation.GenerationIn(mode='external', project_ids=[other.id], source_analysis_id=run.id, source_issue_ids=['I1']), ws=test_workspace, session=db_session)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_failed_or_recovered_run_cannot_publish_memory(db_session, test_workspace):
    api = api_module()
    row = await project(db_session, test_workspace)
    task = await api.create_analysis(row.id, api.AnalysisIn(), ws=test_workspace, session=db_session)
    from app.models.project_analysis import ProjectAnalysis
    run = await db_session.get(ProjectAnalysis, task['id'])
    run.status = 'FAILED'
    await api.publish_success(db_session, run, analyzed())
    assert run.status == 'FAILED'
    assert await db_session.scalar(select(func.count(MemoryItem.id)).where(MemoryItem.workspace_id == test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_deleting_project_archives_active_project_memories(db_session, test_workspace):
    from app.api.v1 import projects
    row = await project(db_session, test_workspace)
    await finish(db_session, test_workspace, row)
    await projects.delete_project(row.id, ws=test_workspace, session=db_session)
    active = (await db_session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == test_workspace.id,
              MemoryItem.status == 'ACTIVE', MemoryItem.meta['project_id'].as_integer() == row.id))).all()
    assert active == [], 'deleted projects must not leave active project facts'


@pytest.mark.asyncio
async def test_legacy_failure_and_restart_keep_last_confirmed_memory(db_session, test_workspace, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.services import project_deep_analysis
    api = api_module()
    monkeypatch.setattr(api, 'SessionLocal', async_sessionmaker(db_session.bind, expire_on_commit=False))
    row = await project(db_session, test_workspace)
    first = await finish(db_session, test_workspace, row)
    task = await api.create_analysis(row.id, api.AnalysisIn(), ws=test_workspace, session=db_session)
    from app.models.project_analysis import ProjectAnalysis
    run = await db_session.get(ProjectAnalysis, task['id'])
    run.status = 'RUNNING'
    run.pipeline_version = 'legacy'
    await db_session.commit()
    async def fail(*args, **kwargs):
        raise RuntimeError('Authorization: do-not-persist-provider-secret')
    monkeypatch.setattr(project_deep_analysis, 'analyze_project', fail)
    await api.execute_analysis(run.id, test_workspace.id, row.id)
    await db_session.refresh(run)
    assert run.status == 'FAILED' and 'do-not-persist' not in run.error
    assert run.snapshot is None and run.report is None
    current = (await db_session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == test_workspace.id, MemoryItem.status == 'ACTIVE'))).all()
    assert current and all(m.meta['analysis_id'] == first.id for m in current)
    run.status = 'RUNNING'
    await db_session.commit()
    await api.recover_interrupted()
    await db_session.refresh(run)
    assert run.status == 'FAILED' and '重启' in run.error


@pytest.mark.asyncio
async def test_issue_question_review_and_actual_answer_are_the_only_personal_evidence(db_session, test_workspace, monkeypatch, tmp_path):
    import json
    from app.api.v1 import bank, exams, question_generation as generation
    from app.models import ExamQuestion, LearningTask, Question
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.services import assessment_jobs, assessment_worker
    from test_question_workshop import response
    row = await project(db_session, test_workspace)
    run = await finish(db_session, test_workspace, row)
    if not await db_session.scalar(select(Skill).where(Skill.key == 'backend')):
        db_session.add(Skill(key='backend', name='后端工程'))
        await db_session.commit()
    task = await generation.create_task(generation.GenerationIn(mode='external', count=1, skill_keys=['backend'],
        project_ids=[row.id], source_analysis_id=run.id, source_issue_ids=['I1']), ws=test_workspace, session=db_session)
    target = task['input_snapshot']['project_issue_targets'][0]
    raw = response(task, title='如何解释该项目的任务处理扩展点？', skill_key='backend', question_mode='project', project_id=row.id,
        fact_refs=[target['fact_ref'], f'P{row.id}-responsibilities'], project_answer='项目入口保留了空实现；本人负责接口，具体执行契约仍需确认。')
    unrelated = json.loads(raw)
    unrelated['items'][0]['fact_refs'] = [f'P{row.id}-responsibilities']
    rejected = await generation.submit_result(task['id'], generation.ResultIn(text=json.dumps(unrelated)), ws=test_workspace, session=db_session)
    assert rejected['status'] == 'FAILED' and '具体问题' in rejected['error']
    result = await generation.submit_result(task['id'], generation.ResultIn(text=raw), ws=test_workspace, session=db_session)
    approved = await bank.approve_import(result['import_session_id'], bank.ApproveItemReq(), ws=test_workspace, session=db_session)
    question = await db_session.scalar(select(Question).where(Question.short_code == approved['created'][0]))
    assert question.generation_meta['input_snapshot']['source_analysis_id'] == run.id
    assert await db_session.scalar(select(func.count(Evidence.id)).where(Evidence.workspace_id == test_workspace.id)) == 0
    exam = await exams.create_exam(exams.CreateExamReq(kind='bank_exam', question_ids=[question.id], question_count=1, idempotency_key='project-assessment'), ws=test_workspace, session=db_session)
    # An explicitly blank answer uses the existing deterministic blank-answer path.
    await exams.save_answer(exam['id'], exams.SaveAnswerReq(order_no=1, text_answer=''), ws=test_workspace, session=db_session)
    ack = await exams.submit_exam(exam['id'], ws=test_workspace, session=db_session)
    assert ack['exam_id'] == exam['id'] and ack['status'] == 'SCORING'
    assert await db_session.scalar(select(func.count(Evidence.id)).where(Evidence.workspace_id == test_workspace.id)) == 0
    monkeypatch.setattr(assessment_worker, 'OUTBOX_ROOT', tmp_path / 'assessment-outbox')
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    async with factory() as worker_session:
        claim = await assessment_jobs.claim_step(worker_session, 'project-evidence-fixture')
    assert claim is not None and claim.run_id == ack['task_id']
    assert not (claim.payload['submitted_answer'].get('text_answer') or '').strip()
    await assessment_worker.execute_claim(claim, 'project-evidence-fixture', factory)
    await assessment_worker.reconcile(factory)
    async with factory() as reporting_session:
        report = await exams._exam_report(reporting_session, test_workspace, exam['id'])
    assert report['status'] == 'COMPLETED', report.get('scoring_task')
    observations = (await db_session.scalars(select(Evidence).where(Evidence.workspace_id == test_workspace.id))).all()
    assert len(observations) == 1 and observations[0].kind == 'EXAM_ANSWER'
    eq = await db_session.scalar(select(ExamQuestion).where(ExamQuestion.exam_id == exam['id']))
    assert eq.frozen_snapshot['project_provenance']['issue_targets'][0]['analysis_id'] == run.id
    tasks = (await db_session.scalars(select(LearningTask).where(LearningTask.workspace_id == test_workspace.id))).all()
    assert tasks and all(t.source_evidence_id == observations[0].id for t in tasks)
    detail = await api_module().get_analysis(row.id, run.id, ws=test_workspace, session=db_session)
    assert detail['generations'][0]['assessments'][0]['id'] == exam['id']
