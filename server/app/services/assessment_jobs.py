"""Durable grading claims. LLM work must happen after these transactions close."""
import random
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.models import AnalysisStep, Exam, ExamAnswer, ScoringRun, ScoringStep, Workspace
from app.services.assessment_rubric import JUDGE_VERSION
from app.services.assessment_versions import digest, ensure_revision, question_snapshot
from app.services.bank_practice import is_bank_practice_snapshot
from app.services.llm_judge import JUDGE_PROMPT_VERSION
from app.services.project_autopilot_config import model_fingerprint, runtime_config
from app.services.tracing import emit_event, start_trace

ACTIVE=('QUEUED','RUNNING','RETRY_WAIT')
TERMINAL=('COMPLETED','CANCELLED','FAILED','NEEDS_REVIEW')

def now():return datetime.now(timezone.utc)


def scoring_route(inputs):
    # Only a held-out open retest in this flow can publish new competency evidence.
    return 'study_strong' if any(p.get('study_practice_id') and p.get('qtype') == 'open'
                                 for _, p in inputs) else 'default'


def scoring_config(base, route):
    if route != 'study_strong':
        return base
    from app.services.study_routing import profile
    # A missing deep model fails through the normal NOT_CONFIGURED worker path.
    # No silent substitution of a lower-quality judge on an evidence-producing run.
    return profile(base, 'strong') or {'provider': '', 'model': '', 'base_url': '', 'api_key': ''}


async def enqueue(session, ws, exam, *, mode='OFFICIAL'):
    if exam.workspace_id!=ws.id:raise ValueError('考试不属于当前工作空间')
    if mode not in ('OFFICIAL', 'PRACTICE') or (mode == 'PRACTICE') != (exam.kind == 'practice'):
        raise ValueError('评分模式与答卷用途不一致')
    existing=await session.get(ScoringRun,exam.active_scoring_run_id) if exam.active_scoring_run_id else None
    if existing:
        if existing.workspace_id!=ws.id or existing.exam_id!=exam.id:raise ValueError('任务作用域不匹配')
        return existing
    cfg,_,_=await runtime_config(session,ws.id,require_consent=False)
    inputs=[]
    for eq in exam.questions:
        from fastapi import HTTPException
        if eq.frozen_snapshot and (not eq.frozen_snapshot.get('question_version')
                or not (eq.frozen_snapshot.get('rubric') or {}).get('rubric_version')
                or not eq.frozen_snapshot.get('answer_revision_id')):
            raise HTTPException(409,'旧答卷缺少可核对的评分版本，已保留原答和历史结果；请新建考试或做独立影子复核，不能自动混合新旧评分')
        if not eq.creation_snapshot and not eq.frozen_snapshot:
            raise HTTPException(409,'这份旧试卷未在开考时冻结题目与标准；已保留答卷，请新建版本明确的考试')
        revision=await ensure_revision(session,eq)
        if (eq.creation_snapshot or {}).get('study_practice_id'):
            paper_type = eq.creation_snapshot['qtype']
            if ((paper_type == 'mcq' and revision.payload.get('choice_index') is None) or
                    (paper_type == 'open' and not (revision.payload.get('text_answer') or '').strip())):
                raise HTTPException(422, '请提交自己的原答再进行复测；未作答的学习目标保留待验证')
        if not eq.frozen_snapshot:
            base=deepcopy(eq.creation_snapshot or question_snapshot(eq.question))
            if base.get('interview_set'):
                base.update(assessment_mode='PRACTICE', independence={
                    'eligible_for_independent_verification': False,
                    'canonical_question_id': base.get('canonical_question_id'),
                    'reason': '面经集试卷用于练习，不计入独立验证',
                })
            elif mode == 'OFFICIAL' or is_bank_practice_snapshot(base):
                from app.services.learning_exposure import independence_snapshot
                independence, assistance = await independence_snapshot(session, ws.id, exam, eq, base)
                base.update(independence=independence, assistance=assistance, assessment_mode=mode)
            eq.frozen_snapshot={**base,'submitted_answer':deepcopy(revision.payload),
                'answer_revision_id':revision.id,'answer_revision':revision.revision,'answer_hash':revision.content_hash}
        frozen=deepcopy(eq.frozen_snapshot)
        if frozen.get('qtype')=='open' and (frozen.get('rubric') or {}).get('schema_version')!=2:
            raise HTTPException(409,'旧开放题标准与当前评分器不兼容，不能自动改写历史标准')
        frozen.setdefault('answer_revision_id',revision.id)
        inputs.append((eq,frozen))
    route = scoring_route(inputs)
    cfg = scoring_config(cfg, route)
    exam.scoring_generation=(exam.scoring_generation or 0)+1
    trace=await start_trace(session,ws.id,'assessment_scoring',f'评分：{exam.title}',trigger='user',trigger_ref=f'exam:{exam.id}')
    run=ScoringRun(exam_id=exam.id,workspace_id=ws.id,generation=exam.scoring_generation,
        status='QUEUED',mode=mode,judge_version=JUDGE_VERSION,model_fingerprint=model_fingerprint(cfg),
        input_hash=digest([i for _,i in inputs]),trace_id=trace.id,
        meta={'scoring_route':route,'model':cfg.get('model'),'provider':cfg.get('provider'),'cost_known':False,
              'raw_response_visibility':'private_database_only','prompt_version':JUDGE_PROMPT_VERSION,
              'version_semantics':{
                  'question_version':'outer immutable question snapshot fingerprint, including rubric and question metadata',
                  'rubric.question_version':'question material identity bound by the rubric author/compiler; not the outer snapshot hash'},
              'question_count':len(inputs)})
    session.add(run);await session.flush();exam.active_scoring_run_id=run.id
    for eq,payload in inputs:
        session.add(ScoringStep(run_id=run.id,exam_question_id=eq.id,order_no=eq.order_no,
            input_json=payload,input_hash=digest({'input':payload,'judge':JUDGE_VERSION,'model':run.model_fingerprint})))
    exam.status='SCORING';exam.score=None;exam.gained_points=None;exam.completed_at=None
    exam.submitted_at=exam.submitted_at or now()
    exam.summary={'scoring_task_id':run.id,'score':None,'scoring_failed':[]}
    await emit_event(session,ws.id,'exam.scoring_queued',{'exam_id':exam.id,'run_id':run.id,'generation':run.generation},'exam',exam.id)
    await session.flush()
    return run


def ack(run):
    state={'FAILED':'SCORING_FAILED','NEEDS_REVIEW':'NEEDS_REVIEW','RETRY_WAIT':'SCORING_RETRY_WAIT','CANCELLED':'ABORTED'}.get(run.status,'SCORING')
    return {'exam_id':run.exam_id,'task_id':run.id,'status':state,'report_url':f'/api/v1/exams/{run.exam_id}/report'}


@dataclass(frozen=True)
class Claim:
    step_id:int
    run_id:int
    exam_id:int
    workspace_id:int
    generation:int
    owner:str
    fencing_token:int
    input_hash:str
    payload:dict


async def lock_run(session, run_id, *, skip_locked=False):
    """Common row order: Workspace -> Exam -> Run -> Step -> Answer.

    Read identity without a lock, then recheck it after acquiring parents. API
    callers may already own Workspace/Exam locks; this helper never asks them
    for the global dispatch lock after acquiring a parent row.
    """
    identity=(await session.execute(select(ScoringRun.workspace_id,ScoringRun.exam_id)
        .where(ScoringRun.id==run_id))).first()
    if identity is None:return None,None
    workspace_id,exam_id=identity
    workspace=await session.scalar(select(Workspace.id).where(Workspace.id==workspace_id)
        .with_for_update(skip_locked=skip_locked))
    if workspace is None:return None,None
    exam=await session.scalar(select(Exam).where(Exam.id==exam_id,Exam.workspace_id==workspace_id)
        .with_for_update(of=Exam,skip_locked=skip_locked).execution_options(populate_existing=True))
    if exam is None:return None,None
    run=await session.scalar(select(ScoringRun).where(ScoringRun.id==run_id,
        ScoringRun.workspace_id==workspace_id,ScoringRun.exam_id==exam_id)
        .with_for_update(skip_locked=skip_locked).execution_options(populate_existing=True))
    return run,exam


async def claim_step(session, owner, lease_seconds=45, at=None):
    at=at or now()
    await session.execute(text('SELECT pg_advisory_xact_lock(20261117, 0)'))
    for model in (ScoringStep,AnalysisStep):
        if await session.scalar(select(model.id).where(model.status=='RUNNING',model.lease_expires_at>at).limit(1)):
            await session.commit();return None
    rows=(await session.execute(select(ScoringStep.id,ScoringRun.id).join(ScoringRun).where(
        ScoringRun.status.in_(ACTIVE),ScoringStep.status.in_(('QUEUED','RETRY_WAIT','RUNNING')))
        .order_by(ScoringRun.id,ScoringStep.order_no))).all()
    for step_id,run_id in rows:
        run,exam=await lock_run(session,run_id,skip_locked=True)
        if not run or run.status not in ACTIVE or exam.active_scoring_run_id!=run.id or \
            exam.scoring_generation!=run.generation or exam.status in ('ABORTED','COMPLETED'):continue
        step=await session.scalar(select(ScoringStep).where(ScoringStep.id==step_id,ScoringStep.run_id==run.id)
            .with_for_update(skip_locked=True).execution_options(populate_existing=True))
        if not step or step.status not in ('QUEUED','RETRY_WAIT','RUNNING') or step.next_retry_at and step.next_retry_at>at:continue
        if step.status=='RUNNING':
            # Heartbeats do not take the global dispatch lock. Recheck after
            # acquiring the row, since a renewal may have committed after scan.
            if step.lease_expires_at and step.lease_expires_at>at:continue
            step.retry_count+=1
            step.attempts=[*step.attempts,{'attempt':step.attempt_count,'outcome':'OUTCOME_UNKNOWN',
                'at':at.isoformat(),'reason':'lease_expired; provider billing may have occurred'}]
            if step.retry_count>=3:
                step.status='FAILED';step.error_category='WORKER_LOST';step.error='本题连续失去后台任务，已停止自动接管';continue
        step.attempt_count+=1;step.fencing_token+=1
        step.status='RUNNING';step.lease_owner=owner;step.lease_expires_at=at+timedelta(seconds=lease_seconds)
        step.next_retry_at=None;step.updated_at=at;run.status='RUNNING';run.updated_at=at
        claim=Claim(step.id,run.id,run.exam_id,run.workspace_id,run.generation,owner,step.fencing_token,step.input_hash,deepcopy(step.input_json))
        await session.commit();return claim
    await session.commit();return None


async def owned(session, claim, at=None):
    at=at or now()
    run,exam=await lock_run(session,claim.run_id)
    if not run:return None,None
    step=await session.scalar(select(ScoringStep).where(ScoringStep.id==claim.step_id).with_for_update().execution_options(populate_existing=True))
    if not run or not step or run.status not in ACTIVE or step.status!='RUNNING' or \
        step.lease_owner!=claim.owner or step.fencing_token!=claim.fencing_token or \
        not step.lease_expires_at or step.lease_expires_at<=at or run.generation!=claim.generation:
        return None,None
    if run.workspace_id!=claim.workspace_id or run.exam_id!=claim.exam_id or step.run_id!=run.id or step.input_hash!=claim.input_hash:
        return None,None
    if not exam or exam.active_scoring_run_id!=run.id or exam.scoring_generation!=run.generation or exam.status in ('ABORTED','COMPLETED'):
        return None,None
    return step,run


async def heartbeat(session,claim,lease_seconds=45):
    step,run=await owned(session,claim)
    if not step:await session.commit();return False
    step.lease_expires_at=now()+timedelta(seconds=lease_seconds)
    await session.commit();return True


def public_result(result):
    return {k:v for k,v in result.items() if k not in ('raw_response','usage','request_meta')}


def apply_result(answer,result):
    answer.points=result['points'];answer.max_points=result['max_points'];answer.verdict=result['verdict']
    answer.judge_version=result.get('judge_version',JUDGE_VERSION)
    answer.feedback={**public_result(result),'hit_points':result.get('hit',[]),'missed_points':result.get('missing',[]),
        'advice':result.get('reasoning',''),'judge_reasoning':result.get('reasoning','')}


def attempt_record(step,result,outcome):
    raw=result.get('raw_response')
    return {'attempt':step.attempt_count,'outcome':outcome,'at':now().isoformat(),
        'usage':result.get('usage') or {'known':False},'request_meta':result.get('request_meta') or {},
        'raw_response':raw[:262144] if isinstance(raw,str) else None,
        'raw_response_truncated':isinstance(raw,str) and len(raw)>262144,
        'raw_response_sha256':digest(raw) if isinstance(raw,str) else None}


async def complete_step(session,claim,result):
    step,run=await owned(session,claim)
    if not step:await session.commit();return False
    step.result_json=public_result(result);step.status='SUCCEEDED';step.error=None;step.error_category=None
    step.attempts=[*step.attempts,attempt_record(step,result,'SUCCEEDED')]
    step.lease_owner=None;step.lease_expires_at=None;step.updated_at=now();run.updated_at=now()
    if run.mode in ('OFFICIAL', 'PRACTICE'):
        answer=await session.scalar(select(ExamAnswer).where(ExamAnswer.exam_question_id==step.exam_question_id))
        apply_result(answer,result)
    await session.commit();return True


async def fail_step(session,claim,error):
    step,run=await owned(session,claim)
    if not step:await session.commit();return False
    category=getattr(error,'category','INTERNAL_ERROR')
    safe_message=str(error)[:600] if hasattr(error,'category') else '本题后台处理失败，已保存原答和成功题结果'
    step.error_category=category;step.error=safe_message
    data={k:getattr(error,k,None) for k in ('raw_response','usage','request_meta')}
    step.attempts=[*step.attempts,attempt_record(step,data,category)]
    transient=category in ('REQUEST_TIMEOUT','NETWORK_TRANSIENT','RATE_LIMITED','PROVIDER_5XX')
    repair=category in ('INVALID_JSON','INVALID_SCHEMA','INVALID_QUOTE','INVALID_CITATION','OUTPUT_TRUNCATED','INVALID_RESPONSE','IDENTITY_MISMATCH')
    step.retry_count+=int(transient);step.repair_count+=int(repair)
    retry=(transient and step.retry_count<3) or (repair and step.repair_count<2)
    step.status='RETRY_WAIT' if retry else 'FAILED'
    delay=max(float((data.get('request_meta') or {}).get('retry_after') or 0), min(120,5*2**step.retry_count)+random.uniform(0,2))
    step.next_retry_at=now()+timedelta(seconds=delay) if retry else None
    step.lease_owner=None;step.lease_expires_at=None;step.updated_at=now()
    run.error_category=category;run.error=safe_message;run.updated_at=now()
    if run.mode in ('OFFICIAL', 'PRACTICE'):
        answer=await session.scalar(select(ExamAnswer).where(ExamAnswer.exam_question_id==step.exam_question_id))
        answer.points=None;answer.verdict='SCORING_FAILED';answer.judge_version=None
        answer.feedback={'error':safe_message,'error_category':category,'next_retry_at':step.next_retry_at.isoformat() if step.next_retry_at else None}
    await session.commit();return True


async def resume(session,ws,exam):
    if exam.workspace_id!=ws.id:raise ValueError('考试不属于当前工作空间')
    run,locked_exam=await lock_run(session,exam.active_scoring_run_id)
    if not run:return await enqueue(session,ws,exam)
    if run.workspace_id!=ws.id or run.exam_id!=exam.id:raise ValueError('任务作用域不匹配')
    exam=locked_exam
    if run.status in ACTIVE:return run
    if run.status=='COMPLETED':return run
    if run.status=='CANCELLED':raise ValueError('已取消的任务不能恢复')
    cfg,_,_=await runtime_config(session,ws.id,require_consent=False)
    cfg=scoring_config(cfg,(run.meta or {}).get('scoring_route','default'))
    if model_fingerprint(cfg)!=run.model_fingerprint or run.judge_version!=JUDGE_VERSION:
        raise ValueError('模型或评分器版本变化，不能混合旧成功题；请保留原成绩并创建影子复核')
    rows=(await session.scalars(select(ScoringStep).where(ScoringStep.run_id==run.id).with_for_update())).all()
    for step in rows:
        if step.status=='SUCCEEDED':continue
        step.status='QUEUED';step.next_retry_at=None;step.retry_count=0;step.repair_count=0
        step.fencing_token+=1;step.lease_owner=None;step.lease_expires_at=None
    run.status='QUEUED';run.error=None;run.error_category=None;run.updated_at=now()
    if (run.meta or {}).get('publication'):
        old=run.meta['publication']
        run.meta={**run.meta,'publication':{**old,'status':'PENDING','attempt_count':0,
            'next_retry_at':None,'lease_expires_at':None,'token':None,'manually_resumed_at':now().isoformat()}}
    exam.status='SCORING';exam.score=None;exam.gained_points=None
    await emit_event(session,ws.id,'exam.scoring_retry',{'exam_id':exam.id,'run_id':run.id,
        'retained_successes':sum(s.status=='SUCCEEDED' for s in rows)},'exam',exam.id)
    return run


async def cancel(session,ws,exam):
    if exam.workspace_id!=ws.id:raise ValueError('考试不属于当前工作空间')
    run,locked_exam=await lock_run(session,exam.active_scoring_run_id)
    if run and (run.workspace_id!=ws.id or run.exam_id!=exam.id):raise ValueError('任务作用域不匹配')
    if locked_exam:exam=locked_exam
    if not run or run.status=='COMPLETED':raise ValueError('已完成的成绩不能通过取消改写')
    rows=(await session.scalars(select(ScoringStep).where(ScoringStep.run_id==run.id).with_for_update())).all()
    for step in rows:
        if step.status=='SUCCEEDED':continue
        if step.status=='RUNNING':
            step.attempts=[*step.attempts,{'attempt':step.attempt_count,'outcome':'OUTCOME_UNKNOWN','at':now().isoformat(),'reason':'cancelled while provider may still be processing'}]
        step.status='CANCELLED';step.fencing_token+=1;step.lease_owner=None;step.lease_expires_at=None;step.next_retry_at=None
    run.status='CANCELLED';run.updated_at=now();exam.status='ABORTED';exam.score=None;exam.gained_points=None
    await emit_event(session,ws.id,'exam.scoring_cancelled',{'exam_id':exam.id,'run_id':run.id},'exam',exam.id)
    return run


async def view(session,run_id):
    run=await session.get(ScoringRun,run_id) if run_id else None
    if not run:return None
    rows=(await session.scalars(select(ScoringStep).where(ScoringStep.run_id==run.id).order_by(ScoringStep.order_no))).all()
    retries=[s.next_retry_at for s in rows if s.next_retry_at]
    publication_retry=((run.meta or {}).get('publication') or {}).get('next_retry_at')
    if publication_retry:retries.append(datetime.fromisoformat(publication_retry))
    return {'id':run.id,'status':run.status,'completed_count':sum(s.status=='SUCCEEDED' for s in rows),
        'total_count':len(rows),'next_retry_at':min(retries).isoformat() if retries else None,
        'can_retry':run.status in ('FAILED','NEEDS_REVIEW'),
        'error':{'code':run.error_category,'message':run.error} if run.error else None,
        'judge_version':run.judge_version,'model_fingerprint':run.model_fingerprint,
        'scoring_route':(run.meta or {}).get('scoring_route','default'),'requested_model':(run.meta or {}).get('model'),
        'questions':[{'order_no':s.order_no,'status':s.status,'attempt_count':s.attempt_count,
            'can_retry':s.status=='FAILED','error':{'code':s.error_category,'message':s.error} if s.error else None} for s in rows]}
