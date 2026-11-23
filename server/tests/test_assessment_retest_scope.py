"""Coverage and target-version regressions; no model or personal database calls."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete
from test_assessment_snapshot_review import fixture_question

from app.models import Evidence, JobStandard, JobStandardTarget, LearningTask, Question
from app.services.competency import recompute_skill_states
from app.services.learning_loop import complete_retest_for_exam
from app.services.selection import select_questions_for_exam


@pytest.mark.asyncio
async def test_latest_seed_target_version_is_selected_without_multiple_rows_failure(db_session, test_workspace):
    _, skill = await fixture_question(db_session, test_workspace)
    standards = [JobStandard(role='AI Agent 应用开发工程师', level='P6',
        version=uuid4().hex[:12], is_seed=True) for _ in range(2)]
    db_session.add_all(standards)
    await db_session.flush()
    db_session.add_all([JobStandardTarget(standard_id=s.id, skill_id=skill.id, weight_pct=100, target_level=target)
        for s, target in zip(standards, [1.0, 5.0])])
    for number in range(3):
        db_session.add(Evidence(workspace_id=test_workspace.id, skill_id=skill.id, short_code='TV-' + uuid4().hex[:12],
            kind='EXAM_ANSWER', title='Explicit scored fixture', meta={'assessed': True,
                'question_id':number + 1, 'canonical_question_id':number + 1, 'exam_id':1,
                'exam_question_id':number + 1, 'judge_version':'judge-v2', 'validation_status':'VALID',
                'score':4, 'max_points':5}))
    await db_session.flush()
    try:
        states = await recompute_skill_states(db_session, test_workspace.id)
        state = next(s for s in states if s.skill_id == skill.id)
        assert state.estimated_level == 4 and state.status == 'BELOW_TARGET'
    finally:
        await db_session.execute(delete(JobStandardTarget).where(JobStandardTarget.standard_id.in_([s.id for s in standards])))
        await db_session.execute(delete(JobStandard).where(JobStandard.id.in_([s.id for s in standards])))


@pytest.mark.asyncio
async def test_auto_selection_skips_semantically_edited_unreviewed_question(db_session, test_workspace):
    unreviewed, skill = await fixture_question(db_session, test_workspace)
    unreviewed.rubric = {**unreviewed.rubric, 'review_required': True}
    eligible = Question(short_code='OK-' + uuid4().hex[:12], workspace_id=test_workspace.id,
        title='Reviewed fixture alternative', qtype='open', topic='rag', skill_id=skill.id, skill=skill,
        rubric={'max_points':5, 'points':[{'title':'Mechanism'}]})
    db_session.add(eligible)
    await db_session.flush()
    picked, _ = await select_questions_for_exam(db_session, test_workspace.id,
        SimpleNamespace(), count=1, skill_key=skill.key)
    assert [q.id for q in picked] == [eligible.id]


@pytest.mark.asyncio
@pytest.mark.parametrize('frozen_title', ['Original criterion', 'Other criterion'])
async def test_unmapped_retest_ignores_stale_shared_coverage(
        db_session, test_workspace, frozen_title):
    task = LearningTask(workspace_id=test_workspace.id, title='Fixture retest', kind='RETEST',
        status='PENDING', source_criterion='Original criterion',
        content={'source_criterion_covered':True, 'assessment_scope':'Stale other-exam scope'})
    db_session.add(task)
    await db_session.flush()
    frozen = {'schema_version':2, 'rubric_version':'rubric:frozen',
        'criteria':[{'criterion_id':'c2', 'title':frozen_title}]}
    # Live question has already changed and is not evidence of this exam's scope.
    eq = SimpleNamespace(creation_snapshot={'rubric':deepcopy(frozen)}, frozen_snapshot={'rubric':deepcopy(frozen)},
        question=SimpleNamespace(rubric={'points':[{'title':'Original criterion'}]}),
        answer=SimpleNamespace(verdict='CORRECT', feedback={'rubric_version':'rubric:frozen',
            'validation_status':'VALID',
            'criterion_scores':[{'criterion_id':'c2', 'score':4, 'max_score':4}],
            'hit_points':['Original criterion'], 'missed_points':[]}))
    exam = SimpleNamespace(id=123456, workspace_id=test_workspace.id, retest_task_id=task.id,
        status='COMPLETED', score=100, completed_at=datetime.now(timezone.utc), questions=[eq])
    await complete_retest_for_exam(db_session, exam)
    result = task.content['retest_result']
    assert result['source_criterion_covered'] is False
    assert result['source_criterion_passed'] is None
    assert result['assessment_scope'] != 'Stale other-exam scope'
