"""Append-only ambiguity feedback; review never changes an assessment implicitly."""
from copy import deepcopy
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import Event, Exam, ExamQuestion, Workspace
from app.services.assessment_versions import digest
from app.services.tracing import emit_event

router = APIRouter(tags=['assessment-feedback'])
EVENT_KIND = 'assessment.question_feedback'
FROZEN_STATES = {'FROZEN', 'SUBMITTED', 'TRANSCRIPT_CONFIRMED', 'SCORING',
                 'SCORING_RETRY_WAIT', 'SCORING_FAILED', 'NEEDS_REVIEW', 'COMPLETED'}


class FeedbackIn(BaseModel):
    category: Literal['QUESTION_AMBIGUITY', 'RUBRIC_AMBIGUITY', 'REFERENCE_ISSUE', 'OTHER']
    reason: str = Field(min_length=5, max_length=2000)
    request_key: str = Field(min_length=1, max_length=96)

    @field_validator('reason', 'request_key', mode='before')
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


def feedback_view(event):
    payload = event.payload or {}
    return {'id': event.id, 'status': 'PENDING_REVIEW',
            'created_at': event.created_at.isoformat() if event.created_at else None,
            **{key: payload.get(key) for key in (
                'exam_id', 'exam_question_id', 'order_no', 'question_id', 'question_version',
                'rubric_version', 'category', 'reason', 'frozen_question')}}


@router.post('/exams/{exam_id}/questions/{exam_question_id}/feedback', status_code=201)
async def create_feedback(exam_id: int, exam_question_id: int, req: FeedbackIn,
                          ws: Workspace = Depends(get_workspace),
                          session: AsyncSession = Depends(get_session)):
    # The workspace lock serializes retry keys without mutating exam or question rows.
    await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
    row = (await session.execute(select(ExamQuestion, Exam).join(Exam, Exam.id == ExamQuestion.exam_id)
        .where(Exam.workspace_id == ws.id, Exam.id == exam_id, ExamQuestion.id == exam_question_id))).first()
    if row is None:
        raise HTTPException(404, '本场考试中没有这道题')
    question, exam = row
    if exam.status not in FROZEN_STATES:
        raise HTTPException(409, '请在答卷提交后从本场报告反馈题目问题')
    snapshot = question.frozen_snapshot or question.creation_snapshot
    if not snapshot:
        raise HTTPException(409, '本题缺少冻结版本，暂不能建立可追溯的题目反馈')
    existing = await session.scalar(select(Event).where(Event.workspace_id == ws.id,
        Event.kind == EVENT_KIND, Event.payload['exam_question_id'].as_integer() == question.id,
        Event.payload['request_key'].as_string() == req.request_key))
    if existing:
        if existing.payload.get('reason') != req.reason or existing.payload.get('category') != req.category:
            raise HTTPException(409, '该反馈请求已保存其他内容，请重新提交新的反馈')
        await session.commit()
        return feedback_view(existing)
    frozen = deepcopy({key: snapshot.get(key) for key in (
        'question_id', 'canonical_question_id', 'short_code', 'title', 'qtype', 'options',
        'skill_id', 'difficulty', 'question_version', 'rubric', 'answer_html')})
    rubric = frozen.get('rubric') or {}
    payload = {'status': 'PENDING_REVIEW', 'exam_id': exam.id, 'exam_question_id': question.id,
        'order_no': question.order_no, 'question_id': question.question_id,
        'question_version': snapshot.get('question_version') or digest(frozen),
        'rubric_version': rubric.get('rubric_version') or digest(rubric),
        'frozen_question': frozen, 'category': req.category, 'reason': req.reason,
        'request_key': req.request_key}
    event = await emit_event(session, ws.id, EVENT_KIND, payload, 'exam_question', question.id)
    from app.services.learning_memory import invalidate_assessment_dependents
    await invalidate_assessment_dependents(session, ws.id, exam.id, question.id, '来源题目或评分标准反馈待复核')
    await session.commit()
    return feedback_view(event)


@router.get('/assessment-feedback')
async def list_feedback(exam_id: int | None = None, exam_question_id: int | None = None,
                        before_id: int | None = None,
                        limit: Annotated[int, Query(ge=1, le=100)] = 50,
                        ws: Workspace = Depends(get_workspace),
                        session: AsyncSession = Depends(get_session)):
    query = select(Event).where(Event.workspace_id == ws.id, Event.kind == EVENT_KIND)
    if exam_id is not None:
        query = query.where(Event.payload['exam_id'].as_integer() == exam_id)
    if exam_question_id is not None:
        query = query.where(Event.payload['exam_question_id'].as_integer() == exam_question_id)
    if before_id is not None:
        query = query.where(Event.id < before_id)
    rows = (await session.scalars(query.order_by(Event.id.desc()).limit(limit + 1))).all()
    return {'items': [feedback_view(row) for row in rows[:limit]],
            'next_before_id': rows[limit - 1].id if len(rows) > limit else None}
