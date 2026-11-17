from copy import deepcopy
import pytest
from fastapi import HTTPException
from app.api.v1 import bank,exams
from app.services.selection import covers_criterion
from app.services.assessment_rubric import compile_rubric
from test_assessment_queue import make_exam


@pytest.mark.asyncio
async def test_semantic_edit_preserves_old_version_and_requires_rubric_review(db_session,test_workspace):
    detail,questions=await make_exam(db_session,test_workspace)
    q=questions[0];before=deepcopy(q.rubric);original=q.title
    await bank.update_question(q.short_code,bank.QuestionUpdate(title='A different engineering scenario'),test_workspace,db_session)
    assert q.rubric['review_required'] is True
    version=q.generation_meta['question_versions'][-1]
    assert version['title']==original and version['rubric']==before
    fetched=await exams._exam_detail(db_session,test_workspace,detail['id'])
    assert fetched['questions'][0]['title']==original
    await exams.abort_exam(detail['id'],test_workspace,db_session)
    with pytest.raises(HTTPException) as error:
        await exams.create_exam(exams.CreateExamReq(kind='bank_exam',question_ids=[q.id]),test_workspace,db_session)
    assert error.value.status_code==409


def test_v2_retest_criterion_can_be_matched_without_legacy_points():
    from types import SimpleNamespace
    rubric=compile_rubric({'max_points':5,'points':[{'title':'Explain retrieval mechanism'}]},question_id=1,title='Retest')
    assert covers_criterion(SimpleNamespace(rubric=rubric),'Explain retrieval mechanism')
