"""Read only the synthetic workspace's persisted outcome. No credentials/raw provider payloads."""
import asyncio,json,sys
from pathlib import Path
from sqlalchemy import select
from app.db import SessionLocal,engine
from app.models import Workspace, ScoringRun, ScoringStep, Evidence, MemoryItem
from app.models.learning_feedback import LearningSubmission
from app.services.study_practice import owned_practice,retest_result

async def main(out):
    out=Path(out); j=json.loads((out/'evidence.json').read_text(encoding='utf8'));assert j['workspace'].startswith('accept-coach-')
    async with SessionLocal() as db:
        ws=await db.scalar(select(Workspace).where(Workspace.slug==j['workspace'])); practice=await owned_practice(db,ws.id,j['practice']['id']); retest=await retest_result(db,ws,practice); submissions=(await db.scalars(select(LearningSubmission).where(LearningSubmission.workspace_id==ws.id,LearningSubmission.activity_id==practice.id).order_by(LearningSubmission.revision))).all()
        runs=[]
        for rid in [*[s.scoring_run_id for s in submissions],retest['scoring_run_id']]:
            run=await db.get(ScoringRun,rid); assert run.workspace_id==ws.id
            step=await db.scalar(select(ScoringStep).where(ScoringStep.run_id==rid)); result=step.result_json or {}; attempts=step.attempts or []; runs.append(dict(id=rid,status=run.status,mode=run.mode,requested_model=run.meta.get('model'),route=run.meta.get('scoring_route'),answer_revision_id=step.input_json['answer_revision_id'],points=result.get('points'),max_points=result.get('max_points'),criterion_scores=[{k:c.get(k) for k in ('score','reasoning_summary','answer_quotes')} for c in result.get('criterion_scores',[])],attempts=[dict(model=a.get('request_meta',{}).get('reported_model'),usage=a.get('usage'),latency=a.get('request_meta',{}).get('elapsed_ms')) for a in attempts]))
        evid=list(await db.scalars(select(Evidence.id).where(Evidence.workspace_id==ws.id))); memories=[dict(id=m.id,status=m.status) for m in await db.scalars(select(MemoryItem).where(MemoryItem.workspace_id==ws.id))]
        data=dict(workspace=ws.id,session=j['session']['id'],note=j['note'],card=j['activated']['id'],practice=practice.id,submissions=[dict(id=s.id,revision=s.revision,original=s.payload.get('text') or s.payload.get('answer') or s.payload.get('text_answer'),assistance=s.assistance,status=s.status) for s in submissions],runs=runs,retest=retest,evidence_ids=evid,memory_statuses=memories);(out/'database.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf8');print(json.dumps({'ids':evid,'run_ids':[r['id'] for r in runs],'models':[r['requested_model'] for r in runs],'scores':[r['points'] for r in runs],'memory':memories},ensure_ascii=True))
        assert len(submissions)==2 and submissions[1].assistance['hint_used'];assert retest['status']=='COMPLETED' and len(retest['evidence_ids'])==1 and evid==retest['evidence_ids'];assert retest['competency_snapshot_id'];assert all(m['status']!='ACTIVE' for m in memories)
    await engine.dispose()
if __name__=='__main__':asyncio.run(main(sys.argv[1]))
