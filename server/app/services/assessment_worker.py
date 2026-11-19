"""Per-question execution and atomic publication, used by the existing local Worker."""
import asyncio
from contextlib import suppress
from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select
from app.models import Exam, Workspace, ScoringRun, ScoringStep, Trace, SkillState, CompetencySnapshot
from app.services import assessment_jobs as jobs
from app.services.assessment_versions import digest
from app.services.assessment_rubric import AssessmentJudgeError, JUDGE_VERSION, Judgement, validate_judgement
from app.services.llm import AgentAdapter
from app.services.llm_judge import judge_frozen_answer
from app.services.project_autopilot_config import runtime_config, model_fingerprint

log=logging.getLogger('careerpilot.assessment_worker')
OUTBOX_ROOT=Path(__file__).resolve().parents[3]/'personal'/'assessment-outbox'


def outbox_path(database_identity,claim):
    folder=OUTBOX_ROOT/digest(database_identity)[:24]
    path=folder/f'{claim.run_id}-{claim.step_id}-{claim.input_hash}.json'
    if not path.resolve().is_relative_to(OUTBOX_ROOT.resolve()):raise ValueError('invalid outbox scope')
    return path


def save_outbox(path,claim,result):
    path.parent.mkdir(parents=True,exist_ok=True)
    payload={'run_id':claim.run_id,'step_id':claim.step_id,'input_hash':claim.input_hash,
        'generation':claim.generation,'completed_at':jobs.now().isoformat(),'result':result}
    temp=path.with_suffix(f'.{os.getpid()}.partial')
    with temp.open('w',encoding='utf-8') as stream:
        json.dump(payload,stream,ensure_ascii=False,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)


def rule_result(payload):
    from app.services.scorer import score_question
    from app.services.assessment_versions import mcq_rubric
    payload=deepcopy(payload)
    if payload['qtype']=='mcq':payload['rubric']=mcq_rubric(payload.get('rubric'),payload.get('options'))
    q=SimpleNamespace(**payload)
    submitted=payload.get('submitted_answer') or {}
    choice=submitted.get('choice_index')
    if q.qtype=='mcq' and choice is not None and (type(choice) is not int or not 0<=choice<len(payload['options'])):
        raise AssessmentJudgeError('INVALID_INPUT','冻结选项不属于本题，不能生成有效成绩')
    if q.qtype=='open' and (submitted.get('text_answer') or '').strip():
        raise AssessmentJudgeError('INVALID_INPUT','非空开放题不得规则降级评分')
    result=score_question(q,choice_index=submitted.get('choice_index'),text=None)
    rubric=payload.get('rubric') or {};maximum=float(rubric.get('max_points',5))
    criteria=[{'criterion_id':c['criterion_id'],'title':c['title'],'score':0,'max_score':4,
        'answer_quotes':[],'satisfied_anchors':[next(a['anchor_id'] for a in c['score_anchors'] if a['score']==0)],
        'missing_points':[c['title']],'reasoning_summary':'本次未作答，没有该项表现证据。',
        'why_not_higher':'需要提交符合本题要求的真实回答。'} for c in rubric.get('criteria',[])]
    names=lambda values:[p.get('title','') if isinstance(p,dict) else str(p) for p in values]
    return {**result,'max_points':maximum,'overall_score':100*result['points']/maximum,
        'hit':names(result.get('hits',[])),'missing':names(result.get('misses',[])) or [c['title'] for c in criteria],
        'reasoning':'未作答；确定性记为本题0分。' if q.qtype=='open' else '按冻结的选择题正确选项确定性比较。',
        'criterion_scores':criteria,'question_id':payload['question_id'],
        'answer_revision_id':payload['answer_revision_id'],'rubric_version':rubric.get('rubric_version',digest(rubric)),
        'judge_version':'rule-v2','validation_status':'VALID','confidence_status':'DETERMINISTIC_RULE',
        'calibration_status':rubric.get('calibration_status','UNASSESSED'),'needs_human_review':False,
        'usage':{'model_calls':0},'request_meta':{'request_started':False}}


def cached_result(path,claim):
    if not path.exists():return None
    saved=json.loads(path.read_text(encoding='utf-8'))
    if any(saved.get(k)!=getattr(claim,k) for k in ('run_id','step_id','input_hash','generation')):return None
    result=saved['result'];payload=claim.payload
    if result.get('judge_version')=='rule-v2':
        return rule_result(payload)
    if isinstance(result.get('raw_response'),str):
        fields=json.loads(result['raw_response'])
    else:
        from app.services.assessment_rubric import CriterionScore, DetectedError
        fields={k:result[k] for k in Judgement.model_fields if k in result}
        fields['criterion_scores']=[{**{k:r[k] for k in CriterionScore.model_fields if k in r},
            'score':r.get('judge_score',r['score'])} for r in result['criterion_scores']]
        fields['critical_errors']=[{k:r[k] for k in DetectedError.model_fields if k in r} for r in result['critical_errors']]
    valid=validate_judgement(fields,rubric=payload['rubric'],text=payload['submitted_answer'].get('text_answer') or '',
        question_id=payload['question_id'],answer_revision_id=payload['answer_revision_id'])
    recovered = {**valid,**{k:result.get(k) for k in ('usage','raw_response')},
        'request_meta':{**(result.get('request_meta') or {}),'recovered_from_local_outbox':True,'request_started':False}}
    return _validate_project_result(recovered, payload)


def _validate_project_result(result, payload):
    if payload.get('learning_activity_kind') != 'PROJECT': return result
    from app.services.project_practice import validate_review_result
    try:
        return validate_review_result(result, payload)
    except AssessmentJudgeError as error:
        # Semantic checks happen after the paid, structurally valid response.
        # Preserve actual usage and the private response for the failed attempt.
        error.raw_response = result.get('raw_response')
        error.usage = result.get('usage') or error.usage
        error.request_meta = {**(result.get('request_meta') or {}), **(error.request_meta or {})}
        raise


async def evaluate(claim,cfg,repair=None):
    payload=claim.payload;submitted=payload.get('submitted_answer') or {}
    if payload['qtype']=='mcq' or not (submitted.get('text_answer') or '').strip():return rule_result(payload)
    if not all(cfg.get(k) for k in ('provider','model','base_url','api_key')):
        raise AssessmentJudgeError('NOT_CONFIGURED','尚未配置可用评分模型，原答已保存')
    result = await judge_frozen_answer(AgentAdapter(),question_id=payload['question_id'],
        answer_revision_id=payload['answer_revision_id'],title=payload['title'],skill_name=payload.get('skill_name'),
        rubric=payload['rubric'],text=submitted.get('text_answer'),llm_config=cfg,timeout=90,
        repair_diagnostic=repair)
    return _validate_project_result(result, payload)


async def execute_claim(claim,worker_id,factory,lease_seconds=45,heartbeat_seconds=10):
    async with factory() as session:
        if not await jobs.heartbeat(session,claim,lease_seconds):return
        run=await session.get(ScoringRun,claim.run_id)
        cfg,_,_=await runtime_config(session,claim.workspace_id,require_consent=False)
        cfg = jobs.scoring_config(cfg, (run.meta or {}).get('scoring_route', 'default'))
        if run.model_fingerprint!=model_fingerprint(cfg) or run.judge_version!=JUDGE_VERSION:
            await jobs.fail_step(session,claim,AssessmentJudgeError('CONFIG_CHANGED','模型或评分器版本已改变，不能混合旧评分；请创建影子复核'))
            return
        if run.mode == 'PRACTICE':
            from fastapi import HTTPException
            from app.models.learning_feedback import LearningGoal, LearningSubmission
            from app.services.learning_feedback import assert_goal_sources
            submission = await session.scalar(select(LearningSubmission).where(
                LearningSubmission.scoring_run_id == run.id, LearningSubmission.workspace_id == run.workspace_id))
            goal = await session.get(LearningGoal, submission.goal_id) if submission else None
            try:
                if goal is None:
                    raise HTTPException(409, '练习来源绑定缺失，需要复核')
                await assert_goal_sources(session, await session.get(Workspace, run.workspace_id), goal)
            except HTTPException as error:
                await jobs.fail_step(session, claim, AssessmentJudgeError('SOURCE_CHANGED', str(error.detail)))
                return
        step=await session.get(ScoringStep,claim.step_id)
        repair=step.error if step.repair_count else None
        path=outbox_path(str(session.get_bind().url),claim)
        if not await jobs.heartbeat(session,claim,lease_seconds):return
    async def evaluate_or_recover():
        recovered=cached_result(path,claim)
        if recovered is not None:return recovered
        result=await evaluate(claim,cfg,repair)
        save_outbox(path,claim,result)
        return result
    task=asyncio.create_task(evaluate_or_recover())
    async def keepalive():
        while True:
            await asyncio.sleep(heartbeat_seconds)
            try:
                async with factory() as session:valid=await jobs.heartbeat(session,claim,lease_seconds)
            except Exception:valid=False
            if not valid:task.cancel();return
    keeper=asyncio.create_task(keepalive())
    try:
        result=await task
        # If DB commit fails, retain the validated local outbox; takeover can
        # publish it under a new lease without another paid model call.
        async with factory() as session:await jobs.complete_step(session,claim,result)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        if path.exists() and not isinstance(exc,AssessmentJudgeError):
            log.warning('grading result publication pending run=%s step=%s; outbox retained',claim.run_id,claim.step_id)
        else:
            async with factory() as session:await jobs.fail_step(session,claim,exc)
    finally:
        keeper.cancel()
        with suppress(asyncio.CancelledError):await keeper


PUBLICATION_MAX_ATTEMPTS=3
PUBLICATION_LEASE_SECONDS=60


def _publication_state(run):
    return deepcopy((run.meta or {}).get('publication') or {'attempt_count':0,'attempts':[]})


def _stop_publication(run,exam,state):
    state.update(status='FAILED',next_retry_at=None,lease_expires_at=None,token=None)
    run.meta={**(run.meta or {}),'publication':state}
    run.status='FAILED';run.error_category='PUBLICATION_FAILED'
    run.error='本次报告发布连续失败，成功评分已保存；可手动重试发布，无需重评成功题目'
    exam.status='SCORING_FAILED';exam.score=None;exam.gained_points=None
    exam.summary={'score':None,'scoring_task_id':run.id,'publication_failed':True,'scoring_failed':[]}


def _begin_publication(run,exam):
    """Reserve a finite durable publication attempt before its transaction.

    This is independent of model attempts. A crashed publisher leaves a lease;
    the next publisher records the unknown outcome and obeys the same bound.
    """
    at=jobs.now();state=_publication_state(run)
    if state.get('status')=='RUNNING':
        expiry=state.get('lease_expires_at')
        if expiry and datetime.fromisoformat(expiry)>at:return None
        state['attempts']=[{**a,'outcome':'OUTCOME_UNKNOWN','ended_at':at.isoformat()}
            if a.get('token')==state.get('token') else a for a in state.get('attempts',[])]
    retry=state.get('next_retry_at')
    if retry and datetime.fromisoformat(retry)>at:return None
    if state.get('attempt_count',0)>=PUBLICATION_MAX_ATTEMPTS:
        _stop_publication(run,exam,state);return None
    token=uuid4().hex;count=state.get('attempt_count',0)+1
    state.update(status='RUNNING',attempt_count=count,token=token,next_retry_at=None,
                 lease_expires_at=(at+timedelta(seconds=PUBLICATION_LEASE_SECONDS)).isoformat())
    state['attempts']=[*state.get('attempts',[]),{'attempt':count,'token':token,'outcome':'RUNNING','at':at.isoformat()}]
    run.meta={**(run.meta or {}),'publication':state};run.status='RUNNING';run.updated_at=at
    return token


async def _record_publication_failure(factory,run_id,token):
    async with factory() as session:
        run,exam=await jobs.lock_run(session,run_id)
        if not run or run.status not in jobs.ACTIVE:return
        state=_publication_state(run)
        if state.get('token')!=token:return
        at=jobs.now()
        state['attempts']=[{**a,'outcome':'PUBLICATION_FAILED','ended_at':at.isoformat()}
            if a.get('token')==token else a for a in state.get('attempts',[])]
        if state['attempt_count']>=PUBLICATION_MAX_ATTEMPTS:
            _stop_publication(run,exam,state)
        else:
            retry=at+timedelta(seconds=min(120,5*2**state['attempt_count']))
            state.update(status='RETRY_WAIT',next_retry_at=retry.isoformat(),token=None,lease_expires_at=None)
            run.meta={**(run.meta or {}),'publication':state};run.status='RETRY_WAIT'
            run.error_category='PUBLICATION_FAILED';run.error='报告发布暂时失败，成功评分已保存；稍后仅重试发布'
            exam.status='SCORING_RETRY_WAIT';exam.score=None;exam.gained_points=None
            exam.summary={'score':None,'scoring_task_id':run.id,'publication_failed':True,'scoring_failed':[]}
        run.updated_at=at
        await session.commit()


async def _publish(factory,run_id,token):
    async with factory() as session:
        run,exam=await jobs.lock_run(session,run_id)
        if not run or run.status not in jobs.ACTIVE:return
        publication=_publication_state(run)
        if (publication.get('token')!=token or publication.get('status')!='RUNNING'
                or datetime.fromisoformat(publication['lease_expires_at'])<=jobs.now()):return
        if run.mode not in ('OFFICIAL', 'PRACTICE'):
            run.status='NEEDS_REVIEW';run.error_category='NON_OFFICIAL_PUBLICATION'
            run.error='非正式评分任务不能自动发布或更新个人能力证据';await session.commit();return
        if exam.active_scoring_run_id!=run.id or exam.scoring_generation!=run.generation or exam.status=='ABORTED':
            run.status='CANCELLED';await session.commit();return
        rows=(await session.scalars(select(ScoringStep).where(ScoringStep.run_id==run.id)
            .order_by(ScoringStep.order_no).with_for_update())).all()
        if not rows or any(s.status!='SUCCEEDED' for s in rows):return
        # The complete report and all derived side effects are one transaction.
        # Its finite attempt reservation above remains durable after rollback.
        from app.api.v1 import exams
        by_id={eq.id:eq for eq in exam.questions}
        for step in rows:jobs.apply_result(by_id[step.exam_question_id].answer,step.result_json)
        exam.total_points=sum(s.result_json['max_points'] for s in rows)
        exam.gained_points=sum(s.result_json['points'] for s in rows)
        exam.score=round(100*exam.gained_points/exam.total_points,1) if exam.total_points else 0
        ws=await session.get(Workspace,run.workspace_id);trace=await session.get(Trace,run.trace_id)
        if run.mode == 'PRACTICE':
            from app.services.learning_feedback import publish_practice
            await publish_practice(session, ws, exam, run, rows)
            from app.services.tracing import finish_trace
            await finish_trace(session, trace, 'SUCCESS', 0)
        else:
            await exams._finalize_exam(session,ws,exam,trace,time.perf_counter())
            states=(await session.scalars(select(SkillState).where(SkillState.workspace_id==ws.id))).all()
            session.add(CompetencySnapshot(workspace_id=ws.id,trigger='exam_completed',trigger_ref=f'scoring:{run.id}',
                evidence_count=sum(s.evidence_coverage or 0 for s in states),model_version='observation-v2-heuristic',
                skills=[{k:getattr(s,k) for k in ('skill_id','estimated_level','confidence','status','evidence_coverage','model_version')} for s in states]))
        run.status='COMPLETED';run.completed_at=jobs.now();run.error=None;run.error_category=None
        publication['attempts']=[{**a,'outcome':'SUCCEEDED','ended_at':jobs.now().isoformat()}
            if a.get('token')==token else a for a in publication.get('attempts',[])]
        publication.update(status='COMPLETED',token=None,lease_expires_at=None,next_retry_at=None)
        run.meta={**(run.meta or {}),'publication':publication}
        await session.flush()
        run.report=({'mode':'PRACTICE','exam_id':exam.id,'mastery_updated':False,
            'submission_id':(exam.summary or {}).get('submission_id'),
            'questions':[s.result_json for s in rows]} if run.mode == 'PRACTICE'
            else deepcopy(await exams._exam_report(session,ws,exam.id)))
        await session.commit()


async def reconcile(factory):
    async with factory() as session:
        ids=(await session.scalars(select(ScoringRun.id).where(ScoringRun.status.in_(jobs.ACTIVE)))).all()
    for run_id in ids:
        publication_token=None
        async with factory() as session:
            run,exam=await jobs.lock_run(session,run_id)
            if not run or run.status not in jobs.ACTIVE:continue
            if run.mode not in ('OFFICIAL', 'PRACTICE'):
                run.status='NEEDS_REVIEW';run.error_category='NON_OFFICIAL_PUBLICATION'
                run.error='非正式评分任务不能自动发布或更新个人能力证据';await session.commit();continue
            if exam.active_scoring_run_id!=run.id or exam.scoring_generation!=run.generation or exam.status=='ABORTED':
                run.status='CANCELLED';await session.commit();continue
            rows=(await session.scalars(select(ScoringStep).where(ScoringStep.run_id==run.id)
                .order_by(ScoringStep.order_no).with_for_update())).all()
            if not rows:continue
            pending=[s for s in rows if s.status in ('QUEUED','RUNNING','RETRY_WAIT')]
            if pending:
                waiting=all(s.status=='RETRY_WAIT' for s in pending)
                run.status='RETRY_WAIT' if waiting else 'RUNNING'
                exam.status='SCORING_RETRY_WAIT' if waiting else 'SCORING'
                await session.commit();continue
            failed=[s for s in rows if s.status!='SUCCEEDED']
            if failed:
                review=any(s.error_category in ('INVALID_JSON','INVALID_SCHEMA','INVALID_QUOTE','INVALID_CITATION','INVALID_RESPONSE','OUTPUT_TRUNCATED') for s in failed)
                run.status='NEEDS_REVIEW' if review else 'FAILED';exam.status='NEEDS_REVIEW' if review else 'SCORING_FAILED'
                run.error_category=failed[0].error_category;run.error=failed[0].error
                exam.score=None;exam.gained_points=None
                exam.summary={'score':None,'scoring_failed':[s.order_no for s in failed],'scoring_task_id':run.id}
                await session.commit();continue
            publication_token=_begin_publication(run,exam)
            await session.commit()
        if publication_token:
            try:
                await _publish(factory,run_id,publication_token)
            except Exception:
                # Never expose SQL parameters, private responses or exception text.
                try:await _record_publication_failure(factory,run_id,publication_token)
                except Exception:log.warning('grading publication recovery pending run=%s; durable lease retained',run_id)
