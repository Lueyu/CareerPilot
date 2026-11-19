from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.project_dossier import get_project_dossier
from app.models import ImportItem, ImportSession, Project, ProjectAnalysis, Question, QuestionGeneration, Workspace
from app.services.project_autopilot_engine import Claim


def test_technology_is_an_optional_typed_claim():
    claim = {'id': 'retrieval', 'title': 'Query flow', 'text': 'Embeds a query before retrieval.',
             'kind': 'CODE_OBSERVED', 'citations': [{'source_id': 's1', 'start_line': 1, 'end_line': 2}]}
    assert Claim.model_validate(claim).technology is None
    parsed = Claim.model_validate({**claim, 'technology': {'name': 'RAG', 'limitations': 'Ranking quality unmeasured.'}})
    assert parsed.technology.name == 'RAG'


@pytest.mark.asyncio
async def test_dossier_keeps_real_reviewed_questions_separate_from_model_suggestions(db_session, test_workspace):
    wid = test_workspace.id
    project, other = [Project(workspace_id=wid, name=name, owner='o', repo=name, repo_url='')
                      for name in ('dossier', 'other')]
    db_session.add_all([project, other])
    await db_session.flush()
    revision = 'a' * 40
    ref = {'source_id': 's1', 'source_version': revision, 'path': 'search.py', 'start_line': 1, 'end_line': 1}
    code = {'source_id': 's1', 'source_revision': revision, 'path': 'search.py', 'content': 'search(query)'}
    claim = {'title': 'Search path', 'text': 'Search feeds generation.', 'citations': [ref],
             'technology': {'name': 'RAG', 'limitations': 'Quality was not measured.'}}
    run = ProjectAnalysis(
        workspace_id=wid, project_id=project.id, request_key='dossier', source_revision=revision,
        report_status='VALID', coverage_status='COMPLETE', snapshot={'files': [code], 'source_registry': {'s1': code}},
        report={
            'technologies_assessed': True,
            'summary': 'Uses a source-backed retrieval flow.',
            'claims': [{**claim, 'id': 'c1', 'kind': 'CODE_OBSERVED'},
                       {**claim, 'id': 'c2', 'kind': 'DOC_CLAIM', 'technology': {'name': 'Harness', 'limitations': 'Doc'}}],
            'unknowns': ['No load testing'],
            'issues': [{'id': 'I1', 'title': 'Measure search quality', 'status': 'hypothesis',
                        'recommendation': 'Replay a labeled query set.', 'impact': 'Establish a baseline.',
                        'interview_questions': ['How would you evaluate retrieval?'], 'citations': [ref]}],
        },
    )
    imp = ImportSession(workspace_id=wid, short_code=f'IM-{uuid4().hex[:12]}', status='REVIEW')
    questions = [Question(workspace_id=wid, short_code=f'Q-{uuid4().hex[:12]}', title=title, topic='rag', source_type='GENERATED')
                 for title in ('Actual approved question', 'Draft only', 'Other project',
                               'Related topic without project provenance')]
    db_session.add_all([run, imp, *questions])
    await db_session.flush()
    project.latest_valid_analysis_id = run.id
    project.last_seen_sha = 'b' * 40
    target = {'project_id': project.id, 'analysis_id': run.id, 'source_revision': revision, 'fact_ref': 'fact1'}
    db_session.add(QuestionGeneration(
        workspace_id=wid, import_session_id=imp.id, kind='project', mode='api', request_key='dossier-fixture',
        request_json={}, input_snapshot={'source_analysis_id': run.id, 'project_issue_targets': [target]},
    ))
    for question, status, linked_project in zip(questions, ('APPROVED', 'DRAFT', 'APPROVED'),
                                                (project, project, other)):
        db_session.add(ImportItem(
            session_id=imp.id, question_id=question.id, title=question.title, status=status,
            generation_meta={'item': {'project_id': linked_project.id, 'question_mode': 'project',
                                      'fact_refs': ['fact1']}},
        ))
    await db_session.flush()

    view = await get_project_dossier(project.id, test_workspace, db_session)
    assert view['technologies_assessed'] is True
    assert view['report']['summary'] == 'Uses a source-backed retrieval flow.'
    assert view['report']['freshness'] == 'NEEDS_REVIEW'
    assert [item['name'] for item in view['technologies']] == ['RAG']
    assert view['technologies'][0]['limitations'] == 'Quality was not measured.'
    assert view['source_excerpts'][0]['excerpt'] == '1: search(query)'
    assert view['limitations'] == ['No load testing']
    assert view['recommendations'][0]['status'] == 'hypothesis'
    assert view['suggested_questions'][0]['title'] == 'How would you evaluate retrieval?'
    assert view['bank_questions'] == [{
        'id': questions[0].id, 'short_code': questions[0].short_code, 'title': 'Actual approved question',
        'source_type': 'GENERATED', 'analysis_id': run.id, 'source_revision': revision,
    }]
    assert (await get_project_dossier(other.id, test_workspace, db_session))['technologies'] == []
    with pytest.raises(HTTPException) as missing:
        await get_project_dossier(project.id, type('Foreign', (), {'id': wid + 1})(), db_session)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_dossier_returns_all_sources_and_keeps_interview_sets_project_private(db_session, test_workspace):
    wid = test_workspace.id
    foreign = Workspace(slug=f'other-{uuid4().hex}', name='Other workspace')
    db_session.add(foreign)
    await db_session.flush()
    project, other = [
        Project(workspace_id=wid, name=name, owner='o', repo=name, repo_url='')
        for name in ('dossier-all', 'not-this-project')
    ]
    local_import = ImportSession(workspace_id=wid, short_code=f'IM-{uuid4().hex[:12]}')
    foreign_import = ImportSession(workspace_id=foreign.id, short_code=f'IM-{uuid4().hex[:12]}')
    db_session.add_all([project, other, local_import, foreign_import])
    await db_session.flush()
    local_item = ImportItem(session_id=local_import.id, title='Original prompt')
    foreign_item = ImportItem(session_id=foreign_import.id, title='Foreign prompt')
    db_session.add_all([local_item, foreign_item])
    await db_session.flush()

    def question(title, workspace_id, metadata=None):
        return Question(workspace_id=workspace_id, short_code=f'Q-{uuid4().hex[:12]}', title=title,
                        topic='rag', source_type='GENERATED', generation_meta=metadata)

    def practice(title, workspace_id, source_item, set_id):
        return question(title, workspace_id, {
            'assessment_use': 'PRACTICE_ONLY', 'interview_set_id': set_id,
            'source_item_id': source_item.id, 'original_title': source_item.title,
            'rewrite_method': 'model', 'evidence': {'revision': 'a' * 40},
        })

    current_practice = practice('Use this project', wid, local_item, 'current-set')
    other_project = practice('Use other project', wid, local_item, 'other-set')
    foreign_practice = practice('Use foreign project', foreign.id, foreign_item, 'foreign-set')
    bank_questions = [question(f'Approved {index}', wid) for index in range(15)]
    db_session.add_all([current_practice, other_project, foreign_practice, *bank_questions])
    await db_session.flush()

    def entry(imported, item, selected, set_id, project_id):
        return {'id': set_id, 'title': set_id, 'import_id': imported.id, 'project_id': project_id,
                'item_ids': [item.id], 'questions': [{'id': selected.id, 'source_item_id': item.id}]}

    local_import.meta = {'interview_sets': [
        entry(local_import, local_item, current_practice, 'current-set', project.id),
        entry(local_import, local_item, other_project, 'other-set', other.id),
        entry(local_import, local_item, foreign_practice, 'foreign-set', project.id),
    ]}
    foreign_import.meta = {'interview_sets': [
        entry(foreign_import, foreign_item, foreign_practice, 'foreign-set', project.id),
    ]}
    generated_import = ImportSession(workspace_id=wid, short_code=f'IM-{uuid4().hex[:12]}')
    db_session.add(generated_import)
    await db_session.flush()
    db_session.add(QuestionGeneration(
        workspace_id=wid, import_session_id=generated_import.id, kind='project', mode='api', request_key='all-bank',
        request_json={}, input_snapshot={},
    ))
    for approved in bank_questions:
        db_session.add(ImportItem(session_id=generated_import.id, question_id=approved.id,
                                 title=approved.title, status='APPROVED',
                                 generation_meta={'item': {'project_id': project.id, 'question_mode': 'project'}}))
    ref = {'path': 'source.py', 'start_line': 1, 'end_line': 1}
    report = ProjectAnalysis(
        workspace_id=wid, project_id=project.id, request_key='all-report', coverage_status='COMPLETE',
        report_status='VALID', report={
            'claims': [{'id': f'C{i}', 'kind': 'CODE_OBSERVED', 'citations': [ref], 'text': 'Implementation',
                        'technology': {'name': f'Technology {i}', 'limitations': 'Source only'}} for i in range(13)],
            'issues': [{'id': f'I{i}', 'title': 'Issue', 'citations': [ref], 'recommendation': 'Improve',
                        'interview_questions': [f'Suggested {i}']} for i in range(15)],
        })
    db_session.add(report)
    await db_session.flush()
    project.latest_valid_analysis_id = report.id
    run = ProjectAnalysis(workspace_id=wid, project_id=project.id, request_key='new-run',
                          status='RUNNING', pipeline_version='current')
    db_session.add(run)
    await db_session.flush()

    dossier = await get_project_dossier(project.id, test_workspace, db_session)
    assert len(dossier['bank_questions']) == 15
    assert len(dossier['suggested_questions']) == len(dossier['recommendations']) == 15
    assert len(dossier['technologies']) == 13
    assert dossier['practice_questions'] == [{
        'id': current_practice.id, 'short_code': current_practice.short_code, 'title': current_practice.title,
        'source_type': 'GENERATED', 'set_id': 'current-set', 'set_title': 'current-set',
        'import_id': local_import.id, 'source_item_id': local_item.id, 'original_title': local_item.title,
        'rewrite_method': 'model', 'source_revision': 'a' * 40,
    }]
    assert dossier['latest_run']['id'] == run.id and dossier['latest_run']['status'] == 'RUNNING'
    assert isinstance(dossier['worker']['online'], bool)
