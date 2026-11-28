"""Practice feedback reuses grading facts without adding mastery evidence."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_assessment_reliability import prepared, result_for

from app.models import CompetencySnapshot, Evidence, LearningTask, ScoringRun, SkillState
from app.services import assessment_jobs as jobs
from app.services import assessment_worker as worker


async def task_with_valid_origin(session, workspace, monkeypatch):
    exam, _, _ = await prepared(session, workspace, monkeypatch)
    claim = await jobs.claim_step(session, 'source')
    await jobs.complete_step(session, claim, result_for(claim, criterion_score=1))
    await worker.reconcile(async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False))
    task = await session.scalar(select(LearningTask).where(LearningTask.workspace_id == workspace.id))
    assert task is not None
    return task


@pytest.mark.asyncio
async def test_prepare_reuses_goal_and_frozen_source_without_creating_scores(db_session, test_workspace, monkeypatch):
    from app.models.learning_feedback import LearningActivity, LearningGoal
    from app.services.learning_feedback import prepare_path
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    first = await prepare_path(db_session, test_workspace, task.id)
    second = await prepare_path(db_session, test_workspace, task.id)
    assert first['goal']['id'] == second['goal']['id']
    assert len(first['activities']) == 3
    assert all(a['authoring_source'] == 'deterministic_prompt_template' for a in first['activities'])
    assert first['goal']['origin_refs']['answer_revision_id']
    assert first['goal']['origin_refs']['criterion_id']
    assert first['goal']['verification']['understanding'] == 'UNVERIFIED'
    assert await db_session.scalar(select(func.count()).select_from(LearningGoal).where(LearningGoal.workspace_id == test_workspace.id)) == 1
    assert await db_session.scalar(select(func.count()).select_from(LearningActivity).where(LearningActivity.workspace_id == test_workspace.id)) == 3


@pytest.mark.asyncio
async def test_nonempty_practice_submission_is_idempotent_and_never_publishes_mastery(db_session, test_workspace, monkeypatch):
    from app.models.learning_feedback import LearningActivity, LearningSubmission
    from app.services.learning_feedback import prepare_path, submission_view, submit_activity
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    path = await prepare_path(db_session, test_workspace, task.id)
    activity = await db_session.get(LearningActivity, path['activities'][0]['id'])
    counts = {model: await db_session.scalar(select(func.count()).select_from(model).where(model.workspace_id == test_workspace.id))
              for model in (Evidence, CompetencySnapshot, SkillState)}
    payload = {'text_answer':'A genuine fixture attempt explaining this mechanism.', 'ai_declared':False}
    submission = await submit_activity(db_session, test_workspace, activity, payload, 'same-request', activity.version)
    same = await submit_activity(db_session, test_workspace, activity, payload, 'same-request', activity.version)
    assert same.id == submission.id
    with pytest.raises(HTTPException) as error:
        await submit_activity(db_session, test_workspace, activity, {**payload,'text_answer':'different'}, 'same-request', activity.version)
    assert error.value.status_code == 409
    await db_session.commit()
    claim = await jobs.claim_step(db_session, 'practice')
    assert claim.payload['learning_activity_kind'] == 'CONCEPT'
    run = await db_session.get(ScoringRun, claim.run_id)
    assert run.mode == 'PRACTICE'
    await jobs.complete_step(db_session, claim, result_for(claim, criterion_score=3))
    await worker.reconcile(async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False))
    view = await submission_view(db_session, test_workspace, submission.id)
    assert view['status'] == 'COMPLETED' and view['feedback']['correct']
    assert view['feedback']['versions']['rubric_version']
    for model, before in counts.items():
        assert await db_session.scalar(select(func.count()).select_from(model).where(model.workspace_id == test_workspace.id)) == before
    assert await db_session.scalar(select(func.count()).select_from(LearningSubmission).where(LearningSubmission.workspace_id == test_workspace.id)) == 1


@pytest.mark.asyncio
async def test_project_validation_failure_retains_known_provider_attempt_metadata(monkeypatch):
    from app.services import project_practice
    from app.services.assessment_rubric import AssessmentJudgeError
    result = {'raw_response':'PRIVATE_FIXTURE_RESPONSE', 'usage':{'known':True,'total_tokens':17},
        'request_meta':{'request_id':'fixture-request','request_started':True,'input_chars':29},
        'criterion_scores':[]}
    async def scored(*args, **kwargs): return deepcopy(result)
    def rejected(*args, **kwargs): raise AssessmentJudgeError('INVALID_PROJECT_CLAIM', '未核验的执行声明')
    monkeypatch.setattr(worker, 'judge_frozen_answer', scored)
    monkeypatch.setattr(project_practice, 'validate_review_result', rejected)
    claim = SimpleNamespace(workspace_id=1, run_id=1, step_id=1,
        payload={'qtype':'open','question_id':1,'answer_revision_id':1,'title':'Fixture',
        'rubric':{},'learning_activity_kind':'PROJECT','submitted_answer':{'text_answer':'Source fixture'}})
    with pytest.raises(AssessmentJudgeError) as error:
        await worker.evaluate(claim, {'provider':'fixture','model':'fixture','base_url':'https://fixture.invalid','api_key':'fixture'})
    assert error.value.usage == result['usage']
    assert error.value.request_meta['request_id'] == 'fixture-request'
    assert error.value.raw_response == result['raw_response']


@pytest.mark.asyncio
async def test_assistance_is_monotonic_and_frozen_into_submission(db_session, test_workspace, monkeypatch):
    from app.models.learning_feedback import LearningActivity
    from app.services.learning_feedback import prepare_path, record_assistance, submit_activity
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    path = await prepare_path(db_session, test_workspace, task.id)
    activity = await db_session.get(LearningActivity, path['activities'][0]['id'])
    await record_assistance(db_session, test_workspace, activity.id, 'REFERENCE', 'read-answer', activity.version)
    submission = await submit_activity(db_session, test_workspace, activity,
        {'text_answer':'Attempt after seeing a reference.', 'ai_declared':False}, 'assisted-attempt', activity.version)
    assert submission.assistance['reference_viewed'] is True
    with pytest.raises(HTTPException) as error:
        await submit_activity(db_session, SimpleNamespace(id=test_workspace.id + 999), activity,
            {'text_answer':'Cross workspace'}, 'cross', activity.version)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_invalid_or_mismatched_source_cannot_create_a_goal(db_session, test_workspace, monkeypatch):
    from sqlalchemy import delete

    from app.models.learning_feedback import LearningGoal
    from app.services.learning_feedback import ensure_goal
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    await db_session.execute(delete(LearningGoal).where(LearningGoal.workspace_id == test_workspace.id))
    evidence = await db_session.get(Evidence, task.source_evidence_id)
    original = deepcopy(evidence.meta)
    for changes in ({'validation_status':'INVALID'}, {'exam_question_id':999999}, {'answer_revision_id':999999}):
        evidence.meta = {**original, **changes}
        await db_session.flush()
        with pytest.raises(HTTPException) as error:
            await ensure_goal(db_session, test_workspace, task)
        assert error.value.status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(LearningGoal).where(LearningGoal.workspace_id == test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_valid_assisted_observation_can_support_learning_but_not_independent_mastery(db_session, test_workspace, monkeypatch):
    from sqlalchemy import delete

    from app.models.learning_feedback import LearningGoal
    from app.services.competency import assessed_observations
    from app.services.learning_feedback import assert_goal_sources, ensure_goal
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    await db_session.execute(delete(LearningGoal).where(LearningGoal.workspace_id == test_workspace.id))
    evidence = await db_session.get(Evidence, task.source_evidence_id)
    evidence.meta = {**evidence.meta, 'independent':False, 'assistance':{'reference_viewed':True}}
    await db_session.flush()
    assert assessed_observations([evidence]) == []
    goal = await ensure_goal(db_session, test_workspace, task)
    assert goal.evidence_status == 'ASSISTED_OBSERVATION'
    assert goal.origin_refs['assistance']['reference_viewed'] is True
    assert goal.origin_refs['independent'] is False
    await assert_goal_sources(db_session, test_workspace, goal)


@pytest.mark.asyncio
async def test_old_feedback_is_retained_but_cannot_replace_newer_submission_state(db_session, test_workspace, monkeypatch):
    from app.models.learning_feedback import LearningActivity, LearningGoal
    from app.services.learning_feedback import path_view, prepare_path, submission_view, submit_activity
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    path = await prepare_path(db_session, test_workspace, task.id)
    activity = await db_session.get(LearningActivity, path['activities'][0]['id'])
    old = await submit_activity(db_session, test_workspace, activity, {'text_answer':'Old attempt.'}, 'first', activity.version)
    new = await submit_activity(db_session, test_workspace, activity, {'text_answer':'Revised attempt.'}, 'second', activity.version, old.id)
    await db_session.commit()
    claim = await jobs.claim_step(db_session, 'older-result')
    assert claim.run_id == old.scoring_run_id
    await jobs.complete_step(db_session, claim, result_for(claim, criterion_score=4))
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    await worker.reconcile(factory)
    goal = await db_session.get(LearningGoal, activity.goal_id, populate_existing=True)
    assert goal.verification['understanding'] == 'UNVERIFIED'
    historical = await submission_view(db_session, test_workspace, old.id)
    assert historical['has_feedback'] and historical['is_latest'] is False
    compact = await path_view(db_session, test_workspace, task.id)
    assert all('feedback' not in s for s in compact['submissions'])
    claim = await jobs.claim_step(db_session, 'newer-result')
    assert claim.run_id == new.scoring_run_id
    await jobs.complete_step(db_session, claim, result_for(claim, criterion_score=1))
    await worker.reconcile(factory)
    await db_session.refresh(activity)
    assert activity.status == 'NEEDS_REVISION'


@pytest.mark.asyncio
async def test_practice_questions_cannot_be_used_for_official_exam(db_session, test_workspace, monkeypatch):
    from app.api.v1 import exams
    from app.models.learning_feedback import LearningActivity
    from app.services.learning_feedback import prepare_path
    from app.services.selection import select_questions_for_exam
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    path = await prepare_path(db_session, test_workspace, task.id)
    activity = await db_session.get(LearningActivity, path['activities'][0]['id'])
    with pytest.raises(HTTPException) as error:
        await exams.create_exam(exams.CreateExamReq(kind='bank_exam', question_ids=[activity.question_id]), test_workspace, db_session)
    assert error.value.status_code == 409
    picked, _ = await select_questions_for_exam(db_session, test_workspace.id, SimpleNamespace(), 20)
    assert all((q.generation_meta or {}).get('assessment_use') != 'PRACTICE_ONLY' for q in picked)


@pytest.mark.asyncio
async def test_paused_activity_and_withdrawn_source_block_new_dispatch(db_session, test_workspace, monkeypatch):
    from app.models.learning_feedback import LearningActivity
    from app.services.learning_feedback import prepare_path, submit_activity
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    path = await prepare_path(db_session, test_workspace, task.id)
    activity = await db_session.get(LearningActivity, path['activities'][0]['id'])
    activity.status = 'DEFERRED'; await db_session.flush()
    with pytest.raises(HTTPException) as error:
        await submit_activity(db_session, test_workspace, activity, {'text_answer':'Not silently resumed'}, 'pause', activity.version)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_worker_rechecks_source_before_any_external_call(db_session, test_workspace, monkeypatch):
    from app.models import ScoringStep
    from app.models.learning_feedback import LearningActivity
    from app.services.learning_feedback import prepare_path, submit_activity
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    path = await prepare_path(db_session, test_workspace, task.id)
    activity = await db_session.get(LearningActivity, path['activities'][0]['id'])
    await submit_activity(db_session, test_workspace, activity, {'text_answer':'Queued answer.'}, 'queued', activity.version)
    await db_session.commit()
    claim = await jobs.claim_step(db_session, 'source-recheck')
    evidence = await db_session.get(Evidence, task.source_evidence_id)
    evidence.meta = {**evidence.meta, 'validation_status':'NEEDS_REVIEW'}
    await db_session.commit()
    monkeypatch.setattr(worker, 'runtime_config', jobs.runtime_config)
    async def forbidden(*args, **kwargs): raise AssertionError('A withdrawn source must not reach the provider')
    monkeypatch.setattr(worker, 'evaluate', forbidden)
    await worker.execute_claim(claim, claim.owner, async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False))
    step = await db_session.get(ScoringStep, claim.step_id, populate_existing=True)
    assert step.status == 'FAILED' and step.error_category == 'SOURCE_CHANGED'
    activity.status = 'READY'
    evidence = await db_session.get(Evidence, task.source_evidence_id)
    evidence.meta = {**evidence.meta, 'validation_status':'NEEDS_REVIEW'}
    await db_session.flush()
    with pytest.raises(HTTPException) as error:
        await submit_activity(db_session, test_workspace, activity, {'text_answer':'Not new evidence'}, 'stale', activity.version)
    assert error.value.status_code == 409
