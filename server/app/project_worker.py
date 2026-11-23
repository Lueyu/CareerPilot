"""Independent durable project worker. Run: python -m app.project_worker."""
import argparse
import asyncio
import logging
import os
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal, engine
from app.models import AnalysisStep, AppSetting, MemoryItem, Project, ProjectAnalysis, WorkerHeartbeat
from app.services import project_autopilot_engine as analysis
from app.services import project_jobs as jobs
from app.services.project_autopilot_config import CONSENT_VERSION, model_fingerprint, runtime_config
from app.services.project_candidates import invalidate_changed_sources, publish_candidates
from app.services.project_context import project_kind
from app.services.project_memory import PROJECT_TYPES, active_project_memories
from app.services.project_run_compatibility import assert_compatible
from app.services.tracing import emit_event

log=logging.getLogger('careerpilot.project_worker')

def _repair_diagnostic(step):
    """Keep all unresolved repair categories across transport failures, safely.

    Attempt bodies and arbitrary error fields are never echoed into prompts.
    A successful attempt closes the preceding repair history for this step.
    """
    if getattr(step,'status',None)=='SUCCEEDED':return None
    messages={
        'OUTPUT_TRUNCATED':'上次模型输出被截断；请保持完整结构并使用更精简的输出。',
        'INVALID_SCHEMA':'上次模型输出未通过完整 JSON 结构校验；请严格满足要求的结果结构。',
        'INVALID_CITATION':'上次模型引用未通过来源、版本、行号、依赖路径、claim_ids 或事实类型边界校验；请核对本阶段允许的来源、路径与结论关联，不将文档声明或推断提升为代码事实。',
    }
    selected=[]
    current=getattr(step,'error_category',None)
    if isinstance(current,str) and current in messages:selected.append(current)
    attempts=getattr(step,'attempts',None)
    if isinstance(attempts,(list,tuple)):
        for attempt in reversed(attempts):
            if not isinstance(attempt,dict):continue
            outcome=attempt.get('outcome')
            if outcome=='SUCCEEDED':break
            if isinstance(outcome,str) and outcome in messages and outcome not in selected:
                selected.append(outcome)
            if len(selected)==len(messages):break
    return ' '.join(messages[category] for category in selected) if selected else None


def affected_paths(manifest,baseline):
    diff=manifest.get('diff') or {}
    if not diff.get('complete'):return None
    paths={p for c in diff.get('changes',[]) for p in (c.get('path'),c.get('previous_path')) if p}
    modules=[v for k,v in (baseline.get('artifacts') or {}).items() if k.startswith('module:')]
    changed=True
    while changed:
        before=len(paths)
        for module in modules:
            if paths and (not module.get('dependency_scope_complete') or paths.intersection(module.get('dependencies',[]))):
                paths.update(module.get('paths',[]))
        changed=len(paths)!=before
    return paths

async def context_for(session,run,project):
    rows=(await session.scalars(select(AnalysisStep).where(AnalysisStep.run_id==run.id,
        AnalysisStep.workspace_id==run.workspace_id,AnalysisStep.status=='SUCCEEDED'))).all()
    artifacts={r.step_key:r.output_json for r in rows}
    baseline=await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.project_id==project.id,
        ProjectAnalysis.workspace_id==run.workspace_id,ProjectAnalysis.report_status=='VALID',
        ProjectAnalysis.id<run.id).order_by(ProjectAnalysis.id.desc()).limit(1))
    previous={}
    if baseline:
        steps=(await session.scalars(select(AnalysisStep).where(AnalysisStep.run_id==baseline.id,
            AnalysisStep.workspace_id==run.workspace_id,AnalysisStep.status=='SUCCEEDED'))).all()
        previous={'snapshot':baseline.snapshot,'report':baseline.report,
            'artifacts':{s.step_key:s.output_json for s in steps}}
    info={k:getattr(project,k) for k in ('id','name','owner','repo','branch','repo_url')}
    info['kind']=project_kind(project)
    memories=await active_project_memories(session,run.workspace_id,project.id)
    historical=(await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id==run.workspace_id,
        MemoryItem.mtype.in_(PROJECT_TYPES),MemoryItem.meta['project_id'].as_integer()==project.id)
        .order_by(MemoryItem.id.desc()).limit(100))).all()
    def bounded_memory(rows,limit=4000):
        result=[];used=0
        for m in rows:
            item={'id':m.id,'content':m.content[:800],'content_truncated':len(m.content)>800,
                'fact_key':(m.meta or {}).get('fact_key'),'fact_type':(m.meta or {}).get('fact_type'),
                'status':m.status,'freshness':m.freshness,'user_corrected':m.source=='USER',
                'source_revision':(m.meta or {}).get('source_revision'),
                'note':'历史对照资料；必须核对当前源码，不等于个人贡献或当前项目事实。'}
            if used+len(str(item))>limit:break
            result.append(item);used+=len(str(item))
        return result
    context={'run_id':run.id,'workspace_id':run.workspace_id,'project_id':project.id,'project':info,
        'pipeline_version':run.pipeline_version,'prompt_bundle_version':run.prompt_bundle_version,
        'mode':run.mode,'target_sha':run.target_sha,'base_sha':baseline.source_revision if baseline else None,
        'baseline':previous,'model_config_fingerprint':run.model_config_fingerprint,'budget':run.budget,
        'claim_reviews':(run.result_meta or {}).get('claim_reviews', []),
        'source_splits':(run.result_meta or {}).get('source_splits', []),
        'claim_input_contract':(run.result_meta or {}).get('claim_input_contract', 'legacy'),
        'confirmed_memories':bounded_memory(memories),'existing_candidates':bounded_memory(historical),
        'history_context_scope':'有界项目历史摘要；完整忽略/确认/替代约束由数据库审核服务执行。'}
    return context,artifacts

async def reconcile(factory=SessionLocal):
    async with factory() as session:
        ids=(await session.scalars(select(ProjectAnalysis.id).where(ProjectAnalysis.status.in_(jobs.ACTIVE),
            ProjectAnalysis.pipeline_version!='legacy').order_by(ProjectAnalysis.id))).all()
    for run_id in ids:
        async with factory() as session:
            # Match controls/claim lock ordering and never call external services here.
            from sqlalchemy import text
            await session.execute(text('SELECT pg_advisory_xact_lock(20261117, 0)'))
            run=await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.id==run_id).with_for_update())
            if not run or run.status not in jobs.ACTIVE:continue
            project=await session.get(Project,run.project_id)
            if not project or project.archived_at:
                run.status='CANCELLED';await session.commit();continue
            context,artifacts=await context_for(session,run,project)
            try:assert_compatible(run,artifacts)
            except analysis.StageError as exc:
                run.status='BLOCKED';run.error=str(exc)
                run.result_meta={**(run.result_meta or {}),'error_category':exc.category}
                await session.commit();continue
            resolved=artifacts.get('resolve')
            if resolved:
                observed = resolved.get('target_sha')
                if observed and not run.target_sha and project.generation == run.generation:
                    project.last_seen_sha = observed
                run.target_sha=observed;project.branch=resolved.get('branch',project.branch)
            if 'manifest' in artifacts and not (run.result_meta or {}).get('memory_sources_checked'):
                await invalidate_changed_sources(session,project,
                    affected_paths(artifacts['manifest'],context.get('baseline') or {}),run.target_sha)
                run.result_meta={**(run.result_meta or {}),'memory_sources_checked':True}
            if 'impact' in artifacts and not (run.result_meta or {}).get('memory_impact_checked'):
                impact=artifacts['impact']
                affected=None if impact.get('unknown_dependencies') else set(impact.get('affected_paths',[]))
                await invalidate_changed_sources(session,project,affected,run.target_sha)
                run.result_meta={**(run.result_meta or {}),'memory_impact_checked':True}
            published=artifacts.get('report')
            if published and run.report_published_at is None:
                run.source_revision=published.get('source_revision',run.target_sha)
                run.snapshot=published.get('snapshot');run.report=published.get('report')
                run.coverage_status=published.get('coverage_status','PARTIAL')
                run.report_status='VALID' if run.coverage_status=='COMPLETE' else 'PARTIAL'
                run.report_published_at=jobs.utcnow()
                if run.report_status=='VALID' and project.generation==run.generation:
                    project.latest_valid_analysis_id=run.id
                    if not project.last_seen_sha:project.last_seen_sha=run.source_revision
                    if not (run.result_meta or {}).get('memory_sources_checked'):
                        await invalidate_changed_sources(session,project,None,run.source_revision)
                    await emit_event(session,run.workspace_id,'project.report.saved',{'coverage':'COMPLETE'},'project_analysis',run.id)
                elif run.report_status=='PARTIAL':
                    run.status='BLOCKED';run.error='部分模块或章节未完成，已保存阶段结果；完整报告尚未生成。'
            if 'candidates' in artifacts and run.report_status=='VALID' and run.status in jobs.ACTIVE:
                await publish_candidates(session,run,artifacts['candidates'].get('candidates',[]))
                run.status='COMPLETED';run.finished_at=jobs.utcnow();run.updated_at=jobs.utcnow()
                from app.models import Trace
                from app.services.tracing import finish_trace
                trace_id=(run.result_meta or {}).get('trace_id')
                trace=await session.get(Trace,trace_id) if trace_id else None
                if trace:await finish_trace(session,trace,'SUCCESS')
                await emit_event(session,run.workspace_id,'project.analysis.completed',{'memory_status':'PENDING_REVIEW'},'project_analysis',run.id)
            if run.status in jobs.ACTIVE:
                try:await jobs.ensure_steps(session,run,analysis.plan_steps(context,artifacts))
                except analysis.StageError as exc:
                    run.status='PAUSED_BUDGET' if exc.category=='BUDGET_EXHAUSTED' else 'BLOCKED'
                    run.error=str(exc)[:600]
            await session.commit()
            if run.status=='COMPLETED' and project.pending_update:
                # Scheduler checks latest head once, coalescing all intervening commits.
                project.next_check_at=jobs.utcnow()
                await session.commit()

async def worker_heartbeat(factory,worker_id,step_id=None):
    async with factory() as session:
        stmt=insert(WorkerHeartbeat).values(worker_id=worker_id,pid=os.getpid(),last_seen_at=jobs.utcnow(),current_step_id=step_id)
        await session.execute(stmt.on_conflict_do_update(index_elements=['worker_id'],set_={
            'last_seen_at':jobs.utcnow(),'current_step_id':step_id}))
        await session.commit()

async def execute_claim(claim,worker_id,factory=SessionLocal,lease_seconds=45,heartbeat_seconds=10):
    async with factory() as session:
        if not await jobs.heartbeat(session,claim,lease_seconds):return
        run=await session.get(ProjectAnalysis,claim.run_id);project=await session.get(Project,claim.project_id)
        one_run_consent = (getattr(run, 'result_meta', None) or {}).get('source_transfer_consent') == CONSENT_VERSION
        try:config,credentials,settings=await runtime_config(session,claim.workspace_id,not one_run_consent)
        except PermissionError:
            await jobs.fail_step(session,claim,'CONSENT_REQUIRED','请先确认此项目的源码传输范围');return
        current_fp=model_fingerprint(config)
        if run.model_config_fingerprint and current_fp!=run.model_config_fingerprint:
            await jobs.fail_step(session,claim,'CONFIG_CHANGED','模型配置已改变；本次任务保留原检查点，请新建完整分析');return
        context,artifacts=await context_for(session,run,project)
        try:assert_compatible(run,artifacts)
        except analysis.StageError as exc:
            await jobs.fail_step(session,claim,exc.category,str(exc));return
        step=await session.get(AnalysisStep,claim.step_id)
        repair=_repair_diagnostic(step)
        runtime=analysis.RuntimeContext(workspace_id=claim.workspace_id,project_id=claim.project_id,
            llm_config=config,credentials=credentials,max_source_bytes=run.budget.get('max_source_bytes'),
            request_timeout=run.budget.get('request_timeout',180),input_chars=run.budget.get('input_chars',40000),
            transport_mode=(getattr(run,'result_meta',None) or {}).get('transport_mode','json'),
            stage_timeout=(getattr(run,'result_meta',None) or {}).get('stage_timeout_seconds'))
        # Recheck immediately before dispatch; a queued cancellation must not
        # launch a new external request merely because a lease was once valid.
        if not await jobs.heartbeat(session,claim,lease_seconds):return
    task=asyncio.create_task(analysis.execute_stage(claim.stage,{'context':context,'payload':claim.payload,'artifacts':artifacts,
        'repair_diagnostic':repair},runtime))
    async def keep_alive():
        while True:
            await asyncio.sleep(heartbeat_seconds)
            async with factory() as session:
                valid=await jobs.heartbeat(session,claim,lease_seconds)
            await worker_heartbeat(factory,worker_id,claim.step_id)
            if not valid:
                task.cancel();return
    keeper=asyncio.create_task(keep_alive())
    started=time.monotonic()
    try:
        result=await task
        async with factory() as session:
            await jobs.complete_step(session,claim,result['artifact'],result.get('usage'),
                {**result.get('request_meta',{}),'elapsed_ms':int((time.monotonic()-started)*1000)})
    except asyncio.CancelledError:
        # Leave current lease for takeover on process shutdown; controls already fenced it.
        pass
    except Exception as exc:
        category=getattr(exc,'category','INTERNAL_ERROR')
        message=str(exc) if getattr(exc,'safe_for_user',False) else '本阶段失败，已保留完成步骤；请检查高级详情。'
        async with factory() as session:
            await jobs.fail_step(session,claim,category,message,getattr(exc,'retry_after',None),getattr(exc,'request_id',None),
                usage=getattr(exc,'usage',None),request_meta={**(getattr(exc,'request_meta',None) or {}),
                    'elapsed_ms':int((time.monotonic()-started)*1000)})
        log.warning('run=%s step=%s category=%s',claim.run_id,claim.step_id,category)
    finally:
        keeper.cancel()
        with suppress(asyncio.CancelledError):await keeper

async def serve(poll_seconds=2,lease_seconds=None,heartbeat_seconds=10,once=False,factory=SessionLocal,*,ready_file=None):
    # Allow longer host scheduling stalls during streamed project analysis.
    # An explicit lease override still applies to both kinds of job.
    scoring_lease = 45 if lease_seconds is None else lease_seconds
    project_lease = 180 if lease_seconds is None else lease_seconds
    if heartbeat_seconds >= min(scoring_lease, project_lease):
        raise ValueError('heartbeat must be shorter than lease')
    worker_id=f'{os.getpid()}-{analysis.PROMPT_BUNDLE_VERSION}-{uuid4().hex}'

    async def announce_worker():
        nonlocal ready_file
        await worker_heartbeat(factory, worker_id)
        if ready_file is not None:
            ready_file.touch(exist_ok=False)
            ready_file = None

    # An upgraded worker may be launched by the ordinary personal startup
    # during the drain. Advertise its version without touching old checkpoints.
    while True:
        async with factory() as session:
            handoff = await session.get(AppSetting, jobs.HANDOFF_KEY)
        if not handoff or handoff.value.get('target_prompt') != analysis.PROMPT_BUNDLE_VERSION:
            break
        await announce_worker()
        if once:
            return
        await asyncio.sleep(poll_seconds)
    async def scheduler_loop():
        from app.services.project_scheduler import tick
        while True:
            try:await tick(factory)
            except Exception:log.warning('scheduler check failed; persisted due time retained')
            await asyncio.sleep(30)
    scheduler=asyncio.create_task(scheduler_loop()) if not once else None
    try:
        while True:
            await announce_worker()
            from app.services import assessment_jobs, assessment_worker
            await assessment_worker.reconcile(factory)
            async with factory() as session:
                grading=await assessment_jobs.claim_step(session,worker_id,scoring_lease)
            if grading:
                await assessment_worker.execute_claim(grading,worker_id,factory,scoring_lease,heartbeat_seconds)
                await assessment_worker.reconcile(factory)
                if once:return
                continue
            from app.services.study_notes import process_next_note_job
            if await process_next_note_job(factory):
                if once: return
                continue
            await reconcile(factory)
            async with factory() as session:claim=await jobs.claim_step(session,worker_id,project_lease)
            if claim:
                await execute_claim(claim,worker_id,factory,project_lease,heartbeat_seconds)
                await reconcile(factory)
            if once:return
            if not claim:await asyncio.sleep(poll_seconds)
    finally:
        if scheduler:
            scheduler.cancel()
            with suppress(asyncio.CancelledError):await scheduler

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--poll-seconds',type=float,default=2)
    parser.add_argument('--lease-seconds',type=float,help='override the lease for both scoring and project stages')
    parser.add_argument('--heartbeat-seconds',type=float,default=10)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--ready-file', type=Path, help='create this file after the first persisted heartbeat')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(name)s %(levelname)s %(message)s')
    async def run():
        try:await serve(**vars(args))
        finally:await engine.dispose()
    asyncio.run(run())

if __name__=='__main__':main()
