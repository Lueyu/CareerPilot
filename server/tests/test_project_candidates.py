import importlib.util

import pytest
from sqlalchemy import func, select

from app.models import Evidence, Project, ProjectAnalysis
from app.services.project_memory import active_project_memories


async def prepared(session,ws):
    assert importlib.util.find_spec('app.services.project_candidates'), 'candidate confirmation service missing'
    from app.services import project_candidates as service
    p=Project(workspace_id=ws.id,name='Candidate fixture',owner='fixture',repo='repo',repo_url='https://github.com/fixture/repo')
    session.add(p);await session.flush()
    run=ProjectAnalysis(project_id=p.id,workspace_id=ws.id,request_key='candidate',pipeline_version='autopilot-v1',
        status='COMPLETED',report_status='VALID',coverage_status='COMPLETE',source_revision='a'*40,
        report={'claims':[{'id':'c1','citations':[{'path':'app.py','start_line':1,'end_line':1}]}],
                'issues':[{'id':'i1','skill_key':'rag','title':'Missing guard'}]},
        snapshot={'files':[{'path':'app.py','content':'load()'}], 'manifest':{'files':[{'path':'app.py','sha':'blob-a'}]}})
    session.add(run);await session.flush();p.latest_valid_analysis_id=run.id
    await session.commit()
    return service,p,run

def proposal(content='Project lacks input validation'):
    return {'fact_key':'guard','kind':'issue','mtype':'ProjectIssue','content':content,
            'claim_ids':['c1'],'issue_id':'i1','fact_type':'CODE_OBSERVED'}

@pytest.mark.asyncio
async def test_candidates_do_not_activate_or_create_ability_evidence(db_session,test_workspace):
    s,p,r=await prepared(db_session,test_workspace)
    rows=await s.publish_candidates(db_session,r,[proposal()]);await db_session.commit()
    assert rows[0].status=='PENDING_REVIEW'
    assert not await active_project_memories(db_session,test_workspace.id,p.id)
    assert await db_session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id==test_workspace.id))==0
    result=await s.review_candidates(db_session,test_workspace.id,[{'candidate_id':rows[0].id,'expected_revision':1,'action':'confirm','edited_content':'User checked guard risk'}])
    assert result[0]['status']=='CONFIRMED'
    active=await active_project_memories(db_session,test_workspace.id,p.id)
    assert len(active)==1 and active[0].content=='User checked guard risk' and active[0].source=='USER'

@pytest.mark.asyncio
async def test_batch_revision_conflicts_do_not_overwrite_and_are_scoped(db_session,test_workspace):
    s,p,r=await prepared(db_session,test_workspace)
    rows=await s.publish_candidates(db_session,r,[proposal()]);await db_session.commit()
    result=await s.review_candidates(db_session,test_workspace.id,[{'candidate_id':rows[0].id,'expected_revision':9,'action':'confirm'},
        {'candidate_id':rows[0].id+1000,'expected_revision':1,'action':'confirm'}])
    assert [x['status'] for x in result]==['CONFLICT','NOT_FOUND']
    assert not await active_project_memories(db_session,test_workspace.id,p.id)

@pytest.mark.asyncio
async def test_ignored_fact_suppression_and_partial_report_cannot_publish(db_session,test_workspace):
    s,p,r=await prepared(db_session,test_workspace)
    rows=await s.publish_candidates(db_session,r,[proposal()]);await db_session.commit()
    await s.review_candidates(db_session,test_workspace.id,[{'candidate_id':rows[0].id,'expected_revision':1,'action':'ignore'}])
    assert not await s.publish_candidates(db_session,r,[proposal()])
    r.coverage_status='PARTIAL';await db_session.commit()
    with pytest.raises(ValueError):await s.publish_candidates(db_session,r,[proposal('new')])

@pytest.mark.asyncio
async def test_changed_source_marks_confirmed_memory_stale_preserving_user_text(db_session,test_workspace):
    s,p,r=await prepared(db_session,test_workspace)
    rows=await s.publish_candidates(db_session,r,[proposal()]);await db_session.commit()
    await s.review_candidates(db_session,test_workspace.id,[{'candidate_id':rows[0].id,'expected_revision':1,'action':'confirm','edited_content':'My correction'}])
    await s.invalidate_changed_sources(db_session,p,{'app.py'},'b'*40);await db_session.commit()
    await db_session.refresh(rows[0])
    assert rows[0].content=='My correction' and rows[0].freshness=='NEEDS_REVIEW' and rows[0].status=='ACTIVE'
    assert not await active_project_memories(db_session,test_workspace.id,p.id)
