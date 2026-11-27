"""HTTP contracts for local project automation; all data is an isolated fixture."""
import importlib.util
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_workspace
from app.db import get_session
from app.i18n import LanguageMiddleware
from app.models import AnalysisStep, AppSetting, MemoryItem, Project, ProjectAnalysis, Workspace
from app.services.project_autopilot_config import CONSENT_VERSION, DEFAULT_BUDGET
from app.services.project_jobs import utcnow


def test_report_changes_use_the_saved_locale_without_rewriting_originals():
    from app.api.v1.project_autopilot import normalized_report

    original = {'sections': [{'id': 'overview', 'title': '项目用途', 'body': '原始报告'}],
                'changes': {'changes': [{'status': 'modified', 'path': '源码/入口.py'}]}}
    frozen = deepcopy(original)
    assert normalized_report(original)['changes'] == ['修改：源码/入口.py']
    projected = normalized_report({**original, 'generation_language': 'en'})
    assert projected['changes'] == ['Modified 源码/入口.py']
    assert projected['sections'][0]['body'] == '原始报告'
    assert original == frozen


def api(session, workspace):
    assert importlib.util.find_spec('app.api.v1.project_autopilot'), 'Project Autopilot HTTP API is missing'
    from app.api.v1 import project_autopilot
    app = FastAPI()
    app.add_middleware(LanguageMiddleware)
    app.include_router(project_autopilot.router, prefix='/api/v1')
    async def db(): yield session
    async def ws(): return workspace
    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_workspace] = ws
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture/api/v1')


async def project(session, ws, **kw):
    row = Project(workspace_id=ws.id, name='API fixture', owner='fixture', repo='repo',
                  repo_url='https://github.com/fixture/repo', **kw)
    session.add(row); await session.commit()
    return row


async def consent(session, ws):
    session.add(AppSetting(key=f'project-autopilot:{ws.id}', value={'enabled':True,
        'consent_version':CONSENT_VERSION, 'consented_at':utcnow().isoformat(),
        'budget':{'max_model_calls':5,'max_source_bytes':10000}, 'check_interval_hours':6}))
    await session.commit()


@pytest.mark.asyncio
async def test_worker_handoff_stages_one_consented_scan_without_replacing_old_work(db_session, test_workspace):
    from app.services import project_jobs
    from app.services.project_prompts import PROMPT_BUNDLE_VERSION
    p = await project(db_session, test_workspace)
    old = await project_jobs.enqueue(db_session, p, 'existing', prompt_bundle_version='old')
    old.status = 'RUNNING'
    generation = p.generation
    db_session.add(AppSetting(key=project_jobs.HANDOFF_KEY, value={'target_prompt': PROMPT_BUNDLE_VERSION}))
    await db_session.commit()
    try:
        async with api(db_session, test_workspace) as client:
            for key in ('first', 'second'):
                response = await client.post(f'/projects/{p.id}/ai-scan', json={
                    'request_key': key, 'acknowledge_source_transfer': True})
                assert response.status_code == 202, response.text
                data = response.json()
                assert data['status'] == 'WAITING_WORKER' and data['allowed_actions'] == ['cancel']
            queued = (await db_session.scalars(select(ProjectAnalysis).where(
                ProjectAnalysis.project_id == p.id, ProjectAnalysis.status == 'WAITING_WORKER'))).one()
            assert queued.result_meta['source_transfer_consent'] == CONSENT_VERSION
            await db_session.refresh(p)
            await db_session.refresh(old)
            assert p.generation == generation and old.status == 'RUNNING'
            assert not p.pending_update
            await project_jobs.control_run(db_session, queued.id, test_workspace.id, 'cancel')
            assert queued.status == 'CANCELLED'
    finally:
        await db_session.execute(delete(AppSetting).where(AppSetting.key == project_jobs.HANDOFF_KEY))
        await db_session.commit()


@pytest.mark.asyncio
async def test_enable_requires_explicit_transfer_consent_and_queues_existing_repos(db_session, test_workspace):
    p = await project(db_session, test_workspace)
    await project(db_session, test_workspace, meta={'kind':'manual'})
    async with api(db_session,test_workspace) as client:
        blocked = await client.put('/project-autopilot/settings',json={'enabled':True,'budget':{'max_model_calls':5,'max_source_bytes':10000}})
        assert blocked.status_code in (400,422)
        assert await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(ProjectAnalysis.workspace_id==test_workspace.id)) == 0
        enabled = await client.put('/project-autopilot/settings',json={'enabled':True,'acknowledge_source_transfer':True,'budget':{'max_model_calls':5,'max_source_bytes':10000}})
        assert enabled.status_code==200, enabled.text
        assert enabled.json()['consented_at'] and enabled.json()['budget']['max_model_calls']==5
        runs=(await db_session.scalars(select(ProjectAnalysis).where(ProjectAnalysis.workspace_id==test_workspace.id))).all()
        assert len(runs)==1 and runs[0].project_id==p.id and runs[0].status=='QUEUED'
        disabled=await client.put('/project-autopilot/settings',json={'enabled':False})
        assert disabled.status_code==200
        await db_session.refresh(runs[0]);assert runs[0].status=='PAUSED_USER'


@pytest.mark.asyncio
@pytest.mark.parametrize(('headers', 'language'), [({'Accept-Language': 'en'}, 'en'), ({}, 'zh-CN')])
async def test_check_updates_only_marks_due_without_source_or_model_requests(db_session,test_workspace,monkeypatch,headers,language):
    from app.services import project_scheduler
    p=await project(db_session,test_workspace);await consent(db_session,test_workspace)
    p.next_check_at=utcnow()+timedelta(hours=6);await db_session.commit()
    async def forbidden(*args,**kwargs):raise AssertionError('HTTP route must not fetch repositories')
    with monkeypatch.context() as guard:
        guard.setattr(project_scheduler, 'check_project', forbidden)
        guard.setattr(project_scheduler, 'probe_repository', forbidden)
        async with api(db_session, test_workspace) as client:
            for _ in range(2):
                response = await client.post(f'/projects/{p.id}/check-updates', json={}, headers=headers)
                assert response.status_code == 202, response.text
            await db_session.refresh(p)
            assert p.next_check_at <= utcnow()
            assert await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(
                ProjectAnalysis.project_id == p.id)) == 0

    async def probe(*args, **kwargs):
        return {'target_sha': 'a' * 40, 'branch': 'main'}

    monkeypatch.setattr(project_scheduler, 'probe_repository', probe)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    await project_scheduler.tick(factory)
    await db_session.refresh(p)
    run = (await db_session.scalars(select(ProjectAnalysis).where(ProjectAnalysis.project_id == p.id))).one()
    assert run.result_meta['generation_language'] == language
    assert p.pending_update is None
    assert p.next_check_at > utcnow()

    async with api(db_session, test_workspace) as client:
        response = await client.post(f'/projects/{p.id}/check-updates', json={}, headers=headers)
        assert response.status_code == 202, response.text
    await project_scheduler.tick(factory)
    await db_session.refresh(p)
    assert p.pending_update is None
    assert await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(
        ProjectAnalysis.project_id == p.id)) == 1


@pytest.mark.asyncio
async def test_manual_check_keeps_pending_full_scan_and_its_request_key(db_session, test_workspace, monkeypatch):
    from app.services import project_jobs, project_scheduler

    p = await project(db_session, test_workspace)
    await consent(db_session, test_workspace)
    active = await project_jobs.enqueue(db_session, p, 'active', target_sha='a' * 40)
    await project_jobs.enqueue(db_session, p, 'pending-full')
    await db_session.commit()

    async def probe(*args, **kwargs):
        return {'target_sha': 'b' * 40, 'branch': 'main'}

    monkeypatch.setattr(project_scheduler, 'probe_repository', probe)
    async with api(db_session, test_workspace) as client:
        response = await client.post(f'/projects/{p.id}/check-updates', headers={'Accept-Language': 'en'})
        assert response.status_code == 202, response.text
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    await project_scheduler.tick(factory)
    await db_session.refresh(p)
    assert p.pending_update == {'mode': 'full', 'request_key': 'pending-full',
                                'generation_language': 'en', 'target_sha': 'b' * 40}
    await db_session.refresh(active)
    assert active.result_meta['generation_language'] == 'zh-CN'

    active.status = 'COMPLETED'
    p.next_check_at = utcnow()
    await db_session.commit()
    await project_scheduler.tick(factory)
    run = (await db_session.scalars(select(ProjectAnalysis).where(
        ProjectAnalysis.project_id == p.id, ProjectAnalysis.request_key == 'pending-full'))).one()
    assert run.mode == 'full' and run.target_sha == 'b' * 40
    assert run.result_meta['generation_language'] == 'en'


@pytest.mark.asyncio
async def test_full_reanalysis_is_idempotent_and_run_detail_never_exposes_raw_stage_payload(db_session,test_workspace):
    p=await project(db_session,test_workspace);await consent(db_session,test_workspace)
    async with api(db_session,test_workspace) as client:
        first=await client.post(f'/projects/{p.id}/reanalyze',json={'mode':'full','request_key':'fixture-retry'})
        assert first.status_code==202,first.text
        second=await client.post(f'/projects/{p.id}/reanalyze',json={'mode':'full','request_key':'fixture-retry'})
        assert second.json()['id']==first.json()['id']
        rid=first.json()['id']
        step=await db_session.scalar(select(AnalysisStep).where(AnalysisStep.run_id==rid))
        step.input_json={'secret':'fixture-must-not-leak'};step.output_json={'raw_source':'fixture-source-must-not-leak'};await db_session.commit()
        detail=await client.get(f'/analysis-runs/{rid}')
        assert detail.status_code==200 and detail.json()['steps']
        assert 'fixture-must-not-leak' not in detail.text and 'fixture-source-must-not-leak' not in detail.text
        assert (await client.post(f'/analysis-runs/{rid}/pause')).json()['status']=='PAUSED_USER'
        assert (await client.post(f'/analysis-runs/{rid}/resume')).json()['status']=='QUEUED'
        assert (await client.post(f'/analysis-runs/{rid}/cancel')).json()['status']=='CANCELLED'


@pytest.mark.asyncio
async def test_report_keeps_valid_history_separate_from_blocked_partial_run(db_session,test_workspace):
    p=await project(db_session,test_workspace)
    old=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='COMPLETED',report_status='VALID',coverage_status='COMPLETE',source_revision='a'*40,report={'summary':'Valid older report','architecture':[],'issues':[],'unknowns':[]})
    db_session.add(old);await db_session.flush();p.latest_valid_analysis_id=old.id;p.last_seen_sha='b'*40
    latest=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='BLOCKED',pipeline_version='autopilot-v1',coverage_status='PARTIAL',error='模型超时，进度保留')
    db_session.add(latest);await db_session.commit()
    async with api(db_session,test_workspace) as client:
        result=await client.get(f'/projects/{p.id}/report')
        assert result.status_code==200,result.text
        value=result.json();assert value['latest_run']['id']==latest.id
        assert value['latest_run']['coverage_status']=='PARTIAL' and value['report']['id']==old.id
        assert value['report']['freshness']=='NEEDS_REVIEW'
        assert value['report']['active_issue_ids']==[]


@pytest.mark.asyncio
async def test_candidate_review_is_versioned_and_unknown_ids_do_not_activate(db_session,test_workspace):
    p=await project(db_session,test_workspace)
    run=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='COMPLETED',report_status='VALID',coverage_status='COMPLETE')
    db_session.add(run);await db_session.flush();p.latest_valid_analysis_id=run.id
    candidate=MemoryItem(workspace_id=test_workspace.id,mtype='ProjectIssue',content='Fixture issue',source='SYSTEM',status='PENDING_REVIEW',freshness='CURRENT',revision=2,meta={'project_id':p.id,'analysis_id':run.id})
    db_session.add(candidate);await db_session.commit()
    async with api(db_session,test_workspace) as client:
        listed=await client.get(f'/projects/{p.id}/memory-candidates');assert listed.status_code==200
        assert listed.json()['items'][0]['revision']==2
        conflict=await client.post('/memory-candidates/review',json={'items':[{'candidate_id':candidate.id,'expected_revision':1,'action':'confirm'}]})
        assert conflict.json()['results'][0]['status']=='CONFLICT'
        await db_session.refresh(candidate);assert candidate.status=='PENDING_REVIEW'
        confirmed=await client.post('/memory-candidates/review',json={'items':[{'candidate_id':candidate.id,'expected_revision':2,'action':'confirm','edited_content':'User confirmed correction'}]})
        assert confirmed.json()['results'][0]['status']=='CONFIRMED'
        await db_session.refresh(candidate);assert candidate.content=='User confirmed correction' and candidate.confirmed_at
        assert (await client.get('/analysis-runs/2147483647')).status_code==404
        assert (await client.post('/projects/2147483647/reanalyze',json={'mode':'full'})).status_code==404


@pytest.mark.asyncio
async def test_saved_module_is_visible_as_partial_before_final_report_exists(db_session,test_workspace):
    p=await project(db_session,test_workspace)
    run=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='RUNNING',pipeline_version='autopilot-v1')
    db_session.add(run);await db_session.flush()
    db_session.add(AnalysisStep(run_id=run.id,workspace_id=test_workspace.id,project_id=p.id,step_key='module:one',stage='module',module_key='api',input_hash='fixture',status='SUCCEEDED',output_json={'claims':[{'text':'Saved module observation'}]}))
    await db_session.commit()
    async with api(db_session,test_workspace) as client:
        value=(await client.get(f'/projects/{p.id}/report')).json()
        assert value['latest_run']['coverage_status']=='PARTIAL'
        assert value['report'] is None
        assert 'Saved module observation' in str(value['partial_preview'])


@pytest.mark.asyncio
async def test_editing_enabled_budget_only_updates_future_scan_defaults(db_session, test_workspace):
    started = await project(db_session, test_workspace)
    await project(db_session, test_workspace)
    await consent(db_session, test_workspace)
    async with api(db_session, test_workspace) as client:
        one = (await client.post(f'/projects/{started.id}/ai-scan', json={})).json()
        saved = await client.put('/project-autopilot/settings', json={'enabled':True,
            'acknowledge_source_transfer':True, 'budget':{}})
        assert saved.status_code == 200 and saved.json()['budget'] == DEFAULT_BUDGET
        rows = (await db_session.scalars(select(ProjectAnalysis).where(
            ProjectAnalysis.workspace_id == test_workspace.id))).all()
        assert len(rows) == 1 and rows[0].id == one['id']
        assert rows[0].budget == {'max_model_calls':5,'max_source_bytes':10000}


@pytest.mark.asyncio
async def test_budget_resume_uses_explicitly_updated_budget_without_changing_model_fingerprint(db_session,test_workspace):
    p=await project(db_session,test_workspace);await consent(db_session,test_workspace)
    async with api(db_session,test_workspace) as client:
        value=(await client.post(f'/projects/{p.id}/reanalyze',json={'mode':'full'})).json()
        run=await db_session.get(ProjectAnalysis,value['id']);fingerprint=run.model_config_fingerprint
        run.status='PAUSED_BUDGET';run.usage={'model_calls':5};await db_session.commit()
        assert (await client.post(f'/analysis-runs/{run.id}/resume')).status_code==409
        saved=await client.put('/project-autopilot/settings',json={'enabled':True,'acknowledge_source_transfer':True,'budget':{'max_model_calls':8,'max_source_bytes':10000}})
        assert saved.status_code==200
        resumed=await client.post(f'/analysis-runs/{run.id}/resume')
        assert resumed.status_code==200,resumed.text
        await db_session.refresh(run);assert run.budget['max_model_calls']==8 and run.model_config_fingerprint==fingerprint


@pytest.mark.asyncio
async def test_all_report_run_and_candidate_paths_reject_another_workspace(db_session,test_workspace):
    p=await project(db_session,test_workspace)
    run=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='QUEUED',pipeline_version='autopilot-v1')
    db_session.add(run);await db_session.flush()
    candidate=MemoryItem(workspace_id=test_workspace.id,mtype='ProjectIssue',content='Private fixture observation',source='SYSTEM',status='PENDING_REVIEW',meta={'project_id':p.id,'analysis_id':run.id})
    other=Workspace(slug='other-'+uuid4().hex,name='Other isolated fixture')
    db_session.add_all([candidate,other]);await db_session.commit()
    try:
        async with api(db_session,other) as client:
            for path in (f'/projects/{p.id}/report',f'/projects/{p.id}/memory-candidates',f'/analysis-runs/{run.id}'):
                assert (await client.get(path)).status_code==404,path
            for path in (f'/projects/{p.id}/check-updates',f'/projects/{p.id}/reanalyze',f'/analysis-runs/{run.id}/cancel'):
                assert (await client.post(path,json={})).status_code==404,path
            reviewed=await client.post('/memory-candidates/review',json={'items':[{'candidate_id':candidate.id,'expected_revision':1,'action':'confirm'}]})
            assert reviewed.json()['results'][0]['status']=='NOT_FOUND'
            assert (await client.get('/memory-candidates')).json()['items']==[]
        await db_session.refresh(candidate);assert candidate.status=='PENDING_REVIEW'
    finally:
        await db_session.execute(delete(Workspace).where(Workspace.id==other.id));await db_session.commit()


@pytest.mark.asyncio
async def test_report_response_normalizes_chapters_without_rewriting_frozen_report(db_session,test_workspace):
    p=await project(db_session,test_workspace,last_seen_sha='a'*40)
    raw={'summary':'Summary','architecture':[],'issues':[],'unknowns':[],
         'sections':[{'id':'purpose','title':'用途','paragraphs':[{'text':'Real paragraph','claim_ids':['C1']}]}],
         'claims':[{'id':'C1','text':'Claim','citations':[]}],
         'changes':{'complete':True,'changes':[{'status':'added','path':'app.py'}]}}
    run=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='COMPLETED',report_status='VALID',coverage_status='COMPLETE',source_revision='a'*40,report=raw)
    db_session.add(run);await db_session.flush();p.latest_valid_analysis_id=run.id;await db_session.commit()
    async with api(db_session,test_workspace) as client:
        report=(await client.get(f'/projects/{p.id}/report')).json()['report']['report']
        assert report['sections'][0]['body']=='Real paragraph' and report['sections'][0]['claim_ids']==['C1']
        assert report['changes']==['新增：app.py']
        await db_session.refresh(run);assert run.report==raw


@pytest.mark.asyncio
async def test_removed_project_report_returns_scoped_readonly_identity_and_no_allowed_writes(db_session,test_workspace):
    p=await project(db_session,test_workspace,archived_at=utcnow())
    async with api(db_session,test_workspace) as client:
        response=await client.get(f'/projects/{p.id}/report')
        assert response.status_code==200
        assert response.json()['project']['id']==p.id and response.json()['project']['archived_at']
        assert response.json()['allowed_actions']==[]
        assert (await client.post(f'/projects/{p.id}/reanalyze',json={'mode':'full'})).status_code==404
        assert (await client.get(f'/projects/{p.id}/memory-candidates')).status_code==404


@pytest.mark.asyncio
async def test_excerpt_identity_keeps_distinct_source_versions_at_the_same_path(db_session,test_workspace,monkeypatch):
    from app.api.v1 import project_analysis
    p=await project(db_session,test_workspace,last_seen_sha='b'*40)
    run=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='COMPLETED',report_status='VALID',coverage_status='COMPLETE',source_revision='b'*40,report={'summary':'Two sources','architecture':[],'issues':[],'unknowns':[]})
    db_session.add(run);await db_session.flush();p.latest_valid_analysis_id=run.id;await db_session.commit()
    excerpts=[{'path':'app.py','start_line':1,'end_line':2,'source_id':'source-'+version,'source_version':version,'excerpt':'version '+version,'excerpt_truncated':False} for version in ('a','b')]
    async def detail(*args,**kwargs):return {'source_excerpts':excerpts}
    monkeypatch.setattr(project_analysis,'get_analysis',detail)
    async with api(db_session,test_workspace) as client:
        value=(await client.get(f'/projects/{p.id}/report')).json()
        assert len(value['report']['source_excerpts'])==2


@pytest.mark.asyncio
async def test_run_details_include_safe_trace_and_attempt_metadata(db_session,test_workspace):
    p=await project(db_session,test_workspace)
    run=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='QUEUED',pipeline_version='autopilot-v1',result_meta={'trace_id':712})
    db_session.add(run);await db_session.flush()
    step=AnalysisStep(run_id=run.id,workspace_id=test_workspace.id,project_id=p.id,step_key='plan',stage='plan_modules',input_hash='safe-input-hash',input_json={'private':'hidden source'},output_json={'private':'hidden output'},usage={'known':False},request_meta={'request_id':'provider-request'},attempts=[{'outcome':'OUTCOME_UNKNOWN'}])
    db_session.add(step);await db_session.commit()
    async with api(db_session,test_workspace) as client:
        response=await client.get(f'/analysis-runs/{run.id}')
        value=response.json();assert value['trace_id']==712
        shown=value['steps'][0];assert shown['id']==step.id and shown['run_id']==run.id
        assert shown['usage']=={'known':False} and shown['request_meta']['request_id']=='provider-request'
        assert shown['attempts'][0]['outcome']=='OUTCOME_UNKNOWN' and shown['input_hash']=='safe-input-hash'
        assert 'hidden source' not in response.text and 'hidden output' not in response.text


@pytest.mark.asyncio
async def test_first_enable_resumes_same_config_consent_blocked_run_without_a_second_full_request(db_session,test_workspace):
    from app.services.project_autopilot_config import model_fingerprint, runtime_config
    from app.services.project_scheduler import versions
    p=await project(db_session,test_workspace)
    config,_,_=await runtime_config(db_session,test_workspace.id,require_consent=False)
    pipeline,prompt=versions()
    run=ProjectAnalysis(workspace_id=test_workspace.id,project_id=p.id,request_key=uuid4().hex,status='BLOCKED',pipeline_version=pipeline,prompt_bundle_version=prompt,model_config_fingerprint=model_fingerprint(config))
    db_session.add(run);await db_session.flush()
    db_session.add(AnalysisStep(run_id=run.id,workspace_id=test_workspace.id,project_id=p.id,step_key='resolve',stage='resolve',input_hash='fixture',status='BLOCKED',error_category='CONSENT_REQUIRED'))
    await db_session.commit()
    async with api(db_session,test_workspace) as client:
        response=await client.put('/project-autopilot/settings',json={'enabled':True,'acknowledge_source_transfer':True,'budget':{'max_model_calls':5,'max_source_bytes':10000}})
        assert response.status_code==200,response.text
        await db_session.refresh(run);await db_session.refresh(p)
        assert run.status=='QUEUED' and p.pending_update is None
        assert await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(ProjectAnalysis.project_id==p.id))==1


@pytest.mark.asyncio
async def test_single_scan_consent_has_no_workspace_side_effects(db_session, test_workspace):
    p = await project(db_session, test_workspace)
    await project(db_session, test_workspace)
    async with api(db_session, test_workspace) as client:
        settings = (await client.get('/project-autopilot/settings')).json()
        assert settings['budget'] == DEFAULT_BUDGET and not settings['enabled']
        path = f'/projects/{p.id}/ai-scan'
        assert (await client.post(path, json={})).status_code == 409
        first = await client.post(path, json={'acknowledge_source_transfer': True})
        assert first.status_code == 202, first.text
        duplicate = await client.post(path, json={'acknowledge_source_transfer': True})
        assert duplicate.json()['id'] == first.json()['id']
        await client.put('/project-autopilot/settings', json={'enabled':False, 'budget':{'max_model_calls':3}})
        saved = await client.get(f"/analysis-runs/{first.json()['id']}")
        assert saved.json()['status'] == 'QUEUED' and saved.json()['budget'] == DEFAULT_BUDGET
        assert not (await client.get('/project-autopilot/settings')).json()['enabled']
    runs = (await db_session.scalars(select(ProjectAnalysis).where(ProjectAnalysis.workspace_id == test_workspace.id))).all()
    assert len(runs) == 1 and runs[0].budget == DEFAULT_BUDGET
    assert runs[0].result_meta['source_transfer_consent'] == CONSENT_VERSION
    await db_session.refresh(p)
    assert p.pending_update is None


@pytest.mark.asyncio
async def test_explicit_scan_replaces_incompatible_open_run(db_session, test_workspace):
    p = await project(db_session, test_workspace)
    async with api(db_session, test_workspace) as client:
        url = f'/projects/{p.id}/ai-scan'
        first = (await client.post(url, json={'acknowledge_source_transfer': True})).json()
        old = await db_session.get(ProjectAnalysis, first['id'])
        old.prompt_bundle_version = 'project-autopilot-prompts-v2'
        await db_session.commit()
        new = await client.post(url, json={'acknowledge_source_transfer': True})
        assert new.status_code == 202 and new.json()['id'] != old.id
        await db_session.refresh(old)
        assert old.status == 'CANCELLED'
