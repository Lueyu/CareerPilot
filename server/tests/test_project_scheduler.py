import importlib.util

import pytest
from sqlalchemy import func, select

from app.models import AppSetting, Project, ProjectAnalysis
from app.services.project_autopilot_config import CONSENT_VERSION


async def setup(session,ws):
    assert importlib.util.find_spec('app.services.project_scheduler'), 'durable scheduler missing'
    from app.services import project_scheduler as s
    session.add(AppSetting(key=f'project-autopilot:{ws.id}',value={'enabled':True,'consent_version':CONSENT_VERSION,'budget':{'max_model_calls':5}}))
    p=Project(workspace_id=ws.id,name='scheduler',owner='fixture',repo='repo',branch='main',repo_url='https://github.com/fixture/repo')
    session.add(p);await session.commit()
    return s,p

@pytest.mark.asyncio
async def test_unchanged_probe_does_not_enqueue_or_call_model(db_session,test_workspace,monkeypatch):
    s,p=await setup(db_session,test_workspace)
    async def probe(*args,**kwargs):return {'target_sha':'a'*40,'branch':'main'}
    monkeypatch.setattr(s,'probe_repository',probe)
    first=await s.check_project(db_session,p.id,test_workspace.id)
    run=await db_session.get(ProjectAnalysis,first['run_id']);run.status='COMPLETED';run.report_status='VALID';run.coverage_status='COMPLETE';run.source_revision='a'*40
    from app.services.project_autopilot_engine import SAFETY_POLICY_VERSION, SCHEMA_VERSION
    run.snapshot={'provenance':{'schema_version':SCHEMA_VERSION,'safety_policy_version':SAFETY_POLICY_VERSION}}
    run.prompt_bundle_version = 'project-autopilot-prompts-v2'
    run.model_config_fingerprint = 'old-routing-fingerprint'
    p.latest_valid_analysis_id=run.id;p.last_seen_sha='a'*40;await db_session.commit()
    second=await s.check_project(db_session,p.id,test_workspace.id)
    assert second['status']=='UNCHANGED'
    assert await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(ProjectAnalysis.project_id==p.id))==1

@pytest.mark.asyncio
async def test_running_updates_coalesce_and_missing_consent_never_calls_source(db_session,test_workspace,monkeypatch):
    s,p=await setup(db_session,test_workspace)
    sha=['a'*40]
    async def probe(*args,**kwargs):return {'target_sha':sha[0],'branch':'main'}
    monkeypatch.setattr(s,'probe_repository',probe)
    await s.check_project(db_session,p.id,test_workspace.id)
    for letter in ('b','c','d'):
        sha[0]=letter*40;await s.check_project(db_session,p.id,test_workspace.id)
    await db_session.refresh(p)
    assert p.pending_update['target_sha']=='d'*40
    assert await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(ProjectAnalysis.project_id==p.id))==1
    setting=await db_session.get(AppSetting,f'project-autopilot:{test_workspace.id}')
    setting.value={'enabled':False};await db_session.commit()
    async def forbidden(*args,**kwargs):raise AssertionError('must not fetch')
    monkeypatch.setattr(s,'probe_repository',forbidden)
    assert (await s.check_project(db_session,p.id,test_workspace.id))['status']=='CONSENT_REQUIRED'

@pytest.mark.asyncio
async def test_changed_safety_policy_requires_analysis_even_when_commit_is_unchanged(db_session,test_workspace,monkeypatch):
    s,p=await setup(db_session,test_workspace)
    async def probe(*args,**kwargs):return {'target_sha':'a'*40,'branch':'main'}
    monkeypatch.setattr(s,'probe_repository',probe)
    first=await s.check_project(db_session,p.id,test_workspace.id)
    run=await db_session.get(ProjectAnalysis,first['run_id'])
    run.status='COMPLETED';run.report_status='VALID';run.coverage_status='COMPLETE';run.source_revision='a'*40
    run.snapshot={'provenance':{'schema_version':2,'safety_policy_version':'old-policy'}}
    p.latest_valid_analysis_id=run.id;p.last_seen_sha=run.source_revision;await db_session.commit()
    again=await s.check_project(db_session,p.id,test_workspace.id)
    assert again['run_id'] and again['run_id']!=first['run_id']
