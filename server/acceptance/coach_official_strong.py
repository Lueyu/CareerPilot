"""A fresh real official run via installed API and production worker; all writes are scoped to the acceptance fixture."""
import asyncio,json,re,sys,time
from pathlib import Path
from sqlalchemy import select,func
import httpx
from app.config import get_settings
from app.db import SessionLocal,engine
from app.models import Workspace,ScoringRun,ScoringStep,Evidence,CompetencySnapshot

async def main(out):
    out=Path(out); fixture=json.loads((out/'evidence.json').read_text(encoding='utf8')); slug=fixture['workspace'];assert slug.startswith('accept-coach-');path=out/'official-strong.json';record=json.loads(path.read_text(encoding='utf8')) if path.exists() else dict(workspace=slug); save=lambda:path.write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str),encoding='utf8')
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8002/api/v1',headers={'X-Workspace':slug,'X-Api-Token':get_settings().api_token},timeout=45,trust_env=False) as client:
        async def req(method, endpoint, body=None):
            res=await client.request(method,endpoint,json=body);res.raise_for_status();return res.json()
        practice=await req('POST',f"/study/sessions/{fixture['session']['id']}/practices",{'kind':'quiz','card_id':fixture['activated']['id'],'request_key':'official-strong-practice-v1'});pid=practice['id'];record['practice_id']=pid;save()
        await req('POST',f'/study/practices/{pid}/answers',{'text_answer':'精确率看返回集合内相关项的占比；召回率看标注相关项被命中的比例。','request_key':'official-strong-first-v1'})
        exam=await req('POST',f'/study/practices/{pid}/retest');eid=exam['id'];assert eid!=fixture['retest']['id'];q=exam['questions'][0]['title'];assert q!=fixture['retest']['questions'][0]['title'];record['exam_id']=eid;record['question']=q;save();m=re.search(r'返回 (\d+) 条，其中 (\d+) 条相关，标注共有 (\d+) 条相关',q);assert m,q;k,h,t=map(int,m.groups());ans=f'precision@{k} = {h}/{k} = {h/k:.3f}；分母是返回条数，分子是其中实际相关数。recall@{k} = {h}/{t} = {h/t:.3f}；分母是标注的全部相关条数。精度关注返回结果的纯度，召回关注标注集合的覆盖。'
        if not record.get('task_id'):
            await req('POST',f'/exams/{eid}/answers',{'order_no':1,'text_answer':ans}); ack=await req('POST',f'/exams/{eid}/submit',{});record['task_id']=ack['task_id'];save();print('submitted',record['task_id'],flush=True)
        task=record['task_id']; until=time.monotonic()+320
        while time.monotonic()<until:
            result=await req('GET',f'/study/practices/{pid}/retest');record['publication']=result;save()
            if result['status']=='COMPLETED' or result['status'] in ('SCORING_FAILED','NEEDS_REVIEW','ABORTED'):break
            await asyncio.sleep(2)
        async with SessionLocal() as db:
            ws=await db.scalar(select(Workspace).where(Workspace.slug==slug));run=await db.get(ScoringRun,task);assert run.workspace_id==ws.id;step=await db.scalar(select(ScoringStep).where(ScoringStep.run_id==task));record['run']=dict(status=run.status,mode=run.mode,requested_model=run.meta.get('model'),provider=run.meta.get('provider'),route=run.meta.get('scoring_route'),fingerprint=run.model_fingerprint,answer_revision_id=step.input_json['answer_revision_id'],attempts=[dict(outcome=a['outcome'],usage=a.get('usage'),metadata={k:v for k,v in a.get('request_meta',{}).items() if k in ('status','request_id','reported_model','elapsed_ms','prompt_version')}) for a in step.attempts]);save()
            assert record['run']['route']=='study_strong' and run.status=='COMPLETED' and run.mode=='OFFICIAL',record['run'];assert len(result['evidence_ids'])==1 and result['competency_snapshot_id'];count=await db.scalar(select(func.count()).select_from(CompetencySnapshot).where(CompetencySnapshot.workspace_id==ws.id,CompetencySnapshot.trigger_ref==f'scoring:{task}'));assert count==1; assert (await req('POST',f'/study/practices/{pid}/retest'))['id']==eid;again=await req('GET',f'/study/practices/{pid}/retest');assert again['evidence_ids']==result['evidence_ids'] and again['competency_snapshot_id']==result['competency_snapshot_id'];record['repeated_issue_same_exam']=True;record['competency_snapshot_count']=count;save();print(json.dumps(record['run'],ensure_ascii=True),flush=True)
    await engine.dispose()
if __name__=='__main__':asyncio.run(main(sys.argv[1]))
