from copy import deepcopy
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.services.assessment_versions import question_snapshot
from app.services.assessment_worker import rule_result
from app.services import assessment_jobs as jobs
from app.models import Question
from test_assessment_queue import make_exam


@pytest.mark.parametrize('rubric',[{'answer_index':True,'max_points':5},{'answer_index':2,'max_points':5},
 {'answer_index':0,'max_points':0},{'answer_index':0,'max_points':float('inf')},{'answer_index':0,'max_points':-1}])
def test_invalid_mcq_contract_is_rejected_before_exam_creation(rubric):
    q=Question(id=99,title='Choose one',short_code='G99',qtype='mcq',options=['A','B'],rubric=rubric)
    with pytest.raises(HTTPException) as error:question_snapshot(q)
    assert error.value.status_code==409


def test_mcq_result_uses_same_frozen_rubric_version_and_valid_choice():
    q=Question(id=99,title='Choose one',short_code='G99',qtype='mcq',options=['A','B'],rubric={'answer_index':0,'max_points':5})
    snapshot=question_snapshot(q)
    result=rule_result({**snapshot,'answer_revision_id':7,'submitted_answer':{'choice_index':0}})
    assert result['rubric_version']==snapshot['rubric']['rubric_version']
    assert result['points']==5 and result['validation_status']=='VALID'


def test_frozen_question_version_includes_job_standard_targets():
    q=Question(id=99,title='Choose one',short_code='G99',qtype='mcq',options=['A','B'],rubric={'answer_index':0,'max_points':5})
    one=question_snapshot(q,{'targets':[{'skill_id':1,'target_level':3}]})
    two=question_snapshot(q,{'targets':[{'skill_id':1,'target_level':4}]})
    assert one['question_version']!=two['question_version']
    assert one['rubric']['rubric_version']==two['rubric']['rubric_version']


@pytest.mark.asyncio
async def test_preupgrade_failed_exam_cannot_mix_old_frozen_standard_with_new_judge(db_session,test_workspace):
    from app.models import Exam
    detail,_=await make_exam(db_session,test_workspace)
    exam=await db_session.get(Exam,detail['id'])
    eq=exam.questions[0]
    eq.frozen_snapshot={'title':eq.question.title,'qtype':'open','rubric':deepcopy(eq.question.rubric),
        'submitted_answer':{'text_answer':'Historical user answer'}}
    eq.creation_snapshot=None;exam.status='SCORING_FAILED';await db_session.commit()
    original=deepcopy(eq.frozen_snapshot)
    with pytest.raises(HTTPException) as error:await jobs.enqueue(db_session,test_workspace,exam)
    assert error.value.status_code==409
    assert eq.frozen_snapshot==original and exam.active_scoring_run_id is None
