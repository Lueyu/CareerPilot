from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.bank import bank_detail, list_bank
from app.api.v1.exams import CreateExamReq, SaveAnswerReq, SubmitExamReq, issue_exam, submit_exam
from app.models import Exam, Question, ScoringRun, Workspace


@pytest.mark.asyncio
async def test_private_beta_library_visibility_and_single_practice(db_session, test_workspace):
    owner = test_workspace
    other = Workspace(slug='test-' + uuid4().hex, name='Second fixture')
    db_session.add(other)
    await db_session.flush()

    def question(workspace, origin):
        return Question(
            workspace_id=workspace.id, short_code=uuid4().hex, title=origin,
            topic='unmapped-topic', role='Synthetic private role', qtype='open',
            generation_meta={'assessment_use': 'PRACTICE_ONLY', 'origin': origin},
        )

    private = [question(owner, origin) for origin in ('library_import', 'library_example', 'library_starter')]
    foreign = question(other, 'library_import')
    hidden = question(owner, 'interview_set')
    db_session.add_all([*private, foreign, hidden])
    await db_session.commit()
    try:
        listed = await list_bank(scope='mine', ws=owner, session=db_session, limit=60, offset=0)
        assert {item['id'] for item in listed['items']} == {q.id for q in private}
        for q in private:
            detail = await bank_detail(q.short_code, owner, db_session)
            assert detail['generation_meta'] == q.generation_meta
            assert detail['skill'] is None
        for q in (foreign, hidden):
            with pytest.raises(HTTPException) as exc:
                await bank_detail(q.short_code, owner, db_session)
            assert exc.value.status_code == 404

        def request(*questions, **params):
            return CreateExamReq(kind=Exam.KIND_BANK, question_ids=[q.id for q in questions],
                                 question_count=len(questions), **params)

        for req, code in ((request(foreign), 404), (request(hidden), 409),
                          (request(*private[:2]), 409),
                          (request(private[0], project_generation_id=1), 409)):
            with pytest.raises(HTTPException) as exc:
                await issue_exam(req, owner, db_session)
            assert exc.value.status_code == code

        created = await issue_exam(request(private[0]), owner, db_session)
        assert created['kind'] == 'practice'
        assert created['experienced_bank_practice'] is False
        assert created['questions'][0]['title'] == private[0].title
        exam = await db_session.get(Exam, created['id'])
        assert exam.questions[0].creation_snapshot['assessment_mode'] == 'PRACTICE'
        assert exam.questions[0].creation_snapshot['bank_practice'] is True
        assert exam.questions[0].creation_snapshot['rubric']['target_skill_ids'] == []
        queued = await submit_exam(exam.id, owner, db_session, SubmitExamReq(
            answers=[SaveAnswerReq(order_no=1, text_answer='Synthetic answer')],
        ))
        run = await db_session.get(ScoringRun, queued['task_id'])
        assert run.mode == 'PRACTICE'
        frozen = exam.questions[0].frozen_snapshot
        assert frozen['bank_practice'] is True
        assert frozen['independence']['eligible_for_independent_verification'] is False
        assert private[0].rubric is None
    finally:
        await db_session.delete(other)
        await db_session.commit()
