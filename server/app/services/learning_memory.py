"""Learning facts -> reviewable summaries and small, recorded next-step changes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.models import EvolutionCandidate, Exam, MemoryItem, ScoringRun, ScoringStep, Workspace
from app.models.learning_feedback import LearningActivity, LearningGoal, LearningSubmission
from app.services.tracing import emit_event

LEARNING_TYPES = ('LearningObservation', 'LearningPreference')


def eligible_learning(row):
    return (row.mtype in LEARNING_TYPES and row.status == 'ACTIVE' and row.freshness == 'CURRENT'
            and row.confirmed_at is not None)


def memory_view(row):
    return {'id': row.id, 'revision': row.revision, 'status': row.status, 'freshness': row.freshness,
        'content': row.content, 'mtype': row.mtype, 'meta': row.meta,
        'eligible_for_personalization': eligible_learning(row),
        'confirmed_at': row.confirmed_at.isoformat() if row.confirmed_at else None,
        'scope_note': '确认只表示接受这段描述，不代表模型测评已经校准或整个能力域已经掌握。'}


async def attach_learning_context(session, goal):
    await session.flush()
    rows = (await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == goal.workspace_id,
        MemoryItem.mtype.in_(LEARNING_TYPES), MemoryItem.status == 'ACTIVE', MemoryItem.freshness == 'CURRENT',
        MemoryItem.confirmed_at.is_not(None)).order_by(MemoryItem.id.desc()).limit(100))).all()
    relevant = [row for row in rows if eligible_learning(row)
                and set((row.meta or {}).get('skill_ids', [])).intersection(goal.skill_ids)][:5]
    refs = [{'id': row.id, 'revision': row.revision, 'content': row.content,
             'origin_refs': (row.meta or {}).get('origin_refs')} for row in relevant]
    goal.meta = {**(goal.meta or {}), 'source_memories': refs,
        'context_notes': [f'已确认的有限范围学习观察：{row.content}' for row in relevant]}
    return refs


async def invalidate_memory_dependents(session, workspace_id, memory_id, reason):
    await session.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    rows = (await session.scalars(select(LearningGoal).where(LearningGoal.workspace_id == workspace_id)
        .with_for_update())).all()
    changed, todo, seen = [], [memory_id], set()
    while todo:
        withdrawn = todo.pop()
        if withdrawn in seen:
            continue
        seen.add(withdrawn)
        for goal in rows:
            refs = (goal.meta or {}).get('source_memories', [])
            if goal.id in changed or not any(ref.get('id') == withdrawn for ref in refs):
                continue
            goal.status = 'NEEDS_REVIEW'; goal.evidence_status = 'NEEDS_REVIEW'; goal.plan_revision += 1
            history = list((goal.meta or {}).get('plan_history', []))
            history.append({'revision': goal.plan_revision, 'at': datetime.now(timezone.utc).isoformat(),
                'reason': reason, 'changes': [], 'invalidated_memory_id': withdrawn, 'mastery_updated': False})
            goal.meta = {**(goal.meta or {}), 'source_memories': [], 'context_notes': [], 'plan_history': history,
                'invalidated_sources': [*(goal.meta or {}).get('invalidated_sources', []), {'memory_id': withdrawn, 'reason': reason}],
                'next_action': {'kind': 'REVIEW_SOURCE', 'message': '引用的学习记忆已纠正或撤回，请核对来源后再继续安排。'}}
            await cancel_pending_feedback(session, goal, reason)
            derived = (await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == workspace_id,
                MemoryItem.mtype.in_(LEARNING_TYPES), MemoryItem.meta['goal_id'].as_integer() == goal.id,
                MemoryItem.status.in_(('ACTIVE','CANDIDATE'))).with_for_update())).all()
            for memory in derived:
                memory.freshness = 'NEEDS_REVIEW'; memory.revision += 1; todo.append(memory.id)
            changed.append(goal.id)
    return changed


async def cancel_pending_feedback(session, goal, reason):
    """Fence already queued/running jobs in the same withdrawal transaction."""
    from types import SimpleNamespace

    from app.services import assessment_jobs
    pairs = (await session.execute(select(LearningSubmission, ScoringRun).join(ScoringRun,
        LearningSubmission.scoring_run_id == ScoringRun.id).where(LearningSubmission.workspace_id == goal.workspace_id,
        LearningSubmission.goal_id == goal.id, ScoringRun.mode == 'PRACTICE',
        ScoringRun.status.notin_(('COMPLETED','CANCELLED'))))).all()
    for submission, run in pairs:
        exam = await session.get(Exam, run.exam_id)
        if exam:
            await assessment_jobs.cancel(session, SimpleNamespace(id=goal.workspace_id), exam)
            submission.status = 'CANCELLED'
            run.error_category = 'SOURCE_WITHDRAWN'; run.error = reason


async def invalidate_assessment_dependents(session, workspace_id, exam_id, exam_question_id, reason):
    await session.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    await session.flush()
    goals = (await session.scalars(select(LearningGoal).where(LearningGoal.workspace_id == workspace_id,
        LearningGoal.origin_refs['exam_id'].as_integer() == exam_id,
        LearningGoal.origin_refs['exam_question_id'].as_integer() == exam_question_id).with_for_update())).all()
    for goal in goals:
        goal.status = 'NEEDS_REVIEW'; goal.evidence_status = 'DISPUTED'; goal.plan_revision += 1
        history = list((goal.meta or {}).get('plan_history', []))
        history.append({'revision':goal.plan_revision,'at':datetime.now(timezone.utc).isoformat(),
            'reason':reason,'changes':[],'mastery_updated':False})
        goal.meta = {**(goal.meta or {}),'context_notes':[],'plan_history':history,
            'next_action':{'kind':'REVIEW_SOURCE','message':'来源题目或标准存在待复核反馈；原成绩保留，暂不据此继续推荐。'}}
        await cancel_pending_feedback(session, goal, reason)
        memories = (await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == workspace_id,
            MemoryItem.mtype.in_(LEARNING_TYPES), MemoryItem.meta['goal_id'].as_integer() == goal.id,
            MemoryItem.status.in_(('ACTIVE','CANDIDATE'))).with_for_update())).all()
        for row in memories:
            row.freshness = 'NEEDS_REVIEW'; row.revision += 1
            # Other goals may have used this confirmed summary.
            await invalidate_memory_dependents(session, workspace_id, row.id, reason)
    return [goal.id for goal in goals]


async def _propose_teaching_change(session, goal, activity, submission):
    previous = (await session.scalars(select(LearningSubmission).where(
        LearningSubmission.workspace_id == goal.workspace_id, LearningSubmission.activity_id == activity.id,
        LearningSubmission.status == 'COMPLETED', LearningSubmission.id != submission.id)
        .order_by(LearningSubmission.id.desc()).limit(3))).all()
    repeated = False
    for old in previous:
        step = await session.scalar(select(ScoringStep).where(ScoringStep.run_id == old.scoring_run_id))
        rows = (step.result_json or {}).get('criterion_scores', []) if step else []
        if rows and any(c.get('score', 0) < 3 for c in rows):
            repeated = True; break
    if not repeated:
        return False
    key = f'learning-activity:{activity.id}:change-explanation-v1'
    existing = await session.scalar(select(EvolutionCandidate).where(EvolutionCandidate.workspace_id == goal.workspace_id,
        EvolutionCandidate.source_data['dedup_key'].as_string() == key))
    if existing:
        return True
    candidate = EvolutionCandidate(workspace_id=goal.workspace_id, short_code='EVO-L-' + uuid4().hex[:12],
        title='这项练习连续需要修改：尝试先用反例或更小的前置问题', kind='teaching', status='PROPOSED',
        rationale='仅观察到该活动有多次未满足约定标准的反馈；没有证明某种教法普遍更有效。',
        body={'policy_version': 'learning-feedback-v1', 'hypothesis': '先给一个小反例，再让用户解释机制可能更适合本次活动',
              'baseline': {'method':'current_explanation'}, 'candidate': {'method':'counterexample_then_explain'},
              'offline_check': {'status':'NOT_RUN'}, 'activation':'DISABLED', 'success_measure':'新的未曝光作答与后续保持表现'},
        source_data={'dedup_key':key, 'goal_id':goal.id, 'activity_id':activity.id,
                     'submission_ids':[old.id for old in previous] + [submission.id],
                     'sample_status':'INSUFFICIENT_EVIDENCE', 'learning_effect':'UNKNOWN'})
    session.add(candidate)
    return True


async def on_submission_completed(session, goal, activity, submission, run, steps):
    if not steps or any(not s.result_json or s.result_json.get('validation_status') != 'VALID' for s in steps):
        return
    rows = [row for step in steps for row in step.result_json.get('criterion_scores', [])]
    if not rows:
        return
    source_ref = f'learning-submission:{submission.id}'
    old = await session.scalar(select(MemoryItem).where(MemoryItem.workspace_id == goal.workspace_id,
        MemoryItem.mtype == 'LearningObservation', MemoryItem.source_ref == source_ref))
    if old is not None:
        return
    good = [r.get('title') or r['criterion_id'] for r in rows if r['score'] >= 3]
    missing = [str(point) for r in rows if r['score'] < 3 for point in r.get('missing_points', [])]
    kind = '项目制品检查' if activity.kind == 'PROJECT' else '同源学习练习'
    summary = f'本次{kind}第 {submission.revision} 次提交：'
    summary += ('满足的局部要求：' + '、'.join(good[:4]) + '。') if good else '尚未得到完整满足局部要求的反馈。'
    if missing:
        summary += '待补充：' + '；'.join(missing[:4])[:1200] + '。'
    summary += ('代码只读检查，未运行测试，个人理解仍需单独验证。' if activity.kind == 'PROJECT'
                else '这次记录不代表新的独立迁移验证。')
    summary += '延迟保持尚不能由这次提交证明。'
    row = MemoryItem(workspace_id=goal.workspace_id, mtype='LearningObservation', content=summary,
        source='SYSTEM', source_ref=source_ref, status='CANDIDATE', revision=1,
        freshness='CURRENT' if goal.status not in ('NEEDS_REVIEW','WITHDRAWN') else 'NEEDS_REVIEW',
        meta={'goal_id':goal.id, 'source_task_id':goal.source_task_id, 'activity_id':activity.id, 'submission_id':submission.id,
            'submission_revision':submission.revision, 'answer_revision_id':submission.answer_revision_id,
            'scoring_run_id':run.id, 'skill_ids':deepcopy(goal.skill_ids), 'origin_refs':deepcopy(goal.origin_refs),
            'assistance':deepcopy(submission.assistance), 'policy_version':goal.policy_version,
            'criterion_observations':deepcopy(rows), 'scope':'single_submission_observation',
            'calibration_status':'UNCALIBRATED', 'mastery_updated':False})
    session.add(row)
    latest = activity.latest_submission_id == submission.id
    if latest and goal.status == 'ACTIVE':
        if missing:
            next_action = {'kind':'REVISE', 'message':'先补这一处：' + missing[0][:500]}
            if await _propose_teaching_change(session, goal, activity, submission):
                next_action = {'kind':'CHANGE_EXPLANATION',
                    'message':'这项练习连续需要修改。先换一种小练习：构造一个会让当前解释失败的反例，再说明需要补哪条前提。只围绕：'
                    + missing[0][:400] + '。这是可选的理解步骤，本次固定评分要求保持不变。'}
        else:
            next_action = {'kind':'INDEPENDENT_RETEST', 'message':'本次局部练习已获得合格反馈；可完成阅读后，在未曝光的新题中独立验证。'}
        changes = []
        ready = (await session.scalars(select(LearningActivity).where(LearningActivity.goal_id == goal.id,
            LearningActivity.workspace_id == goal.workspace_id, LearningActivity.status == 'READY')
            .order_by(LearningActivity.position, LearningActivity.id))).all()
        # Preserve all started work. Limit the unstarted next queue; full history
        # and deferred activities remain visible and can be resumed explicitly.
        for item in ready[2:]:
            item.status = 'DEFERRED'; changes.append({'activity_id':item.id,'from':'READY','to':'DEFERRED'})
        goal.plan_revision += 1
        history = list((goal.meta or {}).get('plan_history', []))
        history.append({'revision':goal.plan_revision, 'at':datetime.now(timezone.utc).isoformat(),
            'reason':f'根据提交 #{submission.id} 的有效反馈，只调整尚未开始的后续活动。', 'changes':changes,
            'submission_id':submission.id, 'scoring_run_id':run.id, 'mastery_updated':False})
        goal.meta = {**(goal.meta or {}), 'next_action':next_action, 'plan_history':history}
    await emit_event(session, goal.workspace_id, 'learning.memory_candidate',
        {'goal_id':goal.id,'activity_id':activity.id,'submission_id':submission.id,'is_latest':latest,
         'long_term_active':False}, 'learning_submission', submission.id)
    await session.flush()


async def extra_path(session, goal):
    rows = (await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == goal.workspace_id,
        MemoryItem.mtype.in_(LEARNING_TYPES), MemoryItem.meta['goal_id'].as_integer() == goal.id,
        MemoryItem.status != 'DISMISSED').order_by(MemoryItem.id.desc()))).all()
    candidates = (await session.scalars(select(EvolutionCandidate).where(EvolutionCandidate.workspace_id == goal.workspace_id,
        EvolutionCandidate.source_data['goal_id'].as_integer() == goal.id))).all()
    refs = (goal.meta or {}).get('source_memories', [])
    live = (await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == goal.workspace_id,
        MemoryItem.id.in_([r['id'] for r in refs])))).all() if refs else []
    valid_refs = [{'id': r.id, 'revision':r.revision, 'content':r.content} for r in live if eligible_learning(r)]
    return {'memory_candidates':[memory_view(row) for row in rows],
        'plan_history':(goal.meta or {}).get('plan_history', []),
        'next_action':(goal.meta or {}).get('next_action'), 'source_memories':valid_refs,
        'evolution_candidates':[{'id':r.id, 'title':r.title, 'status':r.status, 'reason':r.rationale} for r in candidates]}


async def on_verification_completed(session, exam, task):
    """Consume an actual completed retest, never a learning-complete click."""
    result = (task.content or {}).get('retest_result') or {}
    if not task.retest_of or not result or not exam.questions:
        return
    goals = (await session.scalars(select(LearningGoal).where(LearningGoal.workspace_id == exam.workspace_id,
        LearningGoal.source_task_id == task.retest_of).with_for_update())).all()
    outcomes = {row['goal_id']: row for row in result.get('goal_outcomes', []) if row.get('goal_id') is not None}
    local_date = exam.completed_at.astimezone(timezone(timedelta(hours=8))).date()
    delayed = result.get('mode') == 'delayed' and task.due_date is not None and local_date >= task.due_date
    for goal in goals:
        outcome = outcomes.get(goal.id)
        if outcome is None:
            continue  # Legacy aggregate results and goals created after issuance cannot verify a goal.
        refs = goal.origin_refs or {}
        if any(refs.get(ref) != outcome.get(field) for ref, field in (
            ('exam_question_id', 'source_exam_question_id'), ('criterion_id', 'source_criterion_id'),
            ('rubric_version', 'source_rubric_version'), ('evidence_id', 'source_evidence_id'),
            ('answer_revision_id', 'source_answer_revision_id'),
        )):
            continue
        independent = outcome['independent']
        passed = outcome['status'] == 'PASSED' and independent is True
        criterion_passed = {'PASSED': True, 'FAILED': False}.get(outcome['status'])
        binding = outcome['binding']
        bindings = [binding] if binding else []
        source_ref = f'learning-verification:{goal.id}:{exam.id}'
        if await session.scalar(select(MemoryItem.id).where(MemoryItem.workspace_id == goal.workspace_id,
            MemoryItem.source_ref == source_ref)):
            continue
        current = goal.status == 'ACTIVE' and goal.evidence_status not in ('NEEDS_REVIEW','WITHDRAWN','STALE','DISPUTED')
        state = dict(goal.verification)
        if current and passed:
            state['understanding'] = 'INDEPENDENT_CORRECT'
            state['retention' if delayed else 'transfer'] = 'PASSED'
        elif current and outcome['status'] == 'FAILED' and independent is True:
            state['retention' if delayed else 'transfer'] = 'PENDING'
        goal.verification = state
        summary = ('这次新题按本场冻结标准满足了原失分项，且没有登记的辅助或题族曝光。' if passed else
                   '已保存这次新题作答；原失分项的独立验证尚未通过或独立性证据不足。')
        summary += '记录一次到期保持表现，不代表长期保持已普遍证明。' if delayed else '本次是即时检查，延迟保持仍需到期实际作答。'
        summary += '不同标准版本不直接相减分数；独立性基于系统记录和用户声明。'
        session.add(MemoryItem(workspace_id=goal.workspace_id, mtype='LearningObservation', source='SYSTEM',
            source_ref=source_ref, content=summary, status='CANDIDATE', revision=1,
            freshness='CURRENT' if current else 'NEEDS_REVIEW',
            meta={'goal_id':goal.id,'source_task_id':goal.source_task_id,'exam_id':exam.id,
                'scoring_run_id':exam.active_scoring_run_id,'retest_task_id':task.id,
                'answer_revision_ids':[binding['answer_revision_id']] if binding else [],
                'criterion_bindings':bindings,'independent_according_to_records':independent,
                'criterion_passed':criterion_passed,'verification_mode':'delayed' if delayed else 'immediate',
                'skill_ids':deepcopy(goal.skill_ids),'origin_refs':deepcopy(goal.origin_refs),'policy_version':goal.policy_version}))
        goal.meta = {**(goal.meta or {}), 'latest_verification':{'exam_id':exam.id,'retest_task_id':task.id,
            'criterion_bindings':bindings,'independence_recorded':independent,'status':outcome['status']},
            'next_action':{'kind':'WAIT_DELAYED' if passed and not delayed else 'REVIEW_RESULT',
                'message':'已保存新的作答表现，请核对候选阶段小结；延迟验证按实际到期完成记录。' if passed else
                          '本次尚不能确认原失分项独立通过；先查看这次具体反馈。'}}
    await session.flush()


async def evaluate_teaching_candidate(session, workspace_id, candidate):
    """Offline provenance checks, explicitly not a teaching-effect experiment."""
    from fastapi import HTTPException

    from app.models import EvolutionRun
    if candidate.workspace_id != workspace_id:
        raise HTTPException(404, '策略候选不存在')
    source = candidate.source_data or {}
    ids = source.get('submission_ids') or []
    rows = (await session.execute(select(LearningSubmission, ScoringRun).join(ScoringRun,
        LearningSubmission.scoring_run_id == ScoringRun.id).where(
        LearningSubmission.workspace_id == workspace_id, LearningSubmission.id.in_(ids),
        LearningSubmission.goal_id == source.get('goal_id'), LearningSubmission.activity_id == source.get('activity_id'),
        ScoringRun.workspace_id == workspace_id, ScoringRun.mode == 'PRACTICE', ScoringRun.status == 'COMPLETED'))).all()
    intact = bool(ids) and set(ids) == {s.id for s, _ in rows}
    unchanged = (candidate.body or {}).get('candidate') == {'method':'counterexample_then_explain'}
    check = {'status':'LINKS_CHECKED' if intact else 'NEEDS_REVIEW',
        'source_submission_count':len(rows),'source_ids_intact':intact,'scoring_standard_not_changed':unchanged,
        'teaching_content_quality':'NOT_HUMAN_REVIEWED','user_trial':'NOT_RUN',
        'effectiveness':'UNKNOWN','checked_at':datetime.now(timezone.utc).isoformat()}
    candidate.body = {**(candidate.body or {}),'offline_check':check,'activation':'DISABLED'}
    if candidate.status == 'PROPOSED':
        candidate.status = 'EVALUATED'
    run = EvolutionRun(workspace_id=workspace_id,candidate_id=candidate.id,sample_size=0,
        verdict='INSUFFICIENT_EVIDENCE',evaluated_at=datetime.now(timezone.utc),regression=None,
        metrics={'来源练习记录数':{'baseline':None,'candidate':len(rows),'delta':None,
                    'note':'核对已完成练习的版本关联；不是策略试验样本，也不证明教学效果。'},
                 '有效策略试用结果数':{'baseline':0,'candidate':0,'delta':None,
                    'note':'尚无获准试用后的可比较独立、项目或延迟结果；效果未知。'}})
    session.add(run); await session.flush()
    await emit_event(session, workspace_id, 'learning.strategy_offline_check',
        {'candidate_id':candidate.id,'links_intact':intact,'trial_sample_size':0,'effectiveness':'UNKNOWN'},
        'evolution_candidate',candidate.id)
    return run
