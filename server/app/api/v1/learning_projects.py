"""Explicit project task and immutable submission preview, then durable feedback."""
import asyncio
from copy import deepcopy
import re
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.api.deps import get_workspace
from app.db import get_session
from app.models import Workspace, Project, ProjectAnalysis
from app.models.learning_feedback import LearningGoal, LearningActivity
from app.models.learning_project import LearningProjectSnapshot
from app.services.project_practice import (report_options, build_contract, assessment_for_contract,
                                           normalize_submission, MAX_FILES)
from app.services.project_sources import open_repository
from app.services.repository_credentials import repository_credentials
from app.services.assessment_versions import digest

router = APIRouter()


async def _goal(session, ws, goal_id, *, lock=False):
    query = select(LearningGoal).where(LearningGoal.id == goal_id, LearningGoal.workspace_id == ws.id)
    if lock:
        await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
        query = query.with_for_update().execution_options(populate_existing=True)
    goal = await session.scalar(query)
    if goal is None:
        raise HTTPException(404, '学习目标不存在')
    if goal.status in ('NEEDS_REVIEW', 'WITHDRAWN', 'PAUSED'):
        raise HTTPException(409, '学习目标已暂停或来源需要复核')
    return goal


async def _activity(session, ws, activity_id, *, lock=False):
    activity = await session.scalar(select(LearningActivity).where(
        LearningActivity.id == activity_id, LearningActivity.workspace_id == ws.id, LearningActivity.kind == 'PROJECT'))
    if activity is None:
        raise HTTPException(404, '项目活动不存在')
    await _goal(session, ws, activity.goal_id, lock=lock)
    if lock:
        activity = await session.scalar(select(LearningActivity).where(LearningActivity.id == activity.id)
            .with_for_update().execution_options(populate_existing=True))
    return activity


@router.get('/learning/goals/{goal_id}/project-options')
async def project_options(goal_id: int, ws=Depends(get_workspace), session=Depends(get_session)):
    await _goal(session, ws, goal_id)
    rows = (await session.execute(select(Project, ProjectAnalysis).join(ProjectAnalysis,
        Project.latest_valid_analysis_id == ProjectAnalysis.id).where(Project.workspace_id == ws.id,
        ProjectAnalysis.workspace_id == ws.id, Project.archived_at.is_(None),
        ProjectAnalysis.report_status == 'VALID', ProjectAnalysis.coverage_status == 'COMPLETE'))).all()
    projects = [{'id': p.id, 'name': p.name, 'analysis_id': run.id, 'baseline_revision': run.source_revision,
        'options': report_options(run)} for p, run in rows]
    projects = [p for p in projects if p['options']]
    return {'projects': projects, 'blocked_reason': None if projects else '还没有带可用源码的完整项目报告；可先做概念练习，等待项目分析完成。'}


class ProjectActivityIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: int
    analysis_id: int
    option_id: str = Field(min_length=1, max_length=200)
    additional_paths: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(default='', max_length=2000)


@router.post('/learning/goals/{goal_id}/project-activities')
async def create_project_activity(goal_id: int, body: ProjectActivityIn, ws=Depends(get_workspace), session=Depends(get_session)):
    goal = await _goal(session, ws, goal_id, lock=True)
    project = await session.scalar(select(Project).where(Project.id == body.project_id,
        Project.workspace_id == ws.id, Project.archived_at.is_(None)))
    run = await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.id == body.analysis_id,
        ProjectAnalysis.project_id == body.project_id, ProjectAnalysis.workspace_id == ws.id))
    if not project or not run:
        raise HTTPException(404, '项目或报告不存在')
    try:
        contract = build_contract(project, run, goal, body.option_id, body.additional_paths, body.reason)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    from app.services.learning_feedback import create_activity
    activity = await create_activity(session, ws, goal, 'PROJECT', contract['objective'],
        {'project_contract': contract, 'assessment_question': assessment_for_contract(contract, goal.skill_ids),
         'prompt': '提交所选源码变更、验证材料和一段说明。先预览范围，再开始只读检查。',
         'criteria': contract['acceptance'], 'objective': contract['objective']}, position=100)
    goal.meta = {**(goal.meta or {}), 'related_project_refs': [
        *[r for r in (goal.meta or {}).get('related_project_refs', []) if r.get('activity_id') != activity.id],
        {'activity_id': activity.id, 'project_id': project.id, 'analysis_id': run.id, 'baseline_revision': run.source_revision}]}
    await session.commit()
    return {'activity_id': activity.id, 'version': activity.version, 'project_contract': contract}


class SourceFileIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=512)
    content: str | None = Field(default=None, max_length=60000)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_revision: str = Field(min_length=40, max_length=64)
    source_kind: str
    files: list[SourceFileIn] = Field(default_factory=list, max_length=MAX_FILES)
    patch: str = Field(default='', max_length=60000)
    commit_sha: str | None = Field(default=None, max_length=64)
    explanation: str = Field(default='', max_length=8000)
    test_logs: str = Field(default='', max_length=8000)


def snapshot_view(row):
    value = row.snapshot
    return {'snapshot_id': row.id, 'activity_version': row.activity_version,
        **{k: value.get(k) for k in ('content_hash', 'source_kind', 'baseline_revision', 'submission_revision',
            'remote_verified', 'excluded_paths', 'test_evidence', 'limits')},
        'files': [{k: f.get(k) for k in ('path', 'diff', 'before_hash', 'after_hash')} for f in value['files']]}


@router.post('/learning/activities/{activity_id}/project-preview')
async def preview_project(activity_id: int, body: PreviewIn, ws=Depends(get_workspace), session=Depends(get_session)):
    activity = await _activity(session, ws, activity_id)
    contract = deepcopy(activity.snapshot['project_contract'])
    version = activity.version
    project = await session.scalar(select(Project).where(Project.id == contract['project_id'],
        Project.workspace_id == ws.id, Project.archived_at.is_(None)))
    if not project:
        raise HTTPException(409, '项目已撤回，不能继续读取或评审')
    project_info = {'kind': (project.meta or {}).get('kind', 'github'), 'owner': project.owner,
                    'repo': project.repo, 'branch': body.commit_sha}
    data = body.model_dump()
    remote = body.source_kind == 'github_commit'
    if remote:
        if project_info['kind'] != 'github' or not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', body.commit_sha or ''):
            raise HTTPException(422, '请提交已授权 GitHub 仓库的完整 commit SHA；本入口不读取本地未推送修改')
        credentials = await repository_credentials(session, ws.id)
    # No network request runs while a database transaction holds a row lock.
    ws = SimpleNamespace(id=ws.id)
    await session.commit()
    try:
        if remote:
            async with asyncio.timeout(45):
                repo = await open_repository(project_info, token=credentials['github_token'])
                if repo.revision != body.commit_sha.lower() or repo.tree_truncated:
                    raise ValueError('远端版本或文件清单无法完整核对')
                present = {x['path'] for x in repo.manifest}
                paths = contract['allowed_paths']
                async def read(path):
                    if path not in present:
                        return {'path': path, 'content': None}
                    text = await repo.read_file(path)
                    if text is None:
                        raise ValueError('选定文件读取失败；没有用空内容冒充删除')
                    return {'path': path, 'content': text}
                data['files'] = await asyncio.gather(*(read(p) for p in paths))
        value = normalize_submission(contract, data, remote_verified=remote)
    except TimeoutError:
        raise HTTPException(504, '远端源码预览超时，尚未开始模型评审；可以重试或显式上传选定文件') from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    activity = await _activity(session, ws, activity_id, lock=True)
    if activity.version != version or digest(activity.snapshot['project_contract']) != digest(contract):
        raise HTTPException(409, '活动版本已经改变，请重新预览')
    row = await session.scalar(select(LearningProjectSnapshot).where(LearningProjectSnapshot.workspace_id == ws.id,
        LearningProjectSnapshot.activity_id == activity.id, LearningProjectSnapshot.content_hash == value['content_hash']))
    if row is None:
        row = LearningProjectSnapshot(workspace_id=ws.id, goal_id=activity.goal_id, activity_id=activity.id,
            project_id=contract['project_id'], activity_version=version, content_hash=value['content_hash'], snapshot=value)
        session.add(row); await session.flush()
    await session.commit()
    return snapshot_view(row)


class ProjectSubmitIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    snapshot_id: int
    request_key: str = Field(min_length=1, max_length=120)
    expected_activity_revision: int
    parent_submission_id: int | None = None
    assistance: dict = Field(default_factory=dict)


@router.post('/learning/activities/{activity_id}/project-submit', status_code=202)
async def submit_project(activity_id: int, body: ProjectSubmitIn, ws=Depends(get_workspace), session=Depends(get_session)):
    activity = await _activity(session, ws, activity_id, lock=True)
    row = await session.scalar(select(LearningProjectSnapshot).where(LearningProjectSnapshot.id == body.snapshot_id,
        LearningProjectSnapshot.workspace_id == ws.id, LearningProjectSnapshot.activity_id == activity.id))
    if row is None:
        raise HTTPException(404, '没有找到当前活动的提交预览')
    if row.activity_version != activity.version or body.expected_activity_revision != activity.version:
        raise HTTPException(409, '预览与活动版本不一致，请重新查看任务')
    from app.services.learning_feedback import submit_activity
    result = await submit_activity(session, ws, activity,
        {'answer_text': row.snapshot['answer_text'], 'project_snapshot_id': row.id,
         'project_snapshot_hash': row.content_hash, 'project_contract': activity.snapshot['project_contract'],
         'test_evidence': row.snapshot['test_evidence'], 'assistance': body.assistance},
        body.request_key, body.expected_activity_revision, body.parent_submission_id)
    await session.commit()
    if isinstance(result, dict):
        return result
    return {'submission_id': result.id, 'run_id': result.scoring_run_id, 'status': result.status,
            'exam_id': result.exam_id, 'revision': result.revision}
