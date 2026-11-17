import pytest
from sqlalchemy import select
from app.models import MemoryItem
from test_project_candidates import prepared,proposal

@pytest.mark.asyncio
async def test_competing_same_fact_candidates_cannot_both_become_active(db_session,test_workspace):
    service,project,run=await prepared(db_session,test_workspace)
    rows=await service.publish_candidates(db_session,run,[proposal('First interpretation'),proposal('Competing interpretation')])
    await db_session.commit()
    assert len(rows)==2  # retain both proposals for review; never silently discard one
    reviewed=await service.review_candidates(db_session,test_workspace.id,[
        {'candidate_id':row.id,'expected_revision':row.revision,'action':'confirm'} for row in rows])
    assert [r['status'] for r in reviewed]==['CONFIRMED','CONFLICT']
    active=(await db_session.scalars(select(MemoryItem).where(MemoryItem.workspace_id==test_workspace.id,MemoryItem.status=='ACTIVE'))).all()
    assert len(active)==1

@pytest.mark.asyncio
async def test_obsolete_base_blocks_confirmation_but_not_ignoring_candidate(db_session,test_workspace):
    service,project,run=await prepared(db_session,test_workspace)
    original=(await service.publish_candidates(db_session,run,[proposal('Old observation')]))[0]
    await db_session.commit()
    await service.review_candidates(db_session,test_workspace.id,[
        {'candidate_id':original.id,'expected_revision':original.revision,'action':'confirm'}])
    candidate=(await service.publish_candidates(db_session,run,[proposal('New interpretation')]))[0]
    await db_session.commit()
    original.status='SUPERSEDED'
    original.revision+=1
    await db_session.commit()
    item={'candidate_id':candidate.id,'expected_revision':candidate.revision}
    rejected=await service.review_candidates(db_session,test_workspace.id,[{**item,'action':'confirm'}])
    assert rejected[0]['status']=='CONFLICT'
    ignored=await service.review_candidates(db_session,test_workspace.id,[{**item,'action':'ignore'}])
    assert ignored[0]['status']=='IGNORED'
    await db_session.refresh(original)
    assert original.status=='SUPERSEDED' and original.content=='Old observation'
