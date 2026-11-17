"""Ambiguity reports are scoped, durable review requests, never grade changes."""
from copy import deepcopy
import importlib
import importlib.util
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, func

from app.api.v1 import exams
from app.models import Event, Exam, ExamQuestion, Workspace
from test_assessment_queue import make_exam


def feedback_api():
    assert importlib.util.find_spec('app.api.v1.assessment_feedback'), 'question ambiguity review API is required'
    return importlib.import_module('app.api.v1.assessment_feedback')


@pytest.mark.asyncio
async def test_feedback_preserves_frozen_material_without_changing_grade_or_question(db_session, test_workspace):
    api = feedback_api()
    detail, questions = await make_exam(db_session, test_workspace)
    await exams.submit_exam(detail['id'], test_workspace, db_session)
    eq = await db_session.scalar(select(ExamQuestion).where(ExamQuestion.exam_id == detail['id']))
    snapshot = deepcopy(eq.frozen_snapshot)
    questions[0].title = 'Later edited live title'
    questions[0].rubric = {'max_points': 99, 'points': []}
    await db_session.commit()
    result = await api.create_feedback(detail['id'], eq.id,
        api.FeedbackIn(category='QUESTION_AMBIGUITY', reason='The failure boundary is not defined in this question.', request_key='feedback-fixture'),
        ws=test_workspace, session=db_session)
    assert result['status'] == 'PENDING_REVIEW'
    event = await db_session.get(Event, result['id'])
    assert event.workspace_id == test_workspace.id
    assert event.payload['exam_id'] == detail['id'] and event.payload['exam_question_id'] == eq.id
    assert event.payload['frozen_question']['title'] == snapshot['title']
    assert event.payload['frozen_question']['rubric'] == snapshot['rubric']
    assert event.payload['question_version'] == snapshot['question_version']
    assert event.payload['rubric_version'] == snapshot['rubric']['rubric_version']
    current = await db_session.get(Exam, detail['id'])
    assert current.status == 'SCORING' and current.score is None
    assert questions[0].title == 'Later edited live title' and questions[0].rubric['max_points'] == 99
    queued = await api.list_feedback(ws=test_workspace, session=db_session)
    assert [item['id'] for item in queued['items']] == [event.id]
    from app.main import app
    assert '/api/v1/assessment-feedback' in app.openapi()['paths']


@pytest.mark.asyncio
async def test_feedback_cannot_read_or_write_another_workspaces_assessment(db_session, test_workspace):
    api = feedback_api()
    detail, _ = await make_exam(db_session, test_workspace)
    await exams.submit_exam(detail['id'], test_workspace, db_session)
    eq = await db_session.scalar(select(ExamQuestion).where(ExamQuestion.exam_id == detail['id']))
    foreign = Workspace(slug='foreign-feedback-' + uuid4().hex, name='Foreign fixture')
    db_session.add(foreign)
    await db_session.commit()
    with pytest.raises(HTTPException) as error:
        await api.create_feedback(detail['id'], eq.id, api.FeedbackIn(category='QUESTION_AMBIGUITY', reason='Unclear wording', request_key='foreign'), ws=foreign, session=db_session)
    assert error.value.status_code == 404
    own = await api.create_feedback(detail['id'], eq.id, api.FeedbackIn(category='QUESTION_AMBIGUITY', reason='Unclear wording', request_key='own'), ws=test_workspace, session=db_session)
    result = await api.list_feedback(exam_id=detail['id'], ws=foreign, session=db_session)
    assert result['items'] == [] and own['id'] > 0
    await db_session.delete(foreign)
    await db_session.commit()


@pytest.mark.asyncio
async def test_feedback_retries_are_idempotent_and_frozen_exam_is_required(db_session, test_workspace):
    api = feedback_api()
    detail, _ = await make_exam(db_session, test_workspace)
    eq = await db_session.scalar(select(ExamQuestion).where(ExamQuestion.exam_id == detail['id']))
    req = api.FeedbackIn(category='RUBRIC_AMBIGUITY', reason='The two criteria appear to overlap.', request_key='same-request')
    with pytest.raises(HTTPException) as error:
        await api.create_feedback(detail['id'], eq.id, req, ws=test_workspace, session=db_session)
    assert error.value.status_code == 409
    await exams.submit_exam(detail['id'], test_workspace, db_session)
    first = await api.create_feedback(detail['id'], eq.id, req, ws=test_workspace, session=db_session)
    second = await api.create_feedback(detail['id'], eq.id, req, ws=test_workspace, session=db_session)
    assert first['id'] == second['id']
    assert await db_session.scalar(select(func.count(Event.id)).where(Event.workspace_id == test_workspace.id, Event.kind == api.EVENT_KIND)) == 1
    with pytest.raises(HTTPException) as conflict:
        await api.create_feedback(detail['id'], eq.id, req.model_copy(update={'reason':'A different reason for the same request.'}), ws=test_workspace, session=db_session)
    assert conflict.value.status_code == 409
