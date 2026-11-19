"""One read-only shadow judgement on the isolated acceptance run. No score publication."""
import asyncio,json,sys
from pathlib import Path
from sqlalchemy import select
from app.models import Workspace, ScoringRun, ScoringStep
from app.db import SessionLocal,engine
from app.services.assessment_jobs import scoring_config,scoring_route
from app.services.assessment_worker import evaluate
from app.services.project_autopilot_config import runtime_config
from types import SimpleNamespace

async def main(out):
    out=Path(out); evidence=json.loads((out/'evidence.json').read_text(encoding='utf8'));assert evidence['workspace'].startswith('accept-coach-')
    async with SessionLocal() as db:
        ws=await db.scalar(select(Workspace).where(Workspace.slug==evidence['workspace']));run=await db.get(ScoringRun,evidence['submitted']['task_id']);assert run.workspace_id==ws.id
        step=await db.scalar(select(ScoringStep).where(ScoringStep.run_id==run.id));payload=step.input_json;assert payload['study_practice_id']==evidence['practice']['id'];base,_,_=await runtime_config(db,ws.id,False);route=scoring_route([(None,payload)]);assert route=='study_strong';cfg=scoring_config(base,route)
    try:
        result=await evaluate(SimpleNamespace(payload=payload),cfg);data=dict(mode='READ_ONLY_SHADOW',run_id=run.id,provider=cfg['provider'],requested_model=cfg['model'],route=route,points=result['points'],usage=result['usage'],request={k:v for k,v in result['request_meta'].items() if k in ('reported_model','elapsed_ms','latency_ms','duration_ms','prompt_version','status','request_id')})
    except Exception as e:
        data=dict(mode='READ_ONLY_SHADOW',run_id=run.id,provider=cfg['provider'],requested_model=cfg['model'],route=route,status='failed',category=getattr(e,'category',type(e).__name__))
    (out/'shadow.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(data,ensure_ascii=True));await engine.dispose();assert 'points' in data
if __name__=='__main__':asyncio.run(main(sys.argv[1]))
