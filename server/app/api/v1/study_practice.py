"""Start one exercise on demand; the ordinary exam UI owns formal retest answers."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import message as localized_message
from app.api.deps import get_workspace
from app.api.v1.exams import CreateExamReq, issue_exam
from app.db import get_session
from app.models import Exam, Workspace
from app.models.learning_feedback import LearningActivity
from app.models.study_practice import StudyPractice
from app.services import learning_feedback as learning
from app.services import study_practice as service

router = APIRouter(prefix='/study', tags=['study-practice'])


class CreatePractice(BaseModel):
    focus_message_id: int | None = Field(default=None, gt=0)
    kind: Literal['quiz', 'explain']
    request_key: str = Field(min_length=1, max_length=120)
    card_id: int | None = None
    skill_key: str | None = Field(default=None, max_length=64)


class Answer(BaseModel):
    text_answer: str = Field(min_length=1, max_length=60000)
    request_key: str = Field(min_length=1, max_length=120)
    parent_submission_id: int | None = None


class Assistance(BaseModel):
    kind: Literal['hint', 'reference']
    request_key: str = Field(min_length=1, max_length=120)


@router.post('/sessions/{session_id}/practices')
async def create(session_id: int, body: CreatePractice, ws: Workspace = Depends(get_workspace),
                 session: AsyncSession = Depends(get_session)):
    row = await service.create_practice(session, ws, session_id, body)
    return await service.practice_view(session, ws, row, mark_seen=True)


@router.get('/sessions/{session_id}/practices')
async def history(session_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    await service.owned_session(session, ws.id, session_id)
    rows = (await session.scalars(select(StudyPractice).where(
        StudyPractice.workspace_id == ws.id, StudyPractice.session_id == session_id)
        .order_by(StudyPractice.id.desc()))).all()
    return {'items': [await service.practice_view(session, ws, row, mark_seen=True) for row in rows]}


@router.get('/practices/{practice_id}')
async def detail(practice_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    row = await service.owned_practice(session, ws.id, practice_id)
    return await service.practice_view(session, ws, row, mark_seen=True)


@router.post('/practices/{practice_id}/answers', status_code=202)
async def answer(practice_id: int, body: Answer, ws: Workspace = Depends(get_workspace),
                 session: AsyncSession = Depends(get_session)):
    row = await service.owned_practice(session, ws.id, practice_id)
    activity = await session.get(LearningActivity, row.id)
    await learning.submit_activity(session, ws, activity, {'text_answer': body.text_answer},
                                   body.request_key, activity.version, body.parent_submission_id)
    await session.commit()
    return await service.practice_view(session, ws, row, mark_seen=True)


@router.post('/practices/{practice_id}/assistance')
async def assistance(practice_id: int, body: Assistance, ws: Workspace = Depends(get_workspace),
                     session: AsyncSession = Depends(get_session)):
    row = await service.owned_practice(session, ws.id, practice_id)
    activity = await session.get(LearningActivity, row.id)
    return await learning.record_assistance(session, ws, row.id, body.kind.upper(), body.request_key, activity.version)


@router.post('/practices/{practice_id}/skip')
async def skip(practice_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    await learning._lock_workspace(session, ws)
    row = await service.owned_practice(session, ws.id, practice_id)
    activity = await session.get(LearningActivity, row.id)
    activity.status = 'SKIPPED'
    await session.commit()
    return await service.practice_view(session, ws, row, mark_seen=True)


@router.post('/practices/{practice_id}/retest')
async def retest(practice_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    await learning._lock_workspace(session, ws)
    row = await service.owned_practice(session, ws.id, practice_id)
    activity = await session.get(LearningActivity, row.id)
    if not activity.latest_submission_id:
        raise HTTPException(409, localized_message("errors.submit_your_own_answer_before_checking_transfer_to_a"))
    return await issue_exam(CreateExamReq(kind=Exam.KIND_FOCUSED, question_count=1), ws, session, study_practice=row)


@router.get('/practices/{practice_id}/retest')
async def retest_status(practice_id: int, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    row = await service.owned_practice(session, ws.id, practice_id)
    return await service.retest_result(session, ws, row)
