"""Retest behavior with explicit rubric and judgement fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_assessment_reliability import result_for

from app.api.v1.exams import CreateExamReq, SaveAnswerReq, SubmitExamReq, create_exam, submit_exam
from app.api.v1.learning import start_retest
from app.models import Evidence, Exam, ExamAnswer, ExamQuestion, LearningTask, Question, Skill
from app.models.learning_feedback import LearningGoal
from app.services import assessment_jobs, assessment_worker
from app.services.assessment_versions import ensure_revision, question_snapshot
from app.services.learning_exposure import independence_snapshot
from app.services.learning_loop import complete_retest_for_exam


async def question(session, workspace, skill, title, *criteria):
    row = Question(short_code='RT-' + uuid4().hex[:12], workspace_id=workspace.id,
        skill=skill, title=title, topic='rag', qtype='open',
        rubric={'max_points': 5, 'points': [
            {'title': criterion, 'detail': f'Explain {criterion} with evidence.'} for criterion in criteria]})
    session.add(row)
    await session.flush()
    return row


async def retest_fixture(session, workspace, *criteria):
    skill = Skill(key='retest-' + uuid4().hex[:12], name='Reviewed retest fixture')
    source = await question(session, workspace, skill, 'Original diagnosis question', *criteria)
    snapshot = question_snapshot(source)
    source_eq = ExamQuestion(question=source, order_no=1, creation_snapshot=snapshot,
        answer=ExamAnswer(text_answer='Original fixture answer with an observed gap', points=1, max_points=5,
            verdict='WRONG', judge_version='judge-v2'))
    diagnosis = Exam(workspace_id=workspace.id, title='Completed diagnosis fixture',
        kind='diagnosis', status='COMPLETED', questions=[source_eq])
    session.add(diagnosis)
    await session.flush()
    revision = await ensure_revision(session, source_eq)
    rubric_version = snapshot['rubric']['rubric_version']
    source_eq.frozen_snapshot = {**snapshot, 'answer_revision_id': revision.id,
        'submitted_answer': revision.payload}
    evidence = Evidence(short_code='RT-EV-' + uuid4().hex[:12], workspace_id=workspace.id, skill_id=skill.id,
        kind='EXAM_ANSWER', title='Scored source gap', meta={
            'assessed': True, 'validation_status': 'VALID', 'judge_version': 'judge-v2',
            'exam_id': diagnosis.id, 'exam_question_id': source_eq.id, 'question_id': source.id,
            'canonical_question_id': source.id, 'answer_revision_id': revision.id,
            'rubric_version': rubric_version, 'score': 1, 'max_points': 5})
    session.add(evidence)
    await session.flush()
    first = snapshot['rubric']['criteria'][0]
    task_fields = dict(workspace_id=workspace.id, skill_id=skill.id, source_exam_id=diagnosis.id,
        source_exam_question_id=source_eq.id, source_criterion=first['title'], source_evidence_id=evidence.id)
    parent = LearningTask(**task_fields, kind='UNDERSTAND', title='Learn source behavior', status='DONE',
        content={'observation': {'criterion_id': first['criterion_id'],
                                  'rubric_version': rubric_version, 'answer_revision_id': revision.id}})
    session.add(parent)
    await session.flush()
    task = LearningTask(**task_fields, kind='RETEST', title='Check source behavior', status='PENDING',
        retest_of=parent.id, content={**parent.content, 'verification_mode': 'immediate'})
    session.add(task)
    await session.flush()
    return skill, source_eq, parent, task


async def create_retest(session, workspace, task):
    detail = await create_exam(CreateExamReq(kind='focused', question_count=1, retest_task_id=task.id),
        workspace, session)
    return await session.get(Exam, detail['id'])


async def publish_scored_answer(session, workspace, exam, task, monkeypatch):
    async def fixture_config(*args, **kwargs):
        return {'provider': 'fixture', 'model': 'reviewed-retest-fixture'}, {}, {}

    monkeypatch.setattr(assessment_jobs, 'runtime_config', fixture_config)
    await submit_exam(exam.id, workspace, session, SubmitExamReq(
        answers=[SaveAnswerReq(order_no=1, text_answer='Fresh fixture answer')]))
    claim = await assessment_jobs.claim_step(session, 'reviewed-retest-fixture')
    assert claim is not None and claim.exam_id == exam.id
    await assessment_jobs.complete_step(session, claim, result_for(claim, criterion_score=4))
    factory = async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False)
    await assessment_worker.reconcile(factory)
    await session.refresh(task)
    return task.content['retest_result']


async def finish_retest(session, exam, task, scores, *, assisted=False, feedback_version=None):
    eq = exam.questions[0]
    rubric = eq.creation_snapshot['rubric']
    snapshot = {**deepcopy(eq.creation_snapshot), 'answer_revision_id': 42,
        'assistance': {'hint_used': assisted}}
    independence, assistance = await independence_snapshot(session, exam.workspace_id, exam, eq, snapshot)
    eq.frozen_snapshot = {**snapshot, 'assistance': assistance, 'independence': independence}
    score_percent = sum(c['weight'] * score / 4 for c, score in zip(rubric['criteria'], scores, strict=True))
    eq.answer = ExamAnswer(points=score_percent / 20, max_points=5,
        verdict='CORRECT' if score_percent >= 80 else 'PARTIAL', text_answer='Fresh fixture answer',
        feedback={'rubric_version': feedback_version or rubric['rubric_version'],
            'validation_status': 'VALID', 'answer_revision_id': 42,
            'criterion_scores': [{'criterion_id': c['criterion_id'], 'score': score, 'max_score': 4}
                                 for c, score in zip(rubric['criteria'], scores, strict=True)]})
    exam.status = 'COMPLETED'
    exam.completed_at = datetime.now(timezone.utc)
    exam.score = score_percent
    await session.flush()
    await complete_retest_for_exam(session, exam)
    return task.content['retest_result']


async def reviewed_mapping(session, workspace, question_row, index, behavior, version=1):
    from app.models import RetestCriterionMapping

    rubric = question_snapshot(question_row)['rubric']
    row = RetestCriterionMapping(workspace_id=workspace.id, question_id=question_row.id,
        rubric_version=rubric['rubric_version'], criterion_id=rubric['criteria'][index]['criterion_id'],
        behavior_key=behavior, behavior_version=version, reviewed_at=datetime.now(timezone.utc))
    session.add(row)
    await session.flush()
    return row


async def create_goal(session, parent, source_eq, index):
    rubric = source_eq.frozen_snapshot['rubric']
    criterion = rubric['criteria'][index]
    goal = LearningGoal(workspace_id=parent.workspace_id, source_task_id=parent.id,
        origin_key=uuid4().hex, skill_ids=[parent.skill_id], evidence_status='VALID',
        target_behavior=criterion['required_evidence'][0], success_criteria=criterion,
        success_criteria_version=rubric['rubric_version'],
        origin_refs={'exam_id': source_eq.exam_id, 'exam_question_id': source_eq.id,
                     'question_id': source_eq.question_id, 'criterion_id': criterion['criterion_id'],
                     'evidence_id': parent.source_evidence_id, 'answer_revision_id': source_eq.frozen_snapshot['answer_revision_id'],
                     'criterion_title': criterion['title'], 'rubric_version': rubric['rubric_version']},
        verification={'understanding': 'UNVERIFIED', 'transfer': 'PENDING', 'retention': 'NOT_DUE'})
    session.add(goal)
    await session.flush()
    return goal


@pytest.mark.asyncio
@pytest.mark.parametrize('different_reviewed_behaviors', [False, True])
async def test_same_title_without_equivalence_is_practice(db_session, test_workspace, different_reviewed_behaviors):
    skill, source_eq, _, task = await retest_fixture(db_session, test_workspace, 'Measure recall')
    target = await question(db_session, test_workspace, skill, 'New diagnosis question', 'Measure recall')
    if different_reviewed_behaviors:
        await reviewed_mapping(db_session, test_workspace, source_eq.question, 0, 'recall-at-k')
        await reviewed_mapping(db_session, test_workspace, target, 0, 'recall-under-filter')
    check = await start_retest(task.id, ws=test_workspace, session=db_session)
    exam = await db_session.get(Exam, check['exam_id'])
    assert exam.questions[0].question_id == target.id
    result = await finish_retest(db_session, exam, task, [4])
    assert result['passed'] is True
    assert result['source_criterion_covered'] is False
    assert result['source_criterion_passed'] is None
    assert result['goal_outcomes'][0]['status'] == 'UNMAPPED'


@pytest.mark.asyncio
@pytest.mark.parametrize('matching_feedback', [True, False])
async def test_reviewed_behavior_selects_differently_titled_question_and_freezes_binding(
        db_session, test_workspace, monkeypatch, matching_feedback):
    skill, source_eq, _, task = await retest_fixture(db_session, test_workspace, 'Measure recall')
    source = await reviewed_mapping(db_session, test_workspace, source_eq.question, 0, 'recall-at-k')
    decoy = await question(db_session, test_workspace, skill, 'Different version question', 'Measure recall')
    await reviewed_mapping(db_session, test_workspace, decoy, 0, 'recall-at-k', version=2)
    target = await question(db_session, test_workspace, skill, 'Transfer question', 'Define top-k evaluation')
    mapping = await reviewed_mapping(db_session, test_workspace, target, 0, 'recall-at-k')

    exam = await create_retest(db_session, test_workspace, task)
    eq = exam.questions[0]
    assert eq.question_id == target.id
    binding, = deepcopy(eq.creation_snapshot['retest_bindings'])
    assert binding['source_mapping_id'] == source.id
    assert binding['target_mapping_id'] == mapping.id
    assert binding['behavior_key'] == 'recall-at-k'
    assert binding['behavior_version'] == 1
    assert binding['criterion_id'] == eq.creation_snapshot['rubric']['criteria'][0]['criterion_id']

    await db_session.delete(mapping)
    target.rubric = {'max_points': 5, 'points': [{'title': 'Newly edited criterion'}]}
    await db_session.flush()
    if matching_feedback:
        result = await publish_scored_answer(db_session, test_workspace, exam, task, monkeypatch)
    else:
        result = await finish_retest(db_session, exam, task, [4], feedback_version='obsolete-rubric-version')
    assert eq.creation_snapshot['retest_bindings'] == [binding]
    assert result['source_criterion_covered'] is True
    assert result['source_criterion_passed'] is (True if matching_feedback else None)
    assert result['goal_outcomes'][0]['status'] == ('PASSED' if matching_feedback else 'UNVERIFIED')


@pytest.mark.asyncio
async def test_prompted_high_score_does_not_promote_learning_goal(db_session, test_workspace):
    skill, source_eq, parent, task = await retest_fixture(db_session, test_workspace, 'Measure recall')
    goal = await create_goal(db_session, parent, source_eq, 0)
    await reviewed_mapping(db_session, test_workspace, source_eq.question, 0, 'recall-at-k')
    target = await question(db_session, test_workspace, skill, 'Prompted transfer question', 'Top-k evaluation')
    await reviewed_mapping(db_session, test_workspace, target, 0, 'recall-at-k')
    exam = await create_retest(db_session, test_workspace, task)

    result = await finish_retest(db_session, exam, task, [4], assisted=True)
    outcome, = result['goal_outcomes']
    assert (outcome['goal_id'], outcome['status'], outcome['score']) == (goal.id, 'ASSISTED', 4)
    assert outcome['covered'] is True and outcome['independent'] is False
    assert result['source_criterion_passed'] is None
    assert goal.verification['transfer'] == 'PENDING'
    assert goal.verification['understanding'] == 'UNVERIFIED'


@pytest.mark.asyncio
@pytest.mark.parametrize('third_source_invalid', [False, True])
async def test_multi_goal_retest_preserves_each_outcome(db_session, test_workspace, third_source_invalid):
    skill, source_eq, parent, task = await retest_fixture(db_session, test_workspace,
        'Measure recall', 'Diagnose drift', 'Assess latency')
    target = await question(db_session, test_workspace, skill, 'Multi-criterion transfer question',
        'Top-k evaluation', 'Explain distribution shift', 'Measure response time')
    goals = [await create_goal(db_session, parent, source_eq, index) for index in range(3)]
    for index, behavior in enumerate(['recall-at-k', 'distribution-shift', 'assess-latency']):
        await reviewed_mapping(db_session, test_workspace, source_eq.question, index, behavior)
        if index < 2 or third_source_invalid:
            await reviewed_mapping(db_session, test_workspace, target, index, behavior)
    if third_source_invalid:
        goals[2].origin_refs = {**goals[2].origin_refs, 'evidence_id': None}
        await db_session.flush()
    check = await start_retest(task.id, ws=test_workspace, session=db_session)
    exam = await db_session.get(Exam, check['exam_id'])
    result = await finish_retest(db_session, exam, task, [4, 1, 4])
    outcomes = {outcome['goal_id']: outcome for outcome in result['goal_outcomes']}
    assert {goal_id: outcome['status'] for goal_id, outcome in outcomes.items()} == {
        goals[0].id: 'PASSED', goals[1].id: 'FAILED', goals[2].id: 'UNMAPPED'}
    assert [goal.verification['transfer'] for goal in goals] == ['PASSED', 'PENDING', 'PENDING']
    assert [goal.verification['retention'] for goal in goals] == ['NOT_DUE'] * 3


@pytest.mark.asyncio
async def test_mapping_with_withdrawn_source_keeps_goal_less_retest_as_practice(db_session, test_workspace):
    skill, source_eq, _, task = await retest_fixture(db_session, test_workspace, 'Measure recall')
    source_evidence = await db_session.get(Evidence, task.source_evidence_id)
    source_evidence.meta = {**source_evidence.meta, 'assessed': False}
    await reviewed_mapping(db_session, test_workspace, source_eq.question, 0, 'recall-at-k')
    candidate = await question(db_session, test_workspace, skill, 'Transfer question', 'Top-k evaluation')
    await reviewed_mapping(db_session, test_workspace, candidate, 0, 'recall-at-k')
    exam = await create_retest(db_session, test_workspace, task)
    result = await finish_retest(db_session, exam, task, [4])
    assert result['passed'] is True
    assert result['goal_outcomes'][0]['status'] == 'UNMAPPED'


@pytest.mark.asyncio
async def test_single_question_tie_prefers_original_loss_over_secondary_goal(db_session, test_workspace):
    skill, source_eq, parent, task = await retest_fixture(db_session, test_workspace, 'Recall', 'Drift')
    await create_goal(db_session, parent, source_eq, 0)
    await create_goal(db_session, parent, source_eq, 1)
    await reviewed_mapping(db_session, test_workspace, source_eq.question, 0, 'recall-at-k')
    await reviewed_mapping(db_session, test_workspace, source_eq.question, 1, 'detect-drift')
    secondary = await question(db_session, test_workspace, skill, 'Secondary question', 'Detect drift')
    await reviewed_mapping(db_session, test_workspace, secondary, 0, 'detect-drift')
    primary = await question(db_session, test_workspace, skill, 'Primary question', 'Top-k evaluation')
    await reviewed_mapping(db_session, test_workspace, primary, 0, 'recall-at-k')
    exam = await create_retest(db_session, test_workspace, task)
    assert exam.questions[0].question_id == primary.id
