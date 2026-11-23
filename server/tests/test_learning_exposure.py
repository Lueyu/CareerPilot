"""Reference exposure must be recorded before an answer can become a retest."""
import pytest
from sqlalchemy import select
from test_assessment_snapshot_review import fixture_question

from app.api.v1 import bank, exams
from app.models import LearningTask, Question


@pytest.mark.asyncio
async def test_bank_list_is_not_exposure_but_reference_detail_excludes_family(db_session, test_workspace):
    from app.services.learning_exposure import exposed_families
    first, skill = await fixture_question(db_session, test_workspace)
    first.answer_html = '<p>Frozen reference material</p>'
    second = Question(workspace_id=test_workspace.id, short_code=first.short_code+'-new', title='Fresh new context',
        qtype='open', topic=first.topic, skill_id=skill.id, skill=skill,
        rubric={'max_points':5,'points':[{'title':'Mechanism'}]})
    variant = Question(workspace_id=test_workspace.id, short_code=first.short_code+'-variant', title='Equivalent template',
        qtype='open', topic=first.topic, skill_id=skill.id, skill=skill, canonical_question_id=first.id,
        rubric={'max_points':5,'points':[{'title':'Mechanism'}]})
    db_session.add_all([second, variant]); await db_session.commit()
    await bank.list_bank(ws=test_workspace, session=db_session)
    assert not await exposed_families(db_session, test_workspace.id)
    detail = await bank.bank_detail(first.short_code, test_workspace, db_session)
    assert detail['answer_html']
    assert first.id in await exposed_families(db_session, test_workspace.id)
    task = LearningTask(workspace_id=test_workspace.id, title='Retest', kind='RETEST', status='PENDING',
        skill_id=skill.id, source_criterion='Mechanism')
    db_session.add(task); await db_session.commit()
    created = await exams.create_exam(exams.CreateExamReq(kind='focused', question_count=1,
        retest_task_id=task.id), test_workspace, db_session)
    assert created['questions'][0]['short_code'] == second.short_code


@pytest.mark.asyncio
async def test_reference_opened_during_exam_freezes_assistance_and_cannot_be_independent(db_session, test_workspace, monkeypatch):
    from app.models import ScoringStep
    from app.services import assessment_jobs as jobs
    first, _ = await fixture_question(db_session, test_workspace)
    first.answer_html='<p>Source reference</p>'
    await db_session.commit()
    created = await exams.create_exam(exams.CreateExamReq(kind='bank_exam', question_ids=[first.id]), test_workspace, db_session)
    await bank.bank_detail(first.short_code, test_workspace, db_session)
    async def cfg(*args,**kwargs): return {},{},{}
    monkeypatch.setattr(jobs,'runtime_config',cfg)
    ack = await exams.submit_exam(created['id'], test_workspace, db_session,
        exams.SubmitExamReq(answers=[exams.SaveAnswerReq(order_no=1,text_answer='Independent-looking text',expected_revision=0)]))
    step = await db_session.scalar(select(ScoringStep).where(ScoringStep.run_id == ack['task_id']))
    assert step.input_json['independence']['eligible_for_independent_verification'] is False
    assert step.input_json['assistance']['reference_viewed'] is True
