from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v1 import exams
from app.models import Evidence, Question, Skill


async def make_exam(session, ws, count=1):
    skill=Skill(key='aq-'+uuid4().hex[:10], name='Assessment fixture')
    session.add(skill);await session.flush()
    questions=[]
    for i in range(count):
        q=Question(short_code='AQ-'+uuid4().hex[:12],workspace_id=ws.id,title=f'Original queue question {i}',
            qtype='open',topic='rag',skill_id=skill.id,skill=skill,difficulty=3,
            rubric={'max_points':5,'points':[{'title':'Explain mechanism','detail':'Describe how retrieval uses source evidence.'}]})
        session.add(q);questions.append(q)
    await session.commit()
    detail=await exams.create_exam(exams.CreateExamReq(kind='bank_exam',question_ids=[q.id for q in questions]),ws,session)
    return detail,questions


@pytest.mark.asyncio
async def test_submit_returns_durable_pending_without_inline_scoring(db_session,test_workspace):
    detail,_=await make_exam(db_session,test_workspace)
    result=await exams.submit_exam(detail['id'],test_workspace,db_session)
    assert result['status']=='SCORING'
    assert result['task_id']>0
    assert await db_session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id==test_workspace.id))==0
    again=await exams.submit_exam(detail['id'],test_workspace,db_session)
    assert again['task_id']==result['task_id']
    report=await exams._exam_report(db_session,test_workspace,detail['id'])
    assert report['score'] is None and report['overall'] is None
    assert report['scoring_task']['total_count']==1


@pytest.mark.asyncio
async def test_question_and_rubric_are_frozen_at_creation(db_session,test_workspace):
    detail,questions=await make_exam(db_session,test_workspace)
    original=questions[0].title
    questions[0].title='Edited while student is answering'
    questions[0].rubric={'max_points':99,'points':[]}
    await db_session.commit()
    fetched=await exams._exam_detail(db_session,test_workspace,detail['id'])
    assert fetched['questions'][0]['title']==original
    assert 'rubric' not in fetched['questions'][0] and 'reference_answer' not in fetched['questions'][0]


@pytest.mark.asyncio
async def test_answer_revision_conflict_preserves_last_saved_text(db_session,test_workspace):
    detail,_=await make_exam(db_session,test_workspace)
    first=await exams.save_answer(detail['id'],exams.SaveAnswerReq(order_no=1,text_answer='First',expected_revision=0),test_workspace,db_session)
    assert first['revision']==1
    with pytest.raises(HTTPException) as error:
        await exams.save_answer(detail['id'],exams.SaveAnswerReq(order_no=1,text_answer='Stale overwrite',expected_revision=0),test_workspace,db_session)
    assert error.value.status_code==409
    await db_session.rollback()
    await db_session.refresh(test_workspace)
    fetched=await exams._exam_detail(db_session,test_workspace,detail['id'])
    assert fetched['questions'][0]['answer']['text_answer']=='First'
