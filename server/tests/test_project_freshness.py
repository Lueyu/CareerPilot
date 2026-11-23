import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_project_candidates import prepared, proposal

from app.models import AnalysisStep
from app.project_worker import reconcile
from app.services import project_jobs


@pytest.mark.asyncio
async def test_changed_manifest_invalidates_memory_before_analysis_completes(db_session,test_workspace,monkeypatch):
    service,p,old=await prepared(db_session,test_workspace)
    rows=await service.publish_candidates(db_session,old,[proposal()]);await db_session.commit()
    await service.review_candidates(db_session,test_workspace.id,[{'candidate_id':rows[0].id,'expected_revision':1,'action':'confirm'}])
    run=await project_jobs.enqueue(db_session,p,'changed',mode='incremental',target_sha='b'*40)
    await project_jobs.ensure_steps(db_session,run,[{'key':'manifest','stage':'manifest','requires':[],'payload':{}}])
    await db_session.commit()
    manifest=await db_session.scalar(select(AnalysisStep).where(AnalysisStep.run_id==run.id,AnalysisStep.step_key=='manifest'))
    from app.services.project_autopilot_engine import (
        PIPELINE_VERSION,
        PROMPT_BUNDLE_VERSION,
        SAFETY_POLICY_VERSION,
        SCHEMA_VERSION,
    )
    manifest.status='SUCCEEDED';manifest.output_json={'diff':{'complete':True,'changes':[{'status':'modified','path':'app.py'}]}}
    manifest.output_json={**manifest.output_json,'provenance':{'pipeline_version':PIPELINE_VERSION,'prompt_bundle_version':PROMPT_BUNDLE_VERSION,'schema_version':SCHEMA_VERSION,'safety_policy_version':SAFETY_POLICY_VERSION}}
    await db_session.commit()
    from app.services import project_autopilot_engine
    monkeypatch.setattr(project_autopilot_engine,'plan_steps',lambda *_:[])
    await reconcile(async_sessionmaker(db_session.bind,expire_on_commit=False))
    await db_session.refresh(rows[0]);await db_session.refresh(run)
    assert run.report is None and rows[0].freshness=='NEEDS_REVIEW'
