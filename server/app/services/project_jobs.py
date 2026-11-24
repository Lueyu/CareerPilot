"""PostgreSQL durable stage queue. No provider calls or open HTTP transactions here."""
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.i18n import Locale, request_language
from app.models import AnalysisStep, AppSetting, Project, ProjectAnalysis

ACTIVE = ('QUEUED', 'RUNNING', 'RETRY_WAIT')
OPEN = ACTIVE + ('PAUSED_USER', 'PAUSED_BUDGET', 'BLOCKED', 'WAITING_WORKER')
HANDOFF_KEY = 'project_worker_handoff'
MODEL_STAGES = {'plan_modules', 'analyze_module', 'cross_module', 'explain_section', 'extract_candidates','analyze_change_impact'}

def utcnow():
    return datetime.now(timezone.utc)

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

@dataclass(frozen=True)
class Claim:
    step_id: int
    step_key: str
    run_id: int
    workspace_id: int
    project_id: int
    stage: str
    payload: dict
    owner: str
    fencing_token: int

async def enqueue(session, project, request_key, mode='full', target_sha=None, budget=None,
                  model_config_fingerprint='', pipeline_version=None, prompt_bundle_version=None, reuse_open=False,
                  language: Locale | None = None):
    language = language or request_language()
    if pipeline_version is None or prompt_bundle_version is None:
        from app.services.project_autopilot_engine import PIPELINE_VERSION, PROMPT_BUNDLE_VERSION
        pipeline_version=pipeline_version or PIPELINE_VERSION
        prompt_bundle_version=prompt_bundle_version or PROMPT_BUNDLE_VERSION
    handoff = await session.scalar(select(AppSetting.value).where(
        AppSetting.key == HANDOFF_KEY).with_for_update(read=True))
    staging = bool(handoff and handoff.get('target_prompt') == prompt_bundle_version)
    await session.execute(text('SELECT pg_advisory_xact_lock(20261117, :id)'), {'id':project.id})
    previous = await session.scalar(select(ProjectAnalysis).where(
        ProjectAnalysis.workspace_id==project.workspace_id, ProjectAnalysis.request_key==request_key))
    if previous:
        if previous.project_id != project.id:
            raise ValueError('请求标识已用于其他项目')
        return previous
    await session.refresh(project)
    if project.archived_at:
        raise ValueError('项目已移除')
    open_statuses = ('WAITING_WORKER',) if staging else OPEN
    running = await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.project_id==project.id,
        ProjectAnalysis.pipeline_version!='legacy', ProjectAnalysis.status.in_(open_statuses)).order_by(ProjectAnalysis.id.desc()))
    if running and reuse_open and (running.prompt_bundle_version != prompt_bundle_version or
            running.pipeline_version != pipeline_version or running.model_config_fingerprint != model_config_fingerprint):
        # A user-requested scan can replace an incompatible checkpoint without deleting its history.
        await control_run(session, running.id, project.workspace_id, 'cancel', commit=False)
        running = None
    if running:
        if staging:
            return running
        if mode=='full' and target_sha is None and not reuse_open:
            project.pending_update={'mode':'full','request_key':request_key,'generation_language':language}
        if target_sha and target_sha != running.target_sha:
            project.pending_update={'generation_language':language,**(project.pending_update or {}),'target_sha':target_sha,
                'mode':'full' if (project.pending_update or {}).get('mode')=='full' else mode}
        return running
    if not staging:
        project.generation += 1
        project.pending_update = None  # a newly accepted run consumes older coalesced intent
    run=ProjectAnalysis(project_id=project.id,workspace_id=project.workspace_id,request_key=request_key,
        status='WAITING_WORKER' if staging else 'QUEUED',mode=mode,target_sha=target_sha,
        base_sha=project.last_seen_sha,generation=project.generation,
        pipeline_version=pipeline_version,prompt_bundle_version=prompt_bundle_version,
        model_config_fingerprint=model_config_fingerprint,
        budget=dict(budget or {}),usage={'model_calls':0})
    session.add(run)
    await session.flush()
    from app.services.tracing import start_trace
    trace=await start_trace(session,project.workspace_id,'project_autopilot',f'理解项目：{project.name}',
        trigger='system',trigger_ref=str(run.id))
    run.result_meta={'trace_id':trace.id,'cost_known':False,'generation_language':language}
    from app.services.project_prompts import PROMPT_BUNDLE_VERSION
    if prompt_bundle_version == PROMPT_BUNDLE_VERSION:
        from app.config import get_settings
        run.result_meta.update(transport_mode='sse-v1',
            stage_timeout_seconds=get_settings().project_scan_stage_timeout_seconds)
    await ensure_steps(session,run,[{'key':'resolve','stage':'resolve','requires':[],'payload':{}}])
    return run

async def ensure_steps(session, run, specs):
    await session.flush()
    known=set((await session.scalars(select(AnalysisStep.step_key).where(AnalysisStep.run_id==run.id))).all())
    for spec in specs:
        if spec['key'] in known:
            continue
        known.add(spec['key'])
        payload=spec.get('payload',{})
        session.add(AnalysisStep(run_id=run.id,workspace_id=run.workspace_id,project_id=run.project_id,
            step_key=spec['key'],stage=spec['stage'],module_key=spec.get('module_key'),
            requires=spec.get('requires',[]),input_json=payload,
            input_hash=fingerprint({'payload':payload,'model':run.model_config_fingerprint,
                'pipeline':run.pipeline_version,'prompt':run.prompt_bundle_version,'target':run.target_sha}),
            uses_model=spec.get('uses_model',spec['stage'] in MODEL_STAGES)))
    await session.flush()

async def _split_priorities(session, rows):
    """New recovery nodes retain their frozen parent's queue position."""
    from app.services import project_stage_splits as splits
    priorities={}
    for run in {run.id:run for _,run in rows}.values():
        queued=[step for step,owner in rows if owner.id==run.id]
        plans=(run.result_meta or {}).get('source_splits',[])
        if not plans and not any((step.input_json or {}).get('source_split') for step in queued):continue
        try:
            if not isinstance(plans,list):raise ValueError('invalid plans')
            parents={step.id:step for step in (await session.scalars(select(AnalysisStep).where(
                AnalysisStep.run_id==run.id,AnalysisStep.workspace_id==run.workspace_id,
                AnalysisStep.status=='SUPERSEDED'))).all()}
            identity={'run_id':run.id,'workspace_id':run.workspace_id,'project_id':run.project_id,
                'target_sha':run.target_sha,'model_config_fingerprint':run.model_config_fingerprint,
                'pipeline_version':run.pipeline_version,'prompt_bundle_version':run.prompt_bundle_version}
            expected={}
            for plan in plans:
                if plan['identity']!=identity:raise ValueError('identity changed')
                frozen=plan['parent'];parent=parents.get(frozen['id'])
                if not parent or parent.output_json is not None or any(getattr(parent,key)!=value
                    for key,value in frozen.items() if key not in ('status','attempts_sha256')) or \
                        fingerprint(parent.attempts)!=frozen['attempts_sha256']:
                    raise ValueError('parent history changed')
                for index,spec in enumerate(splits.plan_specs(plan)):
                    if spec['key'] in expected:raise ValueError('duplicate recovery key')
                    expected[spec['key']]=(parent.id,index+1,spec)
            for step in queued:
                if step.step_key not in expected:
                    if (step.input_json or {}).get('source_split'):raise ValueError('unregistered recovery node')
                    continue
                parent_id,rank,spec=expected[step.step_key]
                expected_hash=fingerprint({'payload':spec['payload'],'model':run.model_config_fingerprint,
                    'pipeline':run.pipeline_version,'prompt':run.prompt_bundle_version,'target':run.target_sha})
                if step.stage!=spec['stage'] or step.input_json!=spec['payload'] or \
                        step.input_hash!=expected_hash or step.requires!=spec['requires'] or \
                        step.uses_model!=spec['uses_model'] or step.module_key!=spec['module_key']:
                    raise ValueError('recovery node changed')
                priorities[step.id]=(parent_id,rank,step.id)
        except (KeyError,TypeError,AttributeError,ValueError):
            run.status='BLOCKED';run.error='源码拆分恢复节点或历史记录不匹配，已停止领取阶段'
            run.result_meta={**(run.result_meta or {}),'error_category':'CONFIG_CHANGED'}
            run.updated_at=utcnow()
    return priorities


async def claim_step(session, owner, lease_seconds=45, now=None):
    now=now or utcnow()
    # Serialise the very short claim decision, not provider execution. Global
    # concurrency is deliberately one, including multiple Worker processes.
    await session.execute(text('SELECT pg_advisory_xact_lock(20261117, 0)'))
    from app.models import ScoringStep
    scoring_live=await session.scalar(select(ScoringStep.id).where(ScoringStep.status=='RUNNING',
        ScoringStep.lease_expires_at>now).limit(1))
    if scoring_live:
        await session.commit()
        return None
    live=await session.scalar(select(AnalysisStep.id).where(AnalysisStep.status=='RUNNING',
        AnalysisStep.lease_expires_at>now).limit(1))
    if live:
        await session.commit()
        return None
    rows=(await session.execute(select(AnalysisStep,ProjectAnalysis).join(ProjectAnalysis,
        AnalysisStep.run_id==ProjectAnalysis.id).join(Project,Project.id==AnalysisStep.project_id).where(
        ProjectAnalysis.status.in_(ACTIVE),Project.archived_at.is_(None),
        AnalysisStep.status.in_(('QUEUED','RETRY_WAIT','RUNNING'))).order_by(AnalysisStep.id)
        .with_for_update(of=AnalysisStep,skip_locked=True))).all()
    priorities=await _split_priorities(session,rows)
    rows.sort(key=lambda pair:priorities.get(pair[0].id,(pair[0].id,0,pair[0].id)))
    for step,run in rows:
        if run.status not in ACTIVE:
            continue
        if step.next_retry_at and step.next_retry_at>now:
            continue
        done=set((await session.scalars(select(AnalysisStep.step_key).where(
            AnalysisStep.run_id==run.id,AnalysisStep.status=='SUCCEEDED'))).all())
        if not set(step.requires).issubset(done):
            continue
        if step.status=='RUNNING':
            step.retry_count+=1
            step.attempts=[*(step.attempts or []),{'attempt':step.attempt_count,'outcome':'OUTCOME_UNKNOWN',
                'at':now.isoformat(),'reason':'lease_expired; external billing cannot be guaranteed exactly once'}]
            if step.retry_count>=3:
                step.status='BLOCKED';step.error_category='WORKER_LOST'
                step.error='本阶段连续丢失 Worker 租约；已停止自动接管，请检查 Worker 后恢复。'
                step.lease_owner=None;step.lease_expires_at=None;step.fencing_token+=1
                run.status='BLOCKED';run.error=step.error;run.updated_at=now
                continue
        used=dict(run.usage or {})
        call_limit = (run.budget or {}).get('max_model_calls')
        if step.uses_model and call_limit is not None and used.get('model_calls',0)>=call_limit:
            run.status='PAUSED_BUDGET'; run.error='已达到本次分析调用预算；已完成阶段保留。'
            run.updated_at=now
            continue
        attempts=list(step.attempts or [])
        step.attempt_count+=1; step.fencing_token+=1
        step.attempts=attempts
        step.status='RUNNING';step.lease_owner=owner;step.lease_expires_at=now+timedelta(seconds=lease_seconds)
        step.started_at=step.started_at or now;step.updated_at=now;step.next_retry_at=None
        run.status='RUNNING';run.started_at=run.started_at or now;run.updated_at=now
        if step.uses_model:
            used['model_calls']=used.get('model_calls',0)+1
            run.usage=used
        claim=Claim(step.id,step.step_key,run.id,run.workspace_id,run.project_id,step.stage,
                    dict(step.input_json),owner,step.fencing_token)
        await session.commit()
        return claim
    await session.commit()
    return None

async def _owned(session, claim, now):
    await session.execute(text('SELECT pg_advisory_xact_lock(20261117, 0)'))
    # Consistent run -> step lock order also used by controls/publication.
    run=await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.id==claim.run_id,
        ProjectAnalysis.workspace_id==claim.workspace_id).with_for_update().execution_options(populate_existing=True))
    step=await session.scalar(select(AnalysisStep).where(AnalysisStep.id==claim.step_id).with_for_update()
        .execution_options(populate_existing=True))
    if not run or not step or run.status not in ACTIVE or step.status!='RUNNING' or \
        step.lease_owner!=claim.owner or step.fencing_token!=claim.fencing_token or \
        not step.lease_expires_at or step.lease_expires_at<=now:
        return None,None
    return step,run

async def heartbeat(session,claim,lease_seconds=45,now=None):
    now=now or utcnow();step,run=await _owned(session,claim,now)
    if not step:
        await session.commit();return False
    step.lease_expires_at=now+timedelta(seconds=lease_seconds)
    # Heartbeat is liveness, not meaningful progress; don't update progress time.
    await session.commit();return True

async def complete_step(session,claim,artifact,usage=None,request_meta=None,now=None):
    now=now or utcnow();step,run=await _owned(session,claim,now)
    if not step:
        await session.commit();return False
    step.status='SUCCEEDED';step.output_json=artifact;step.usage=usage or {'known':False}
    step.request_meta=request_meta or {};step.error=None;step.error_category=None
    step.attempts=[*(step.attempts or []),{'attempt':step.attempt_count,'outcome':'SUCCEEDED',
        'at':now.isoformat(),'usage':step.usage,'request_meta':step.request_meta}]
    step.lease_owner=None;step.lease_expires_at=None;step.updated_at=now
    run.status='RUNNING';run.error=None;run.updated_at=now
    await record_span(session,run,step,'OK')
    await session.commit();return True

def _retry_allowed(step, category):
    """Evaluate the already-recorded failure in this finite automatic window.

    Manual resume resets retry_count but retains attempts. Controls can also
    append uncounted UNKNOWN records, while expired leases consume the total
    failure limit. Do not confuse transport failures with content repair tries.
    """
    repairable = {'INVALID_SCHEMA', 'INVALID_CITATION', 'OUTPUT_TRUNCATED'}
    retryable = {'RATE_LIMITED', 'NETWORK_TRANSIENT', 'PROVIDER_5XX', 'REQUEST_TIMEOUT'}
    total = step.retry_count
    if category not in retryable | repairable or type(total) is not int or not 0 < total < 3:
        return False
    if not isinstance(step.attempts, list):
        return False
    remaining, content_failures = total, 0
    for record in reversed(step.attempts):
        if not isinstance(record, dict) or not isinstance(record.get('outcome'), str) or not record['outcome']:
            return False  # Incomplete history cannot grant an extra attempt.
        outcome = record['outcome']
        if outcome == 'SUCCEEDED':
            return remaining < total and content_failures < 2
        if outcome == 'OUTCOME_UNKNOWN':
            reason = record.get('reason')
            if reason in ('pause', 'resume', 'cancel'):
                continue  # These control records did not increment retry_count.
            if not isinstance(reason, str) or not (reason == 'lease_expired' or reason.startswith('lease_expired;')):
                return False
        remaining -= 1
        content_failures += outcome in repairable
        if content_failures >= 2:
            return False
        if remaining == 0:
            return True
    return False


async def fail_step(session,claim,category,message,retry_after=None,request_id=None,now=None,usage=None,request_meta=None):
    now=now or utcnow();step,run=await _owned(session,claim,now)
    if not step:
        await session.commit();return False
    # Messages originate in controlled adapters; never persist HTTP bodies/keys.
    step.error_category=category;step.error=str(message)[:600];step.retry_count+=1
    step.usage=usage or {'known':False};step.request_meta=request_meta or {}
    step.attempts=[*(step.attempts or []),{'attempt':step.attempt_count,'outcome':category,
        'at':now.isoformat(),'request_id':request_id,'usage':step.usage,'request_meta':step.request_meta}]
    if _retry_allowed(step,category):
        step.status='RETRY_WAIT';run.status='RETRY_WAIT'
        step.next_retry_at=now+timedelta(seconds=max(float(retry_after or 0),min(300,5*2**step.retry_count)))
    else:
        step.status='BLOCKED';run.status='PAUSED_BUDGET' if category=='BUDGET_EXHAUSTED' else 'BLOCKED'
    step.lease_owner=None;step.lease_expires_at=None;step.updated_at=now
    run.error=step.error;run.updated_at=now
    await record_span(session,run,step,'ERROR')
    await session.commit();return True

async def control_run(session,run_id,workspace_id,action, *, commit=True):
    await session.execute(text('SELECT pg_advisory_xact_lock(20261117, 0)'))
    run=await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.id==run_id,
        ProjectAnalysis.workspace_id==workspace_id).with_for_update().execution_options(populate_existing=True))
    if not run:raise LookupError('分析任务不存在')
    if action not in {'pause','resume','cancel'}:raise ValueError('未知操作')
    if run.status == 'WAITING_WORKER' and action != 'cancel':
        raise ValueError('已排队等待后台升级，可以取消')
    if run.status in ('COMPLETED','CANCELLED','FAILED_FINAL'):
        raise ValueError('任务已结束；如需重新分析请创建新任务')
    rows=(await session.scalars(select(AnalysisStep).where(AnalysisStep.run_id==run.id,
        AnalysisStep.status.notin_(('SUCCEEDED','SUPERSEDED'))).with_for_update())).all()
    for step in rows:
        if step.status=='RUNNING':
            step.attempts=[*(step.attempts or []),{'attempt':step.attempt_count,'outcome':'OUTCOME_UNKNOWN',
                'reason':action,'at':utcnow().isoformat()}]
        step.fencing_token+=1;step.lease_owner=None;step.lease_expires_at=None
        step.status='CANCELLED' if action=='cancel' else 'QUEUED'
        step.next_retry_at=None
        if action=='resume':step.retry_count=0
    run.status={'pause':'PAUSED_USER','resume':'QUEUED','cancel':'CANCELLED'}[action]
    run.updated_at=utcnow()
    if action=='resume':run.error=None
    if action=='cancel':run.finished_at=utcnow()
    if commit:
        await session.commit()
    else:
        await session.flush()
    return run

async def split_failed_source_stage(session,run_id,workspace_id,step_id,source_groups):
    """Explicit bounded recovery retaining the failed row, never a provider call.

    This maintenance operation does not change model settings or recursively
    resplit children. The caller supplies complete frozen source groups.
    """
    from copy import deepcopy

    from app.services import project_stage_splits as splits
    await session.execute(text('SELECT pg_advisory_xact_lock(20261117, 0)'))
    run=await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.id==run_id,
        ProjectAnalysis.workspace_id==workspace_id).with_for_update().execution_options(populate_existing=True))
    if not run:raise LookupError('分析任务不存在')
    rows=(await session.scalars(select(AnalysisStep).where(AnalysisStep.run_id==run.id,
        AnalysisStep.workspace_id==workspace_id).with_for_update().execution_options(populate_existing=True))).all()
    parent=next((row for row in rows if row.id==step_id),None)
    if not parent:raise LookupError('分析阶段不存在')
    context={'run_id':run.id,'workspace_id':run.workspace_id,'project_id':run.project_id,
        'target_sha':run.target_sha,'model_config_fingerprint':run.model_config_fingerprint,
        'pipeline_version':run.pipeline_version,'prompt_bundle_version':run.prompt_bundle_version,
        'source_splits':deepcopy((run.result_meta or {}).get('source_splits',[]))}
    artifacts={row.step_key:row.output_json for row in rows if row.status=='SUCCEEDED'}
    previous=[plan for plan in context['source_splits'] if plan.get('parent',{}).get('step_key')==parent.step_key]
    if previous:
        if len(previous)!=1 or previous[0]['source_groups']!=source_groups:
            raise ValueError('已封存的拆分计划不能更换分组')
        plan=previous[0]
        splits.validate_plan(plan,context,artifacts)
        frozen=plan['parent']
        if parent.status!='SUPERSEDED' or parent.output_json is not None or any(
            getattr(parent,key)!=value for key,value in frozen.items()
            if key not in ('status','attempts_sha256')) or splits.row_fingerprint(parent.attempts)!=frozen['attempts_sha256']:
            raise ValueError('原拆分父阶段或失败历史已改变，不能重复恢复')
        await session.commit()
        return deepcopy(plan)
    if run.status!='BLOCKED' or parent.status!='BLOCKED' or parent.stage!='analyze_module' or \
            parent.error_category!='OUTPUT_TRUNCATED' or parent.retry_count<2 or parent.output_json is not None or \
            sum(a.get('outcome')=='OUTPUT_TRUNCATED' for a in parent.attempts or [])<2:
        raise ValueError('仅对已耗尽截断修复的未完成源码阶段进行显式拆分')
    downstream={'module','reuse_module','cross_module','explain_section','report','extract_candidates','candidates'}
    if any(row.status=='RUNNING' or row.stage in downstream or
           (row.id!=parent.id and row.status=='BLOCKED') for row in rows) or \
            run.report_published_at is not None or run.report is not None or run.report_status!='NONE':
        raise ValueError('需要无运行中请求且尚未组装报告模块的唯一失败现场')
    if parent.lease_owner is not None or parent.lease_expires_at is not None:
        raise ValueError('原阶段仍有执行租约，不能拆分')
    parent_value={column.name:deepcopy(getattr(parent,column.name)) for column in parent.__table__.columns}
    plan=splits.create_plan(context,parent_value,source_groups,artifacts=artifacts)
    specs=splits.plan_specs(plan)
    if any(spec['key'] in {row.step_key for row in rows} for spec in specs):
        raise ValueError('新拆分节点与已有步骤冲突')
    meta={**(run.result_meta or {}),'source_splits':[*context['source_splits'],plan]}
    meta.pop('error_category',None)
    now=utcnow()
    parent.status='SUPERSEDED';parent.fencing_token+=1;parent.updated_at=now
    parent.next_retry_at=None
    run.result_meta=meta;run.status='QUEUED';run.error=None;run.updated_at=now
    await ensure_steps(session,run,specs)
    await session.commit()
    return deepcopy(plan)


async def record_span(session,run,step,status):
    from app.models import Trace
    from app.services.tracing import add_span
    trace_id=(run.result_meta or {}).get('trace_id')
    trace=await session.get(Trace,trace_id) if trace_id else None
    if trace:
        add_span(session,trace,f'{step.stage} · attempt {step.attempt_count}',
            int((step.request_meta or {}).get('elapsed_ms') or 0),status=status,
            detail={'run_id':run.id,'step_id':step.id,'step_key':step.step_key,'attempt':step.attempt_count,
                'input_hash':step.input_hash,'usage':step.usage,'request_meta':step.request_meta,
                'error_category':step.error_category,'cost_known':False})
        if run.status in ('BLOCKED','PAUSED_BUDGET'):
            trace.status='FAILED';trace.fail_reason=run.error
