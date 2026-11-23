"""Confirmed learning summaries are context, never a replacement for grades."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import Evidence, EvolutionCandidate, MemoryItem
from app.models.learning_feedback import LearningActivity, LearningGoal


async def goal_fixture(session, ws):
    goal = LearningGoal(workspace_id=ws.id, origin_key=uuid4().hex, target_behavior='解释并验证重试',
        origin_refs={'exam_id':1, 'criterion_id':'c1', 'rubric_version':'fixture'},
        skill_ids=[101], success_criteria={}, success_criteria_version='fixture', evidence_status='VALID',
        verification={'understanding':'UNVERIFIED','transfer':'UNVERIFIED','retention':'NOT_DUE'}, meta={})
    session.add(goal); await session.flush()
    activity = LearningActivity(workspace_id=ws.id, goal_id=goal.id, kind='CONCEPT', title='fixture',
        content_version=uuid4().hex, snapshot={}, status='NEEDS_REVISION', latest_submission_id=10)
    session.add(activity); await session.commit()
    return goal, activity


@pytest.mark.asyncio
async def test_feedback_creates_candidate_once_without_personal_evidence(db_session, test_workspace):
    from app.services.learning_memory import on_submission_completed
    goal, activity = await goal_fixture(db_session, test_workspace)
    submission = SimpleNamespace(id=10, revision=1, assistance={}, answer_revision_id=11, payload={}, scoring_run_id=12)
    run = SimpleNamespace(id=12, judge_version='judge-v2')
    steps = [SimpleNamespace(result_json={'validation_status':'VALID', 'criterion_scores':[
        {'criterion_id':'c1','title':'重试','score':2,'answer_quotes':['重复发送'], 'missing_points':['并发约束']} ]})]
    await on_submission_completed(db_session, goal, activity, submission, run, steps)
    await on_submission_completed(db_session, goal, activity, submission, run, steps)
    await db_session.commit()
    rows = (await db_session.scalars(select(MemoryItem).where(MemoryItem.workspace_id==test_workspace.id))).all()
    assert len(rows) == 1
    assert rows[0].status == 'CANDIDATE' and rows[0].confirmed_at is None
    assert rows[0].meta['submission_id'] == 10 and rows[0].meta['answer_revision_id'] == 11
    assert '并发约束' in rows[0].content
    assert await db_session.scalar(select(func.count(Evidence.id)).where(Evidence.workspace_id==test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_system_failure_creates_no_weakness_memory(db_session, test_workspace):
    from app.services.learning_memory import on_submission_completed
    goal, activity = await goal_fixture(db_session, test_workspace)
    submission = SimpleNamespace(id=10, revision=1, assistance={}, answer_revision_id=11, payload={})
    await on_submission_completed(db_session, goal, activity, submission, SimpleNamespace(id=12),
        [SimpleNamespace(result_json={'validation_status':'INVALID','error':'TIMEOUT'})])
    assert await db_session.scalar(select(func.count(MemoryItem.id)).where(MemoryItem.workspace_id==test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_confirmed_context_excludes_candidate_stale_and_deleted(db_session, test_workspace):
    from datetime import datetime, timezone

    from app.services.learning_memory import attach_learning_context, invalidate_memory_dependents
    goal, _ = await goal_fixture(db_session, test_workspace)
    records = []
    for status, freshness in [('CANDIDATE','CURRENT'),('ACTIVE','CURRENT'),('ACTIVE','NEEDS_REVIEW'),('DISMISSED','CURRENT')]:
        row = MemoryItem(workspace_id=test_workspace.id, mtype='LearningObservation', content=status+freshness,
            status=status, freshness=freshness, confirmed_at=datetime.now(timezone.utc) if status=='ACTIVE' else None,
            meta={'skill_ids':[101]})
        db_session.add(row); records.append(row)
    await db_session.flush()
    await attach_learning_context(db_session, goal)
    assert [x['id'] for x in goal.meta['source_memories']] == [records[1].id]
    records[1].status = 'DISMISSED'
    await invalidate_memory_dependents(db_session, test_workspace.id, records[1].id, '用户撤回')
    assert goal.status == 'NEEDS_REVIEW'
    assert goal.meta['context_notes'] == []
    await attach_learning_context(db_session, goal)
    assert goal.meta['source_memories'] == []


@pytest.mark.asyncio
async def test_memory_review_version_and_workspace_guards(db_session, test_workspace):
    from app.api.v1.learning_memory import MemoryReviewIn, review_memories
    goal, _ = await goal_fixture(db_session, test_workspace)
    row = MemoryItem(workspace_id=test_workspace.id, mtype='LearningObservation', content='本次观察',
        status='CANDIDATE', meta={'goal_id':goal.id}, revision=1, freshness='CURRENT')
    db_session.add(row); await db_session.commit()
    result = await review_memories(goal.id, MemoryReviewIn(items=[{'candidate_id':row.id,
        'expected_revision':1,'action':'confirm','edited_content':'本次范围有限的观察'}]), ws=test_workspace, session=db_session)
    assert result['results'][0]['status'] == 'CONFIRMED'
    assert row.confirmed_at and row.content == '本次范围有限的观察'
    second = await review_memories(goal.id, MemoryReviewIn(items=[{'candidate_id':row.id,
        'expected_revision':1,'action':'confirm'}]), ws=test_workspace, session=db_session)
    assert second['results'][0]['status'] == 'CONFLICT'
    with pytest.raises(HTTPException) as error:
        await review_memories(goal.id, MemoryReviewIn(items=[]), ws=SimpleNamespace(id=-99), session=db_session)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_shorten_preserves_started_work_and_does_not_infer_weakness(db_session, test_workspace):
    from app.api.v1.learning_memory import AdjustIn, adjust_goal
    goal, started = await goal_fixture(db_session, test_workspace)
    ready = []
    for i in range(3):
        row = LearningActivity(workspace_id=test_workspace.id, goal_id=goal.id, kind='CONCEPT', title=f'fixture-{i}',
            content_version=uuid4().hex, snapshot={}, status='READY', position=i+1)
        db_session.add(row); ready.append(row)
    await db_session.commit()
    result = await adjust_goal(goal.id, AdjustIn(action='shorten',reason='今天只有十分钟',expected_plan_revision=1),
        ws=test_workspace, session=db_session)
    assert started.status == 'NEEDS_REVISION'
    assert sum(r.status=='DEFERRED' for r in ready) >= 1
    assert goal.plan_revision == 2
    assert goal.verification['understanding'] == 'UNVERIFIED'
    assert result['mastery_updated'] is False
    assert await db_session.scalar(select(func.count(EvolutionCandidate.id)).where(EvolutionCandidate.workspace_id==test_workspace.id)) == 0


@pytest.mark.asyncio
async def test_generic_confirmation_cannot_bypass_learning_version_review(db_session, test_workspace):
    from app.api.v1.memory import confirm_memory
    goal, _ = await goal_fixture(db_session, test_workspace)
    row = MemoryItem(workspace_id=test_workspace.id, mtype='LearningObservation', content='待核对',
        status='CANDIDATE', meta={'goal_id':goal.id}, freshness='CURRENT')
    db_session.add(row); await db_session.commit()
    with pytest.raises(HTTPException) as error:
        await confirm_memory(row.id, ws=test_workspace, session=db_session)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_memory_delete_invalidates_actual_dependent_plan(db_session, test_workspace):
    from datetime import datetime, timezone

    from app.api.v1.memory import delete_memory
    goal, _ = await goal_fixture(db_session, test_workspace)
    row = MemoryItem(workspace_id=test_workspace.id, mtype='LearningObservation', content='已确认',
        status='ACTIVE', meta={'skill_ids':[101]}, confirmed_at=datetime.now(timezone.utc), freshness='CURRENT')
    db_session.add(row); await db_session.flush()
    goal.meta = {'source_memories':[{'id':row.id, 'revision':1}], 'context_notes':['已确认']}
    await db_session.commit()
    await delete_memory(row.id, ws=test_workspace, session=db_session)
    assert row.status == 'DISMISSED'
    assert goal.status == 'NEEDS_REVIEW'
    assert goal.meta['context_notes'] == []


def test_general_eligibility_requires_learning_confirmation():
    from app.services.project_candidates import eligible
    row = SimpleNamespace(status='ACTIVE', freshness='CURRENT', mtype='LearningObservation', confirmed_at=None)
    assert eligible(row) is False


@pytest.mark.asyncio
async def test_legacy_aggregate_retest_does_not_promote_learning_goal(db_session, test_workspace):
    from datetime import datetime, timezone

    from app.models import LearningTask
    from app.services.learning_memory import on_verification_completed
    goal, _ = await goal_fixture(db_session, test_workspace)
    parent = LearningTask(workspace_id=test_workspace.id, title='source fixture', kind='UNDERSTAND', status='DONE')
    db_session.add(parent); await db_session.flush()
    goal.source_task_id = parent.id
    goal.verification = {**goal.verification, 'transfer': 'PENDING'}
    check = LearningTask(workspace_id=test_workspace.id, title='new question', kind='RETEST', retest_of=parent.id,
        status='DONE', content={'retest_result':{'source_criterion_passed':True,'source_criterion_covered':True,
            'mode':'immediate','criterion_bindings':[{'rubric_version':'new-version'}]}})
    db_session.add(check); await db_session.flush()
    exam = SimpleNamespace(id=555, workspace_id=test_workspace.id, active_scoring_run_id=777,
        completed_at=datetime.now(timezone.utc), questions=[SimpleNamespace(frozen_snapshot={
            'independence':{'eligible_for_independent_verification':True},
            'answer_revision_id':42})])
    await on_verification_completed(db_session, exam, check)
    assert goal.verification['transfer'] == 'PENDING'
    assert goal.verification['understanding'] == 'UNVERIFIED'
    assert goal.verification['retention'] == 'NOT_DUE'


@pytest.mark.asyncio
async def test_source_dispute_marks_dependent_goal_and_summary_for_review(db_session, test_workspace):
    from app.services.learning_memory import invalidate_assessment_dependents
    goal, _ = await goal_fixture(db_session, test_workspace)
    goal.origin_refs = {'exam_id':5,'exam_question_id':6}
    row = MemoryItem(workspace_id=test_workspace.id, mtype='LearningObservation', content='观察',status='CANDIDATE',
        meta={'goal_id':goal.id},freshness='CURRENT')
    db_session.add(row); await db_session.flush()
    await invalidate_assessment_dependents(db_session, test_workspace.id, 5, 6, '评分标准有歧义待复核')
    assert goal.status == 'NEEDS_REVIEW' and goal.evidence_status == 'DISPUTED'
    assert row.freshness == 'NEEDS_REVIEW'
    assert goal.verification['understanding'] == 'UNVERIFIED'


@pytest.mark.asyncio
async def test_repeated_difficulty_proposes_but_never_activates_strategy(db_session, test_workspace, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from test_assessment_reliability import result_for
    from test_learning_feedback import task_with_valid_origin

    from app.services import assessment_jobs as jobs
    from app.services import assessment_worker as worker
    from app.services.learning_feedback import prepare_path, submit_activity
    task = await task_with_valid_origin(db_session, test_workspace, monkeypatch)
    path = await prepare_path(db_session, test_workspace, task.id)
    activity = await db_session.get(LearningActivity, path['activities'][0]['id'])
    parent = None
    for n in range(2):
        submitted = await submit_activity(db_session,test_workspace,activity,{'text_answer':f'Explicit fixture revision {n}'},
            f'fixture-{n}',activity.version,parent)
        parent=submitted.id; await db_session.commit()
        claim = await jobs.claim_step(db_session,'fixture')
        await jobs.complete_step(db_session,claim,result_for(claim,criterion_score=2))
        await worker.reconcile(async_sessionmaker(db_session.bind,expire_on_commit=False,autoflush=False))
    rows = (await db_session.scalars(select(EvolutionCandidate).where(EvolutionCandidate.workspace_id==test_workspace.id))).all()
    assert len(rows) == 1
    assert rows[0].status == 'PROPOSED' and rows[0].body['activation'] == 'DISABLED'
    assert rows[0].source_data['learning_effect'] == 'UNKNOWN'
    goal = await db_session.get(LearningGoal,path['goal']['id']); await db_session.refresh(goal)
    assert goal.meta['next_action']['kind'] == 'CHANGE_EXPLANATION'
    from app.services.evolution import _exam_history_metrics, collect_signals, evaluate_candidate
    assert (await collect_signals(db_session,test_workspace.id))['completed_exams'] == 1
    assert (await _exam_history_metrics(db_session,test_workspace.id))['sample_size'] == 1
    evaluation = await evaluate_candidate(db_session,test_workspace.id,rows[0])
    assert evaluation.verdict == 'INSUFFICIENT_EVIDENCE'
    assert evaluation.sample_size == 0  # Repeated practice is not a trial outcome sample.
    assert rows[0].body['offline_check']['status'] == 'LINKS_CHECKED'
    assert '弱域命中率' not in evaluation.metrics


@pytest.mark.asyncio
async def test_withdrawal_cancels_already_queued_feedback_and_fences_running_worker(db_session, test_workspace, monkeypatch):
    from datetime import datetime, timezone

    from test_learning_feedback import task_with_valid_origin

    from app.api.v1.memory import delete_memory
    from app.models import ScoringRun
    from app.services import assessment_jobs as jobs
    from app.services.learning_feedback import prepare_path, submit_activity
    task = await task_with_valid_origin(db_session,test_workspace,monkeypatch)
    path = await prepare_path(db_session,test_workspace,task.id)
    goal = await db_session.get(LearningGoal,path['goal']['id'])
    memory = MemoryItem(workspace_id=test_workspace.id,mtype='LearningObservation',content='confirmed fixture',
        status='ACTIVE',confirmed_at=datetime.now(timezone.utc),meta={'skill_ids':goal.skill_ids},freshness='CURRENT')
    db_session.add(memory); await db_session.flush()
    goal.meta = {**goal.meta, 'source_memories':[{'id':memory.id,'revision':memory.revision}]}
    await db_session.commit()
    activity = await db_session.get(LearningActivity,path['activities'][0]['id'])
    submitted = await submit_activity(db_session,test_workspace,activity,{'text_answer':'Nonempty fixture answer'},'pending',1)
    await db_session.commit()
    claim = await jobs.claim_step(db_session,'old-worker')
    await delete_memory(memory.id,ws=test_workspace,session=db_session)
    run = await db_session.get(ScoringRun,submitted.scoring_run_id); await db_session.refresh(run)
    assert run.status == 'CANCELLED'
    assert await jobs.heartbeat(db_session,claim) is False
