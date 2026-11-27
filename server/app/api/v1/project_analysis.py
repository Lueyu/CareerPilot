"""Persistent explicit AI analysis; no execution of repository code."""
from datetime import datetime, timezone
from functools import partial
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import SessionLocal, get_session
from app.i18n import message as localized_message
from app.models import Exam, ExamQuestion, ImportItem, Project, ProjectAnalysis, QuestionGeneration, Workspace
from app.services.private_model_billing import BillingScope
from app.services.project_context import project_kind, project_profile
from app.services.project_memory import (
    active_project_memories,
    lock_project_memories,
    publish_project_memories,
    report_binding,
    source_excerpts,
)
from app.services.project_sources import open_repository
from app.services.repository_credentials import repository_credentials

router = APIRouter(tags=['project-analysis'])


class AnalysisIn(BaseModel):
    focus: str = Field(default='', max_length=3000)
    request_key: str | None = Field(default=None, min_length=1, max_length=120)


async def owned_project(session, ws_id, project_id):
    row = await session.scalar(select(Project).where(Project.id == project_id, Project.workspace_id == ws_id))
    if row is None:
        raise HTTPException(404, localized_message("errors.project_not_found"))
    return row


async def owned_run(session, ws_id, project_id, analysis_id, lock=False):
    query = select(ProjectAnalysis).where(ProjectAnalysis.id == analysis_id, ProjectAnalysis.project_id == project_id,
                                          ProjectAnalysis.workspace_id == ws_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query.execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, localized_message("errors.project_analysis_not_found"))
    return row


def run_view(row):
    return {**{key: getattr(row, key) for key in ('id', 'project_id', 'focus', 'status', 'source_revision', 'report', 'error', 'result_meta')},
            'report_status': row.report_status, 'coverage_status': row.coverage_status,
            'coverage': (row.snapshot or {}).get('coverage', {}),
            'created_at': row.created_at.isoformat() if row.created_at else None,
            'finished_at': row.finished_at.isoformat() if row.finished_at else None}


@router.get('/projects/{project_id}/analyses')
async def list_analyses(project_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    await owned_project(session, ws.id, project_id)
    rows = (await session.scalars(select(ProjectAnalysis).where(ProjectAnalysis.project_id == project_id,
            ProjectAnalysis.workspace_id == ws.id).order_by(ProjectAnalysis.id.desc()).limit(30))).all()
    return {'items': [run_view(row) for row in rows]}


@router.post('/projects/{project_id}/analyses')
async def create_analysis(project_id: int, req: AnalysisIn, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    project = await owned_project(session, ws.id, project_id)
    if project_kind(project) not in ('github', 'gitcode') or project.archived_at:
        raise HTTPException(409, localized_message("errors.select_a_repository_project_that_has_not_been_removed"))
    from app.services.project_autopilot_config import model_fingerprint, runtime_config
    from app.services.project_jobs import enqueue
    from app.services.project_scheduler import versions
    config,_,settings=await runtime_config(session,ws.id,require_consent=False)
    pipeline,prompt=versions()
    row=await enqueue(session,project,req.request_key or uuid4().hex,budget=settings['budget'],
        model_config_fingerprint=model_fingerprint(config),pipeline_version=pipeline,prompt_bundle_version=prompt)
    await session.commit()
    return run_view(row)


@router.get('/projects/{project_id}/analyses/{analysis_id}')
async def get_analysis(project_id: int, analysis_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    row = await owned_run(session, ws.id, project_id, analysis_id)
    view = run_view(row)
    latest_success = await session.scalar(select(ProjectAnalysis.id).where(ProjectAnalysis.project_id == project_id,
        ProjectAnalysis.workspace_id == ws.id, ProjectAnalysis.status == 'SUCCESS').order_by(ProjectAnalysis.id.desc()).limit(1))
    project = await owned_project(session,ws.id,project_id)
    latest_success = project.latest_valid_analysis_id or latest_success
    view['is_latest_success'] = (row.report_status=='VALID' or row.status=='SUCCESS') and row.id==latest_success and project.archived_at is None
    citations = [c for section in (row.report or {}).get('architecture', []) + (row.report or {}).get('issues', []) for c in section.get('citations', [])]
    view['source_excerpts'] = source_excerpts(row, citations)
    memories = await active_project_memories(session, ws.id, project_id)
    view['memory_ids'] = [m.id for m in memories if report_binding(m,analysis_id)]
    view['active_issue_ids'] = [report_binding(m,analysis_id)['issue_id'] for m in memories if report_binding(m,analysis_id) and m.mtype == 'ProjectIssue']
    view['issue_memories'] = [{'issue_id': report_binding(m,analysis_id)['issue_id'], 'memory_id': m.id, 'content': m.content,
                              'user_corrected': m.source == 'USER', 'source_revision': m.meta.get('source_revision')}
                             for m in memories if report_binding(m,analysis_id) and m.mtype == 'ProjectIssue']
    generations = (await session.scalars(select(QuestionGeneration).where(
        QuestionGeneration.workspace_id == ws.id,
        QuestionGeneration.input_snapshot['source_analysis_id'].as_integer() == analysis_id,
    ).order_by(QuestionGeneration.id.desc()).limit(20))).all()
    view['generations'] = []
    for task in generations:
        drafts = (await session.scalars(select(ImportItem).where(ImportItem.session_id == task.import_session_id,
                   ImportItem.status.in_(('APPROVED', 'MERGED'))))).all() if task.import_session_id else []
        ids = sorted({d.question_id or d.merged_into for d in drafts if d.question_id or d.merged_into})
        exams = (await session.scalars(select(Exam).join(ExamQuestion, ExamQuestion.exam_id == Exam.id).where(
            Exam.workspace_id == ws.id, ExamQuestion.question_id.in_(ids)).distinct().order_by(Exam.id.desc()))).unique().all() if ids else []
        exams = [e for e in exams if any(s.get('analysis_id') == analysis_id and s.get('generation_id') == task.id
                 for s in (e.project_assessment or {}).get('sources', []))]
        view['generations'].append({'id': task.id, 'status': task.status,
            'issue_ids': task.input_snapshot.get('source_issue_ids', []), 'question_ids': ids,
            'assessments': [{'id': e.id, 'status': e.status, 'score': e.score} for e in exams]})
    return view


async def publish_success(session, run, result):
    if run.status != 'RUNNING':
        return  # restart recovery or another terminal transition won the row lock
    run.source_revision, run.snapshot = result['source_revision'], result['snapshot']
    run.report, run.result_meta = result['report'], result['result_meta']
    run.status, run.error, run.finished_at = 'SUCCESS', None, datetime.now(timezone.utc)
    await publish_project_memories(session, run)


async def execute_analysis(analysis_id, workspace_id, project_id):
    try:
        from app.services.project_deep_analysis import analyze_project
        async def progress(stage):
            async with SessionLocal() as progress_session:
                current = await owned_run(progress_session, workspace_id, project_id, analysis_id, lock=True)
                if current.status != 'RUNNING':
                    raise RuntimeError('analysis interrupted')
                current.result_meta = {'stage': stage}
                await progress_session.commit()
        async with SessionLocal() as session:
            run = await owned_run(session, workspace_id, project_id, analysis_id)
            if run.status != 'RUNNING':
                return
            project = await owned_project(session, workspace_id, project_id)
            info = {key: getattr(project, key) for key in ('id', 'name', 'owner', 'repo', 'branch')}
            info['kind'] = project_kind(project)
            profile = project_profile(project)
            profile['project_notes'] = [{'content': m.content, 'source_revision': (m.meta or {}).get('source_revision'),
                'note': '用户对旧版本项目认知的纠正；需与本次代码核对，不能自动视为新版本事实。'}
                for m in await active_project_memories(session, workspace_id, project_id) if m.source == 'USER']
            focus = run.focus
            credentials = await repository_credentials(session, workspace_id)
        open_source = partial(open_repository, token=credentials.get(f"{info['kind']}_token", ''))
        result = await analyze_project(info, focus, profile, progress=progress, open_source=open_source, billing_scope=BillingScope(workspace_id, f"project_analysis:{analysis_id}"))
        async with SessionLocal() as session:
            await lock_project_memories(session, workspace_id)
            run = await owned_run(session, workspace_id, project_id, analysis_id, lock=True)
            await publish_success(session, run, result)
            await session.commit()
    except Exception as exc:
        # Never persist provider request headers, credentials or a source-file exception dump.
        async with SessionLocal() as session:
            run = await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.id == analysis_id,
                ProjectAnalysis.workspace_id == workspace_id, ProjectAnalysis.project_id == project_id).with_for_update())
            if run is None:
                return  # The user deleted this project while source/model work was in flight.
            if run.status == 'RUNNING':
                run.status, run.finished_at = 'FAILED', datetime.now(timezone.utc)
                reason = str(exc)[:500] if getattr(exc, 'safe_for_user', False) else '请检查仓库权限、分支和模型配置后新建分析'
                run.error = f'本次 AI 分析失败：{reason}。未更新项目记忆；历史结果保持原版本。'
                await session.commit()


@router.post('/projects/{project_id}/analyses/{analysis_id}/run')
async def run_analysis(project_id: int, analysis_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    run = await owned_run(session, ws.id, project_id, analysis_id)
    if run.pipeline_version == 'legacy':
        raise HTTPException(409, localized_message("errors.start_a_full_analysis_to_replace_this_historical_run"))
    if run.status in ('PAUSED_USER','PAUSED_BUDGET','BLOCKED'):
        from app.services.project_jobs import control_run
        run = await control_run(session, run.id, ws.id, 'resume')
    return run_view(run)


async def recover_interrupted():
    async with SessionLocal() as session:
        await session.execute(update(ProjectAnalysis).where(ProjectAnalysis.status == 'RUNNING', ProjectAnalysis.pipeline_version == 'legacy').values(
            status='FAILED', finished_at=datetime.now(timezone.utc), error='应用重启中断本次分析，请新建分析；项目记忆未被替换。'))
        await session.commit()
