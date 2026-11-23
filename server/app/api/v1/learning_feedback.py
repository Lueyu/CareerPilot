"""Existing learning-task page, backed by durable per-submission assessment runs."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import Exam, Workspace
from app.models.learning_feedback import LearningActivity, LearningGoal, LearningSubmission
from app.services import assessment_jobs
from app.services import learning_feedback as service

router = APIRouter(tags=['learning-feedback'])


class AssistanceIn(BaseModel):
    kind: str
    request_key: str = Field(min_length=1, max_length=120)
    expected_activity_revision: int = Field(ge=1)


class SubmissionIn(BaseModel):
    payload: dict
    request_key: str = Field(min_length=1, max_length=120)
    expected_activity_revision: int = Field(ge=1)
    parent_submission_id: int | None = None


def ack(submission):
    return {'submission_id':submission.id, 'exam_id':submission.exam_id,
        'scoring_run_id':submission.scoring_run_id, 'status':submission.status,
        'feedback_url':f'/api/v1/learning/submissions/{submission.id}'}


@router.get('/learning/tasks/{task_id}/path')
async def path(task_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    return await service.path_view(session, ws, task_id)


@router.post('/learning/tasks/{task_id}/prepare')
async def prepare(task_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    return await service.prepare_path(session, ws, task_id)


@router.post('/learning/activities/{activity_id}/assistance')
async def assistance(activity_id: int, body: AssistanceIn, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    return await service.record_assistance(session, ws, activity_id, body.kind, body.request_key, body.expected_activity_revision)


@router.post('/learning/activities/{activity_id}/submissions', status_code=202)
async def submit(activity_id: int, body: SubmissionIn, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    activity = await session.scalar(select(LearningActivity).where(LearningActivity.id == activity_id,
        LearningActivity.workspace_id == ws.id))
    if activity is None: raise HTTPException(404, '学习活动不存在')
    if activity.kind == 'PROJECT': raise HTTPException(409, '项目提交请使用范围预览与快照入口')
    submission = await service.submit_activity(session, ws, activity, body.payload, body.request_key,
        body.expected_activity_revision, body.parent_submission_id)
    await session.commit()
    return ack(submission)


@router.get('/learning/submissions/{submission_id}')
async def submission_detail(submission_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    return await service.submission_view(session, ws, submission_id, mark_seen=True)


@router.post('/learning/submissions/{submission_id}/retry', status_code=202)
async def retry(submission_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    submission = await session.scalar(select(LearningSubmission).where(LearningSubmission.id == submission_id,
        LearningSubmission.workspace_id == ws.id))
    if submission is None: raise HTTPException(404, '练习提交不存在')
    await service._lock_workspace(session, ws)
    goal = await session.get(LearningGoal, submission.goal_id)
    await service.assert_goal_sources(session, ws, goal)
    exam = await session.scalar(select(Exam).where(Exam.id == submission.exam_id, Exam.workspace_id == ws.id))
    if exam is None: raise HTTPException(409, '原答卷已撤回，无法重试')
    try:
        run = await assessment_jobs.resume(session, ws, exam)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    submission.status = run.status
    await session.commit()
    return ack(submission)
