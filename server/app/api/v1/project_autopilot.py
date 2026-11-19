"""Short, workspace-scoped HTTP operations over the durable local project worker."""
from datetime import timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import AppSetting, AnalysisStep, MemoryItem, Project, ProjectAnalysis, Workspace, WorkerHeartbeat
from app.services import project_candidates, project_jobs
from app.services.project_autopilot_config import (
    CONSENT_VERSION, SOURCE_NOTICE, model_fingerprint, runtime_config, settings_for,
)
from app.services.project_context import project_kind
from app.services.project_memory import PROJECT_TYPES, source_excerpts
from app.services.project_scheduler import versions

router = APIRouter(tags=['project-autopilot'])
utcnow = project_jobs.utcnow


class BudgetIn(BaseModel):
    max_model_calls: int | None = Field(default=None, ge=1, le=10000)
    max_source_bytes: int | None = Field(default=None, ge=1, le=1073741824)


class SettingsIn(BaseModel):
    enabled: bool
    acknowledge_source_transfer: bool = False
    budget: BudgetIn | None = None
    check_interval_hours: float | None = Field(default=None, ge=1, le=720)


class ReanalyzeIn(BaseModel):
    acknowledge_source_transfer: bool = False
    mode: Literal['full'] = 'full'
    request_key: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=120)


class ReviewItem(BaseModel):
    candidate_id: int = Field(gt=0)
    expected_revision: int = Field(ge=1)
    action: Literal['confirm', 'ignore']
    edited_content: str | None = Field(default=None, min_length=1, max_length=20000)


class ReviewIn(BaseModel):
    items: list[ReviewItem] = Field(min_length=1, max_length=200)


async def owned_project(session, workspace_id, project_id, allow_archived=False):
    row = await session.scalar(select(Project).where(Project.id == project_id, Project.workspace_id == workspace_id))
    if row is None or (row.archived_at and not allow_archived):
        raise HTTPException(404, '项目不存在或已移除')
    return row


async def owned_run(session, workspace_id, run_id):
    row = await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.id == run_id,
        ProjectAnalysis.workspace_id == workspace_id).execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, '分析任务不存在')
    return row


async def worker_view(session):
    seen = await session.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    return {'online': bool(seen and seen >= utcnow() - timedelta(seconds=45)),
            'last_seen_at': seen.isoformat() if seen else None}


def run_actions(status, error_code=None):
    if status == 'WAITING_WORKER':
        return ['cancel']
    if status in project_jobs.ACTIVE:
        return ['pause', 'cancel']
    if status in ('PAUSED_USER', 'PAUSED_BUDGET', 'BLOCKED'):
        return ['cancel'] if error_code == 'CONFIG_CHANGED' else ['resume', 'cancel']
    return []


async def run_view(session, row, include_steps=False):
    steps = (await session.scalars(select(AnalysisStep).where(AnalysisStep.run_id == row.id,
        AnalysisStep.workspace_id == row.workspace_id).order_by(AnalysisStep.id))).all()
    current_steps = [s for s in steps if s.status != 'SUPERSEDED']
    failed = next((s for s in current_steps if s.error_category and s.status != 'SUCCEEDED'), None)
    current = next((s for s in current_steps if s.status == 'RUNNING'), None) or failed or next((s for s in current_steps if s.status != 'SUCCEEDED'), None)
    status = {'SUCCESS': 'COMPLETED', 'FAILED': 'FAILED_FINAL', 'READY': 'BLOCKED'}.get(row.status, row.status)
    code = failed.error_category if failed else (row.result_meta or {}).get('error_category') or status
    error = {'code': code, 'message': row.error[:600], 'recoverable': bool('resume' in run_actions(status, code))} if row.error else None
    if row.status == 'READY' and row.pipeline_version == 'legacy':
        error = {'code':'LEGACY_TASK', 'message':'旧版待启动任务不会由后台执行，请完整重新分析。', 'recoverable':False}
    modules = [s for s in steps if s.stage in ('module', 'reuse_module')]
    completed_modules = sum(s.status == 'SUCCEEDED' for s in modules)
    coverage_status = 'PARTIAL' if completed_modules and row.coverage_status != 'COMPLETE' else row.coverage_status
    view = {'id': row.id, 'project_id': row.project_id, 'status': status,
        'stage': current.stage if current else None, 'coverage_status': coverage_status,
        'completed_modules': completed_modules, 'total_modules': len(modules),
        'error': error, 'allowed_actions': run_actions(status, code) if row.pipeline_version != 'legacy' else [],
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'updated_at': row.updated_at.isoformat() if row.updated_at else None,
        'trace_id': (row.result_meta or {}).get('trace_id'),
        'report_id': row.id if row.report_status == 'VALID' else None}
    if include_steps:
        view['steps'] = [{'id':s.id, 'run_id':s.run_id, 'key': s.step_key, 'stage': s.stage, 'status': s.status,
            'input_hash':s.input_hash, 'usage':s.usage, 'request_meta':s.request_meta, 'attempts':s.attempts,
            'attempt_count': s.attempt_count, 'retry_count': s.retry_count,
            'next_retry_at': s.next_retry_at.isoformat() if s.next_retry_at else None,
            'error_category': s.error_category,
            'updated_at': s.updated_at.isoformat() if s.updated_at else None} for s in steps]
        view['usage'] = {'model_calls': (row.usage or {}).get('model_calls', 0)}
        view['budget'] = row.budget
    return view


async def latest_run(session, project):
    return await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.project_id == project.id,
        ProjectAnalysis.workspace_id == project.workspace_id).order_by(ProjectAnalysis.id.desc()).limit(1))


async def valid_report(session, project):
    if not project.latest_valid_analysis_id:
        return None
    row = await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.id == project.latest_valid_analysis_id,
        ProjectAnalysis.project_id == project.id, ProjectAnalysis.workspace_id == project.workspace_id))
    return row if row and row.report_status == 'VALID' and row.coverage_status == 'COMPLETE' and row.report else None


def freshness(project, row):
    if not project.last_seen_sha or not row.source_revision:
        return 'UNKNOWN'
    return 'CURRENT' if project.last_seen_sha == row.source_revision else 'NEEDS_REVIEW'


def report_summary(project, row):
    if not row:
        return None
    return {'id': row.id, 'as_of': (row.report_published_at or row.finished_at or row.created_at).isoformat(),
        'summary': (row.report or {}).get('summary', ''), 'source_revision': row.source_revision,
        'coverage_status': row.coverage_status, 'freshness': freshness(project, row)}


async def autopilot_view(session, project):
    settings = await settings_for(session, project.workspace_id)
    consent_required = not settings['enabled'] or settings.get('consent_version') != CONSENT_VERSION
    worker = await worker_view(session)
    run = await latest_run(session, project)
    task = await run_view(session, run) if run else None
    report = await valid_report(session, project)
    pending = await session.scalar(select(func.count()).select_from(MemoryItem).where(
        MemoryItem.workspace_id == project.workspace_id, MemoryItem.mtype.in_(PROJECT_TYPES),
        MemoryItem.status == 'PENDING_REVIEW', MemoryItem.meta['project_id'].as_integer() == project.id))
    actions = ['remove'] if not project.archived_at else []
    if not project.archived_at and project_kind(project) != 'manual':
        actions.append('ai_scan')
        if not consent_required:
            actions += ['check_updates', 'reanalyze']
    if project_kind(project) == 'manual':
        label = '手工登记的项目经历'
    elif task and task['status'] in project_jobs.ACTIVE and not worker['online']:
        label = '已排队，等待本地后台服务启动'
    elif task:
        label = {'WAITING_WORKER':'等待后台升级，扫描已排队', 'QUEUED':'已导入，等待分析', 'RUNNING':'正在了解项目',
            'RETRY_WAIT':'暂时无法继续，进度已保存', 'COMPLETED':'已完成，可查看报告',
            'PAUSED_USER':'已暂停，进度已保存', 'PAUSED_BUDGET':'达到自动分析预算，进度已保存',
            'BLOCKED':'暂时无法继续，进度已保存', 'FAILED_FINAL':'暂时无法继续，进度已保存',
            'CANCELLED':'分析已取消，历史记录保留'}.get(task['status'], '正在读取任务状态')
        if task['coverage_status'] == 'PARTIAL' and task['completed_modules'] and task['status'] in ('RUNNING','QUEUED','COMPLETED'):
            label = '已完成部分分析，正在处理剩余内容' if task['status'] != 'COMPLETED' else '已完成部分分析，仍有内容未覆盖'
        elif report and report.id != run.id and task['status'] in project_jobs.ACTIVE:
            label = '检测到更新，正在更新报告'
    else:
        label = '已完成，可查看报告' if report else '可开始 AI 扫描'
    return {'user_status':label, 'enabled':settings['enabled'], 'consent_required':consent_required,
        'worker':worker, 'latest_run':task, 'latest_valid_report':report_summary(project, report),
        'pending_candidate_count':pending or 0, 'allowed_actions':actions}


async def settings_view(session, workspace_id):
    config, _, settings = await runtime_config(session, workspace_id, require_consent=False)
    profiles = config['auto_profiles']
    models = [f'{label} {profiles[tier]["model"]}' for tier, label in (('flash', '快速'), ('strong', '深入'))
              if profiles.get(tier) and profiles[tier].get('model')]
    return {**settings, 'source_notice':SOURCE_NOTICE, 'provider_display_name':'Auto · ' + ' / '.join(models) if models else '尚未配置模型'}


@router.get('/project-autopilot/settings')
async def get_settings(ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    return await settings_view(session, ws.id)


@router.put('/project-autopilot/settings')
async def put_settings(req: SettingsIn, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    if req.enabled and not req.acknowledge_source_transfer:
        raise HTTPException(400, '启用前必须明确确认源码传输范围')
    await session.execute(text('SELECT pg_advisory_xact_lock(20261119, :ws)'), {'ws':ws.id})
    before = await settings_for(session, ws.id)
    value = {**before, 'enabled':req.enabled, 'budget':req.budget.model_dump(exclude_none=True) if req.budget else before['budget']}
    if req.check_interval_hours is not None:
        value['check_interval_hours'] = req.check_interval_hours
    if req.enabled:
        value.update(consent_version=CONSENT_VERSION, consented_at=before.get('consented_at') or utcnow().isoformat())
    row = await session.get(AppSetting, f'project-autopilot:{ws.id}')
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=f'project-autopilot:{ws.id}', value=value))
    # Imported-but-not-consented runs carry provisional defaults. Persist the
    # explicitly accepted budget before enabling the worker to claim those runs.
    if req.enabled and not before['enabled']:
        pending_runs = (await session.scalars(select(ProjectAnalysis).where(ProjectAnalysis.workspace_id == ws.id,
            ProjectAnalysis.pipeline_version != 'legacy', ProjectAnalysis.status.in_(project_jobs.OPEN)))).all()
        for pending in pending_runs:
            if (pending.result_meta or {}).get('source_transfer_consent') != CONSENT_VERSION:
                pending.budget = dict(value['budget'])
    await session.commit()
    if req.enabled and not before['enabled']:
        config, _, _ = await runtime_config(session, ws.id)
        pipeline, prompt = versions()
        projects = (await session.scalars(select(Project).where(Project.workspace_id == ws.id, Project.archived_at.is_(None)))).all()
        for project in projects:
            if project_kind(project) == 'manual':
                continue
            existing = await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.project_id == project.id,
                ProjectAnalysis.workspace_id == ws.id, ProjectAnalysis.pipeline_version != 'legacy',
                ProjectAnalysis.status.in_(project_jobs.OPEN)).order_by(ProjectAnalysis.id.desc()).limit(1))
            if existing:
                consent_block = await session.scalar(select(AnalysisStep.id).where(AnalysisStep.run_id == existing.id,
                    AnalysisStep.workspace_id == ws.id, AnalysisStep.status == 'BLOCKED',
                    AnalysisStep.error_category == 'CONSENT_REQUIRED').limit(1))
                if existing.status == 'BLOCKED' and consent_block and existing.model_config_fingerprint == model_fingerprint(config):
                    await project_jobs.control_run(session, existing.id, ws.id, 'resume')
                continue
            if not await valid_report(session, project):
                await project_jobs.enqueue(session, project, uuid4().hex, budget=value['budget'],
                    model_config_fingerprint=model_fingerprint(config), pipeline_version=pipeline, prompt_bundle_version=prompt)
                project.next_check_at = utcnow() + timedelta(hours=value['check_interval_hours'])
        await session.commit()
    elif before['enabled'] and not req.enabled:
        ids = (await session.scalars(select(ProjectAnalysis.id).where(ProjectAnalysis.workspace_id == ws.id,
            ProjectAnalysis.pipeline_version != 'legacy', ProjectAnalysis.status.in_(project_jobs.ACTIVE),
            ProjectAnalysis.result_meta['source_transfer_consent'].as_string().is_distinct_from(CONSENT_VERSION)))).all()
        for run_id in ids:
            await project_jobs.control_run(session, run_id, ws.id, 'pause')
    return await settings_view(session, ws.id)


@router.get('/analysis-runs/{run_id}')
async def get_run(run_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    return await run_view(session, await owned_run(session, ws.id, run_id), include_steps=True)


@router.post('/analysis-runs/{run_id}/{action}')
async def control_run(run_id: int, action: Literal['pause','resume','cancel'], ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    run = await owned_run(session, ws.id, run_id)
    await owned_project(session, ws.id, run.project_id)
    if run.pipeline_version == 'legacy':
        raise HTTPException(409, '旧版分析不能恢复到新后台，请完整重新分析')
    view = await run_view(session, run)
    if action not in view['allowed_actions']:
        raise HTTPException(409, '当前任务不支持此操作；模型配置变化请取消旧任务并完整重新分析')
    if action == 'resume':
        try:
            one_run_consent = (run.result_meta or {}).get('source_transfer_consent') == CONSENT_VERSION
            config, _, settings = await runtime_config(session, ws.id, require_consent=not one_run_consent)
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc
        if run.model_config_fingerprint and run.model_config_fingerprint != model_fingerprint(config):
            raise HTTPException(409, '模型配置已变化，请取消旧任务并完整重新分析，避免混用不同模型的阶段结果')
        if run.status == 'PAUSED_BUDGET':
            limit = settings['budget'].get('max_model_calls')
            if limit is not None and limit <= (run.usage or {}).get('model_calls',0):
                raise HTTPException(409, '预算仍不足；请在自动分析设置中明确提高预算后恢复')
            run.budget = dict(settings['budget'])
            await session.commit()
    try:
        run = await project_jobs.control_run(session, run_id, ws.id, action)
    except (LookupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return await run_view(session, run)


@router.post('/projects/{project_id}/ai-scan', status_code=202)
@router.post('/projects/{project_id}/reanalyze', status_code=202)
async def reanalyze(project_id: int, req: ReanalyzeIn, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    project = await owned_project(session, ws.id, project_id)
    if project_kind(project) == 'manual':
        raise HTTPException(409, '手工项目没有可分析的仓库，请继续维护个人经历')
    try:
        config, _, settings = await runtime_config(session, ws.id, require_consent=not req.acknowledge_source_transfer)
        await session.execute(text('SELECT pg_advisory_xact_lock(20261117, 0)'))
        pipeline, prompt = versions()
        run = await project_jobs.enqueue(session, project, req.request_key, mode='full', budget=settings['budget'], reuse_open=True,
            model_config_fingerprint=model_fingerprint(config), pipeline_version=pipeline, prompt_bundle_version=prompt)
        if req.acknowledge_source_transfer and run.status in project_jobs.OPEN:
            await session.flush()
            await session.refresh(run, with_for_update=True)
            run.result_meta = {**(run.result_meta or {}), 'source_transfer_consent': CONSENT_VERSION}
            await session.flush()
            if run.status == 'BLOCKED' and await session.scalar(select(AnalysisStep.id).where(
                AnalysisStep.run_id == run.id, AnalysisStep.status == 'BLOCKED',
                AnalysisStep.error_category == 'CONSENT_REQUIRED').limit(1)):
                await project_jobs.control_run(session, run.id, ws.id, 'resume')
        await session.commit()
    except (PermissionError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return await run_view(session, run)


@router.post('/projects/{project_id}/check-updates', status_code=202)
async def check_updates(project_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    project = await owned_project(session, ws.id, project_id)
    if project_kind(project) == 'manual':
        raise HTTPException(409, '手工项目没有远端仓库')
    settings = await settings_for(session, ws.id)
    if not settings['enabled'] or settings.get('consent_version') != CONSENT_VERSION:
        raise HTTPException(409, '请先确认源码传输范围并启用自动分析')
    project.next_check_at = utcnow()
    await session.commit()
    return {'project_id':project.id, 'status':'CHECK_QUEUED', 'next_check_at':project.next_check_at.isoformat()}


def normalized_report(report):
    result = dict(report)
    result['sections'] = [{**s, 'key':s.get('key') or s.get('id'),
        'body':s.get('body') or '\n\n'.join(p.get('text','') for p in s.get('paragraphs',[])),
        'claim_ids':s.get('claim_ids') or list(dict.fromkeys(c for p in s.get('paragraphs',[]) for c in p.get('claim_ids',[])))}
        for s in report.get('sections',[])]
    changes = report.get('changes')
    if isinstance(changes, dict):
        result['changes'] = [f"{ {'added':'新增','modified':'修改','deleted':'删除','renamed':'重命名'}.get(c.get('status'), '变化')}：{c.get('path','')}" for c in changes.get('changes',[])]
    return result


@router.get('/projects/{project_id}/report')
async def get_report(project_id: int, analysis_id: int | None = None, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    project = await owned_project(session, ws.id, project_id, allow_archived=True)
    current = await latest_run(session, project)
    row = await owned_run(session, ws.id, analysis_id) if analysis_id else await valid_report(session, project)
    if row and row.project_id != project_id:
        raise HTTPException(404, '此分析不属于该项目')
    summary = await autopilot_view(session, project)
    report = None
    if row and row.report:
        from app.api.v1.project_analysis import get_analysis
        old = await get_analysis(project_id, row.id, ws, session)
        refs = [c for claim in row.report.get('claims',[]) for c in claim.get('citations',[])]
        excerpts = old.get('source_excerpts',[]) + source_excerpts(row, refs)
        unique = {(e.get('source_id') or e['path'], e.get('source_version'),
                   e['start_line'], e['end_line']): e for e in excerpts}
        fresh = freshness(project,row)
        eligible = row.id == project.latest_valid_analysis_id and row.coverage_status == 'COMPLETE' and fresh == 'CURRENT' and not project.archived_at
        report = {**report_summary(project,row), 'run_id':row.id, 'is_latest_valid':row.id == project.latest_valid_analysis_id,
            'report':normalized_report(row.report), 'source_excerpts':list(unique.values()),
            'active_issue_ids':old.get('active_issue_ids',[]) if eligible else [], 'issue_memories':old.get('issue_memories',[]),
            'generations':old.get('generations',[]), 'coverage':(row.snapshot or {}).get('coverage',{})}
    partial = None
    if current and current.coverage_status != 'COMPLETE':
        modules = (await session.scalars(select(AnalysisStep).where(AnalysisStep.run_id == current.id,
            AnalysisStep.workspace_id == ws.id, AnalysisStep.stage.in_(('module','reuse_module')), AnalysisStep.status == 'SUCCEEDED'))).all()
        if modules:
            partial = {'summary':'以下为已保存的模块观察，尚未完成完整报告与候选核对。',
                'modules':[{'name':s.module_key, 'summary':'\n'.join(c.get('text','') for c in (s.output_json or {}).get('claims',[]))} for s in modules]}
    identity = {'id':project.id, 'name':project.name, 'owner':project.owner, 'repo':project.repo,
        'kind':project_kind(project), 'archived_at':project.archived_at.isoformat() if project.archived_at else None}
    return {'project_id':project.id, 'project':identity, 'latest_run':await run_view(session,current) if current else None,
        'report':report, 'partial_preview':partial, 'worker':summary['worker'], 'allowed_actions':summary['allowed_actions']}


async def candidate_list(session, workspace_id, project_id=None):
    query = select(MemoryItem).where(MemoryItem.workspace_id == workspace_id,
        MemoryItem.mtype.in_(PROJECT_TYPES), MemoryItem.status == 'PENDING_REVIEW')
    if project_id is not None:
        query = query.where(MemoryItem.meta['project_id'].as_integer() == project_id)
    rows = (await session.scalars(query.order_by(MemoryItem.id.desc()))).all()
    return {'items':[project_candidates.view(row) for row in rows]}


@router.get('/projects/{project_id}/memory-candidates')
async def project_memory_candidates(project_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    await owned_project(session, ws.id, project_id)
    return await candidate_list(session,ws.id,project_id)


@router.get('/memory-candidates')
async def memory_candidates(project_id: int | None = None, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    if project_id is not None:
        await owned_project(session,ws.id,project_id)
    return await candidate_list(session,ws.id,project_id)


@router.post('/memory-candidates/review')
async def review_candidates(req: ReviewIn, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    if len({x.candidate_id for x in req.items}) != len(req.items):
        raise HTTPException(422, '同一批次不能重复提交同一候选')
    return {'results':await project_candidates.review_candidates(session,ws.id,[x.model_dump(exclude_none=True) for x in req.items])}
