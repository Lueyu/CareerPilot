"""Source-bound practice, with the assessment queue as the only grading truth."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import (Evidence, Exam, ExamAnswer, ExamQuestion, LearningTask, Question,
                        ScoringRun, ScoringStep, Skill, Workspace, MemoryItem, Event)
from app.models.learning_feedback import LearningGoal, LearningActivity, LearningSubmission
from app.services.assessment_versions import digest, question_snapshot
from app.services.assessment_rubric import rubric_version, validate_rubric
from app.services.tracing import emit_event

POLICY_VERSION = 'learning-feedback-v1'
VERIFICATION = {'understanding':'UNVERIFIED', 'practice':'NOT_SUBMITTED',
                'transfer':'UNVERIFIED', 'retention':'NOT_DUE'}
BAD_SOURCE = {'DISPUTED', 'STALE', 'WITHDRAWN', 'NEEDS_REVIEW'}


async def _assert_no_dispute(session, workspace_id, exam_question_id):
    pending = await session.scalar(select(Event.id).where(Event.workspace_id == workspace_id,
        Event.kind == 'assessment.question_feedback',
        Event.payload['exam_question_id'].as_integer() == exam_question_id).limit(1))
    if pending is not None:
        raise HTTPException(409, '来源题目或标准有待复核反馈，先保留历史记录；暂不据此安排新练习')


async def _lock_workspace(session, ws):
    if await session.scalar(select(Workspace.id).where(Workspace.id == ws.id).with_for_update()) is None:
        raise HTTPException(404, '工作空间不可用')


async def _task(session, ws, task_id):
    task = await session.scalar(select(LearningTask).where(LearningTask.id == task_id, LearningTask.workspace_id == ws.id))
    if task is None: raise HTTPException(404, '学习任务不存在')
    return task


async def assert_goal_sources(session, ws, goal):
    """Check current eligibility without substituting new facts into a frozen plan."""
    if goal.workspace_id != ws.id: raise HTTPException(404, '学习目标不存在')
    if goal.status != 'ACTIVE' or goal.evidence_status in BAD_SOURCE:
        raise HTTPException(409, '目标已暂停或来源需要复核，不能继续生成新反馈')
    refs = goal.origin_refs or {}
    await _assert_no_dispute(session, ws.id, refs.get('exam_question_id'))
    evidence = await session.scalar(select(Evidence).where(Evidence.id == refs.get('evidence_id'),
        Evidence.workspace_id == ws.id).execution_options(populate_existing=True))
    from app.services.competency import assessed_observations
    if not evidence or not assessed_observations([evidence], include_assisted=True):
        raise HTTPException(409, '原表现来源已撤回或不再有效，需要复核学习目标')
    meta = evidence.meta or {}
    if any(meta.get(key) != refs.get(key) for key in ('exam_id','exam_question_id','question_id')):
        raise HTTPException(409, '目标引用与当前证据不一致，需要复核')
    if meta.get('judge_version') in ('judge-v2','rule-v2') and any(
            meta.get(key) != refs.get(key) for key in ('answer_revision_id','rubric_version')):
        raise HTTPException(409, '来源评分版本已改变，不能沿用旧计划')
    source = (await session.execute(select(ExamQuestion, Exam).join(Exam).where(
        ExamQuestion.id == refs.get('exam_question_id'), Exam.id == refs.get('exam_id'),
        Exam.workspace_id == ws.id))).first()
    if source is None or source[1].status != 'COMPLETED':
        raise HTTPException(409, '来源答卷已撤回或不再是有效完成状态，需要复核')
    frozen = source[0].frozen_snapshot or {}
    if meta.get('judge_version') in ('judge-v2','rule-v2') and (
            frozen.get('answer_revision_id') != refs.get('answer_revision_id')
            or (frozen.get('rubric') or {}).get('rubric_version') != refs.get('rubric_version')):
        raise HTTPException(409, '来源答卷的冻结版本与目标不一致，需要复核')
    for reference in (goal.meta or {}).get('source_memories', []):
        memory = await session.scalar(select(MemoryItem).where(MemoryItem.id == reference.get('id'),
            MemoryItem.workspace_id == ws.id).execution_options(populate_existing=True))
        if (not memory or memory.revision != reference.get('revision') or memory.status != 'ACTIVE'
                or memory.freshness != 'CURRENT' or memory.confirmed_at is None):
            raise HTTPException(409, '引用的记忆已变更或撤回，请复核后再继续，旧内容不会用于新反馈')


def _origin(task):
    observation = (task.content or {}).get('observation') or {}
    return {'exam_id':task.source_exam_id, 'exam_question_id':task.source_exam_question_id,
        'evidence_id':task.source_evidence_id, 'criterion_id':observation.get('criterion_id'),
        'criterion_title':task.source_criterion, 'answer_revision_id':observation.get('answer_revision_id'),
        'rubric_version':observation.get('rubric_version'), 'judge_version':observation.get('judge_version')}


async def ensure_goal(session, ws, task):
    if task.workspace_id != ws.id: raise HTTPException(404, '学习任务不存在')
    await _lock_workspace(session, ws)
    refs = _origin(task)
    await _assert_no_dispute(session, ws.id, refs.get('exam_question_id'))
    key = digest(refs if task.source_evidence_id else {'manual_task_id':task.id})
    existing = await session.scalar(select(LearningGoal).where(LearningGoal.workspace_id == ws.id,
        LearningGoal.origin_key == key))
    if existing: return existing
    evidence = await session.scalar(select(Evidence).where(Evidence.id == task.source_evidence_id,
        Evidence.workspace_id == ws.id)) if task.source_evidence_id else None
    if not evidence or evidence.kind != 'EXAM_ANSWER' or not (evidence.meta or {}).get('assessed'):
        raise HTTPException(409, '该任务尚无有效答题观察，请先补充诊断；系统失败不能变成个人短板')
    from app.services.competency import assessed_observations
    if not assessed_observations([evidence], include_assisted=True):
        raise HTTPException(409, '来源观察没有通过有效评分校验，不能解释为能力短板')
    source = (await session.execute(select(ExamQuestion, Exam).join(Exam).where(
        ExamQuestion.id == task.source_exam_question_id, Exam.workspace_id == ws.id))).first()
    if source is None or source[1].status != 'COMPLETED':
        raise HTTPException(409, '来源考试没有有效完成，不能据此建立学习目标')
    eq, exam = source
    ev_meta = evidence.meta or {}
    if (ev_meta.get('exam_id') != exam.id or ev_meta.get('exam_question_id') != eq.id
            or task.source_exam_id != exam.id or ev_meta.get('question_id') != eq.question_id):
        raise HTTPException(409, '任务、答卷与证据的来源关系不一致，需要复核')
    snapshot = eq.frozen_snapshot or {}
    rubric = snapshot.get('rubric') or {}
    if ev_meta.get('judge_version') in ('judge-v2', 'rule-v2') and (
            ev_meta.get('answer_revision_id') != snapshot.get('answer_revision_id')
            or not snapshot.get('answer_revision_id') or ev_meta.get('rubric_version') != rubric.get('rubric_version')):
        raise HTTPException(409, '观察的答卷或评分标准版本与原始记录不一致')
    observation = (task.content or {}).get('observation') or {}
    criteria = rubric.get('criteria') or rubric.get('points') or []
    criterion = next((c for c in criteria if isinstance(c, dict) and
        (c.get('criterion_id') == refs['criterion_id'] if refs['criterion_id'] else c.get('title') == task.source_criterion)), None)
    if criterion is None and snapshot.get('qtype') == 'mcq':
        criterion = {'title':task.source_criterion, 'detail':task.acceptance or '解释判断依据和可验证的例子'}
    if criterion is None: raise HTTPException(409, '原评分项无法与冻结标准关联，需要复核')
    refs.update(question_id=snapshot.get('question_id', eq.question_id),
        question_version=snapshot.get('question_version'), scoring_run_id=exam.active_scoring_run_id,
        answer_revision_id=snapshot.get('answer_revision_id'),
        rubric_version=rubric.get('rubric_version', 'legacy-unversioned'),
        evidence_ref=evidence.short_code, assistance=deepcopy(ev_meta.get('assistance') or {}),
        independent=ev_meta.get('independent'))
    success = deepcopy(criterion)
    status = 'VALID' if (evidence.meta or {}).get('validation_status') == 'VALID' else 'LEGACY_UNCALIBRATED'
    if not assessed_observations([evidence]): status = 'ASSISTED_OBSERVATION'
    goal = LearningGoal(workspace_id=ws.id, source_task_id=task.id, origin_key=key,
        origin_refs=refs, skill_ids=[task.skill_id] if task.skill_id else [],
        gap_type=task.gap_type or 'ObservedCriterionGap',
        target_behavior=task.acceptance or f'能够独立解释并验证「{task.source_criterion}」',
        benchmark_version=deepcopy(snapshot.get('job_standard') or {}),
        success_criteria=success, success_criteria_version=digest(success),
        evidence_status=status, status='ACTIVE', plan_revision=1, policy_version=POLICY_VERSION,
        verification=deepcopy(VERIFICATION), meta={'calibration_status':(evidence.meta or {}).get('calibration_status', 'LEGACY_UNCALIBRATED'),
            'source_task_ids':[task.id], 'source_observation':deepcopy(observation), 'source_memories':[]})
    session.add(goal)
    await session.flush()
    if importlib.util.find_spec('app.services.learning_memory'):
        from app.services import learning_memory
        if hasattr(learning_memory, 'attach_learning_context'):
            await learning_memory.attach_learning_context(session, goal)
    return goal


async def create_activity(session, ws, goal, kind, title, snapshot, question=None, position=0):
    if goal.workspace_id != ws.id: raise HTTPException(404, '学习目标不存在')
    if kind not in ('CONCEPT', 'PROJECT'): raise HTTPException(422, '不支持的活动类型')
    if goal.evidence_status in BAD_SOURCE: raise HTTPException(409, '目标来源需要复核，不能继续生成活动')
    await assert_goal_sources(session, ws, goal)
    content_version = digest({'kind':kind, 'title':title, 'snapshot':snapshot})
    existing = await session.scalar(select(LearningActivity).where(LearningActivity.goal_id == goal.id,
        LearningActivity.workspace_id == ws.id, LearningActivity.content_version == content_version))
    if existing: return existing
    frozen = deepcopy(snapshot)
    spec = frozen.get('assessment_question') or {}
    if question is None:
        if not spec.get('title') or not spec.get('rubric'):
            raise HTTPException(422, '活动必须在提交前确定题目和评分标准')
        skill_id = spec.get('skill_id') or (goal.skill_ids[0] if goal.skill_ids else None)
        skill = await session.get(Skill, skill_id) if skill_id else None
        question = Question(workspace_id=ws.id, short_code='LP-' + uuid4().hex[:16], title=spec['title'],
            qtype='open', topic=skill.key if skill else 'general', skill_id=skill_id, skill=skill,
            difficulty=spec.get('difficulty', 3), rubric=deepcopy(spec['rubric']),
            answer_html=spec.get('answer_html'), source_type='LEARNING_PRACTICE',
            canonical_question_id=goal.origin_refs.get('question_id'),
            generation_meta={'assessment_use':'PRACTICE_ONLY','learning_goal_id':goal.id})
        session.add(question)
        await session.flush()
        if question.rubric.get('schema_version') == 2:
            bound = {**question.rubric, 'question_id':question.id,
                'question_version':'learning-question:' + digest({'title':question.title, 'goal':goal.id, 'content':content_version})}
            bound['rubric_version'] = rubric_version(bound)
            question.rubric = validate_rubric(bound)
    if question.workspace_id != ws.id or (question.generation_meta or {}).get('assessment_use') != 'PRACTICE_ONLY':
        raise HTTPException(409, '练习只能使用当前空间专用题，不能读取正式封存题')
    frozen['assessment_question'] = question_snapshot(question, goal.benchmark_version)
    frozen.setdefault('prompt', question.title)
    frozen.setdefault('required_output', '提交自己的文字回答；辅助条件与答案一起记录')
    activity = LearningActivity(workspace_id=ws.id, goal_id=goal.id, kind=kind, title=title,
        status='READY', position=position, version=1, content_version=content_version,
        snapshot=frozen, question_id=question.id, meta={})
    session.add(activity)
    await session.flush()
    return activity


async def prepare_path(session, ws, task_id):
    await _lock_workspace(session, ws)
    task = await _task(session, ws, task_id)
    goal = await ensure_goal(session, ws, task)
    await assert_goal_sources(session, ws, goal)
    criterion = goal.success_criteria
    title = criterion.get('title') or task.source_criterion
    requirements = criterion.get('required_evidence') or [criterion.get('detail') or title]
    for index, (label, instruction) in enumerate([
        ('解释机制', '用自己的话解释该机制的输入、处理与结果。'),
        ('结合情境', '给出一个你明确标为假设的具体情境，解释机制如何满足约束及何时会失效。'),
        ('设计验证', '说明怎样用一个最小例子或检查验证该机制，并说明检查结果能和不能证明什么。'),
    ]):
        prompt = f'围绕「{title}」练习：{instruction}\n请覆盖本次预先确定的要求：' + '；'.join(requirements)
        notes = (goal.meta or {}).get('context_notes')
        if notes: prompt += '\n相关已确认学习背景（不作为本次评分要求）：' + str(notes)
        # The same disclosed source criterion remains authoritative. These are
        # practice scaffolds, never unseen transfer questions or LLM-authored facts.
        raw = {'max_points':5, 'points':[{'title':f'{title}：{label}', 'detail':'；'.join([*requirements, instruction])}]}
        if criterion.get('score_anchors'):
            source = await session.scalar(select(ExamQuestion.frozen_snapshot).where(ExamQuestion.id == task.source_exam_question_id))
            rb = deepcopy((source or {}).get('rubric') or {})
            if rb.get('schema_version') == 2:
                single = {**deepcopy(criterion), 'title':f'{title}：{label}', 'weight':100,
                    'required_evidence':[*requirements, instruction]}
                single['score_anchors'] = [{**anchor, 'description':anchor['description'] +
                    (' 本练习还要求：' + instruction if anchor['score'] >= 3 else '')}
                    for anchor in single['score_anchors']]
                rb.update(criteria=[single], critical_errors=[e for e in rb.get('critical_errors', [])
                    if e.get('criterion_id') == single['criterion_id']], reference_standard={
                    'alignment_status':'SOURCE_CRITERION_PRACTICE_NOT_HUMAN_REVIEWED', 'sections':[]},
                    calibration_status='AI_DRAFT', coverage_gaps=[*rb.get('coverage_gaps', []),
                    '按原评分项确定性派生练习；尚未人工审核，不能当作新的独立迁移验证。'])
                rb['rubric_version'] = rubric_version(rb)
                raw = rb
        await create_activity(session, ws, goal, 'CONCEPT', f'{index + 1}. {label}：{title}',
            {'prompt':prompt, 'authoring_source':'deterministic_prompt_template',
             'learning_content':{k:task.content[k] for k in ('analogy','example','principles','steps') if k in (task.content or {})},
             'assessment_question':{'title':prompt, 'rubric':raw, 'skill_id':task.skill_id},
             'source_criterion_version':goal.success_criteria_version}, position=index)
    await session.commit()
    return await path_view(session, ws, task_id)


def _assistance(activity, ai_declared=False):
    meta = activity.meta or {}
    return {'hint_used':bool(meta.get('hint_used')), 'reference_viewed':bool(meta.get('reference_viewed')),
        'feedback_seen':bool(meta.get('feedback_seen')), 'ai_declared':ai_declared,
        'events':deepcopy(meta.get('assistance_events') or [])}


async def record_assistance(session, ws, activity_id, kind, request_key, expected_activity_revision):
    if kind not in ('HINT', 'REFERENCE'): raise HTTPException(422, '未知辅助类型')
    await _lock_workspace(session, ws)
    activity = await session.scalar(select(LearningActivity).where(LearningActivity.id == activity_id,
        LearningActivity.workspace_id == ws.id).with_for_update())
    if activity is None: raise HTTPException(404, '学习活动不存在')
    if activity.version != expected_activity_revision: raise HTTPException(409, '活动版本已变化，请重新读取')
    if activity.status in ('DEFERRED','SKIPPED','PAUSED'):
        raise HTTPException(409, '活动已暂缓或跳过，请先显式恢复')
    meta = deepcopy(activity.meta or {})
    events = meta.get('assistance_events') or []
    previous = next((event for event in events if event['request_key'] == request_key), None)
    if previous and previous['kind'] != kind: raise HTTPException(409, '辅助请求标识已用于不同内容')
    if not previous:
        events.append({'kind':kind, 'request_key':request_key, 'at':datetime.now(timezone.utc).isoformat()})
    meta.update(assistance_events=events, **{'hint_used' if kind == 'HINT' else 'reference_viewed':True})
    activity.meta = meta
    goal = await session.get(LearningGoal, activity.goal_id)
    await assert_goal_sources(session, ws, goal)
    if kind == 'HINT':
        content = '先对照以下已确定要求，补一个具体机制或例子：' + '；'.join(
            goal.success_criteria.get('required_evidence') or [goal.success_criteria.get('detail') or goal.target_behavior])
    else:
        task = await _task(session, ws, goal.source_task_id)
        source = await session.scalar(select(ExamQuestion.frozen_snapshot).join(Exam).where(
            ExamQuestion.id == task.source_exam_question_id, Exam.workspace_id == ws.id))
        from app.services.teaching import _strip_html
        content = _strip_html((source or {}).get('answer_html') or '', 20000) or '该来源没有完整参考答案，请以冻结评分要求为准：' + goal.target_behavior
    await session.commit()  # Record exposure before releasing any answer text.
    return {'assistance':_assistance(activity), 'content':content}


async def submit_activity(session, ws, activity, payload, request_key, expected_activity_revision, parent_submission_id=None):
    await _lock_workspace(session, ws)
    activity = await session.scalar(select(LearningActivity).where(LearningActivity.id == activity.id,
        LearningActivity.workspace_id == ws.id).with_for_update().execution_options(populate_existing=True))
    if activity is None: raise HTTPException(404, '学习活动不存在')
    if not isinstance(payload, dict): raise HTTPException(422, '提交内容必须是对象')
    request_hash = digest({'payload':payload, 'activity_version':expected_activity_revision, 'parent_submission_id':parent_submission_id})
    previous = await session.scalar(select(LearningSubmission).where(LearningSubmission.workspace_id == ws.id,
        LearningSubmission.activity_id == activity.id, LearningSubmission.request_key == request_key))
    if previous:
        if (previous.meta or {}).get('request_hash', previous.input_hash) != request_hash:
            raise HTTPException(409, '该请求已经保存不同提交，请使用新的请求标识')
        return previous
    if activity.version != expected_activity_revision: raise HTTPException(409, '活动版本已变化，请重新读取')
    goal = await session.get(LearningGoal, activity.goal_id)
    if goal.status != 'ACTIVE' or goal.evidence_status in BAD_SOURCE:
        raise HTTPException(409, '目标已暂停或来源需要复核，不能创建新反馈')
    if activity.status in ('DEFERRED','SKIPPED','PAUSED'):
        raise HTTPException(409, '活动已暂缓或跳过，请先显式恢复')
    await assert_goal_sources(session, ws, goal)
    if activity.kind == 'PROJECT':
        from app.services.project_practice import validate_project_submission
        payload = await validate_project_submission(session, ws, activity, payload)
    input_hash = digest({'payload':payload, 'activity_version':expected_activity_revision, 'parent_submission_id':parent_submission_id})
    text = payload.get('answer_text') if activity.kind == 'PROJECT' else payload.get('text_answer', payload.get('answer_text'))
    if not isinstance(text, str) or not text.strip() or len(text) > 60000:
        raise HTTPException(422, '请提交1至60000字符的真实练习内容；空答不用于反馈验收')
    if type(payload.get('ai_declared', False)) is not bool: raise HTTPException(422, 'AI辅助声明必须是布尔值')
    parent = None
    if parent_submission_id is not None:
        parent = await session.scalar(select(LearningSubmission).where(LearningSubmission.id == parent_submission_id,
            LearningSubmission.activity_id == activity.id, LearningSubmission.workspace_id == ws.id))
        if parent is None: raise HTTPException(404, '原提交不属于该活动')
        if activity.latest_submission_id != parent.id: raise HTTPException(409, '已有更新提交，请保留编辑后基于最新版本继续')
    elif activity.latest_submission_id is not None:
        raise HTTPException(409, '继续修改需要明确关联上一次提交')
    revision = (await session.scalar(select(func.max(LearningSubmission.revision)).where(
        LearningSubmission.activity_id == activity.id)) or 0) + 1
    assistance = _assistance(activity, payload.get('ai_declared', False))
    declared = payload.get('assistance') or {}
    if not isinstance(declared, dict): raise HTTPException(422, '辅助声明必须是对象')
    for key, aliases in {'hint_used':('hint_used','used_hint'),
                         'reference_viewed':('reference_viewed','viewed_answer'),
                         'ai_declared':('ai_declared','ai_assisted')}.items():
        for alias in aliases:
            if any(alias in source and type(source[alias]) is not bool for source in (declared, payload)):
                raise HTTPException(422, '辅助声明必须是布尔值')
        assistance[key] = assistance[key] or any(source.get(alias) is True for source in (declared, payload) for alias in aliases)
    assistance['prior_attempt'] = parent is not None
    assistance['source_family_exposed'] = activity.kind == 'CONCEPT'
    submission = LearningSubmission(workspace_id=ws.id, goal_id=goal.id, activity_id=activity.id,
        revision=revision, parent_submission_id=parent_submission_id, request_key=request_key,
        payload=deepcopy(payload), assistance=assistance, input_hash=input_hash,
        activity_version=activity.version, status='QUEUED', meta={'request_hash':request_hash})
    session.add(submission)
    await session.flush()
    question = await session.get(Question, activity.question_id)
    if question is None or question.workspace_id != ws.id: raise HTTPException(409, '活动冻结题目不可用')
    frozen = deepcopy(activity.snapshot['assessment_question'])
    frozen.update(learning_activity_kind=activity.kind, learning_submission_id=submission.id,
        learning_activity_id=activity.id, assistance=assistance, assessment_mode='PRACTICE')
    if activity.kind == 'PROJECT':
        frozen.update(project_contract=deepcopy(activity.snapshot.get('project_contract') or payload.get('project_contract') or {}),
            test_evidence=deepcopy(payload.get('test_evidence') or {}),
            project_source_text=payload.get('project_source_text'),
            project_snapshot_id=payload.get('project_snapshot_id'), project_snapshot_hash=payload.get('project_snapshot_hash'))
    exam = Exam(workspace_id=ws.id, title=f'练习反馈：{activity.title}', kind='practice', status='ACTIVE',
        duration_minutes=240, questions=[])
    session.add(exam)
    await session.flush()
    eq = ExamQuestion(exam_id=exam.id, question_id=question.id, question=question, order_no=1,
        creation_snapshot=frozen, answer=ExamAnswer(text_answer=text, revision=1, max_points=frozen['rubric']['max_points']))
    exam.questions.append(eq)
    await session.flush()
    from app.services import assessment_jobs
    run = await assessment_jobs.enqueue(session, ws, exam, mode='PRACTICE')
    submission.exam_id, submission.scoring_run_id = exam.id, run.id
    submission.answer_revision_id = eq.frozen_snapshot['answer_revision_id']
    activity.latest_submission_id, activity.status = submission.id, 'IN_PROGRESS'
    await emit_event(session, ws.id, 'learning.submission_created', {'submission_id':submission.id,
        'goal_id':goal.id, 'activity_id':activity.id, 'revision':revision, 'scoring_run_id':run.id}, 'learning_submission', submission.id)
    await session.flush()
    return submission


def _feedback(result):
    rows = result.get('criterion_scores') or []
    correct, gaps = [], []
    for row in rows:
        if row['score'] > 0:
            correct.append({'criterion_id':row['criterion_id'], 'quotes':row.get('answer_quotes', []),
                'why':row.get('reasoning_summary', ''), 'score':row['score']})
        if row['score'] < 3:
            gaps.append({'criterion_id':row['criterion_id'], 'kind':'MISSING_OR_UNCLEAR',
                'quotes':row.get('answer_quotes', []), 'why':row.get('why_not_higher', ''),
                'minimal_hint':'；'.join(row.get('missing_points') or [])})
    return {'correct':correct, 'gaps':gaps, 'criterion_scores':rows,
        'next_action':'REVISE' if gaps else 'NEW_CONTEXT_RETEST',
        'scope_note':'这是本次练习反馈，辅助情况已记录；没有增加个人能力等级或独立题目覆盖。',
        'versions':{key:result.get(key) for key in ('question_version','rubric_version','judge_version')},
        'project_feedback':result.get('project_feedback'), 'reasoning':result.get('reasoning')}


async def submission_view(session, ws, submission_id, *, include_feedback=True, mark_seen=False):
    submission = await session.scalar(select(LearningSubmission).where(LearningSubmission.id == submission_id,
        LearningSubmission.workspace_id == ws.id).execution_options(populate_existing=True))
    if submission is None: raise HTTPException(404, '练习提交不存在')
    run = await session.scalar(select(ScoringRun).where(ScoringRun.id == submission.scoring_run_id,
        ScoringRun.workspace_id == ws.id).execution_options(populate_existing=True)) if submission.scoring_run_id else None
    from app.services.assessment_jobs import view
    task = await view(session, run.id) if run else None
    status = run.status if run else submission.status
    step = await session.scalar(select(ScoringStep).where(ScoringStep.run_id == run.id)) if run else None
    has_feedback = bool(status == 'COMPLETED' and step and step.result_json)
    result = {'id':submission.id, 'submission_id':submission.id, 'activity_id':submission.activity_id,
        'revision':submission.revision, 'parent_submission_id':submission.parent_submission_id,
        'status':status, 'exam_id':submission.exam_id, 'scoring_run_id':submission.scoring_run_id,
        'assistance':submission.assistance, 'has_feedback':has_feedback,
        'can_retry':bool(task and task['can_retry']), 'error':task['error'] if task else None,
        'next_retry_at':task.get('next_retry_at') if task else None,
        'created_at':submission.created_at.isoformat(), 'is_latest':False}
    activity = await session.get(LearningActivity, submission.activity_id)
    result['is_latest'] = activity.latest_submission_id == submission.id
    if include_feedback:
        result['payload'] = submission.payload
        result['feedback'] = _feedback(step.result_json) if has_feedback else None
    if has_feedback and include_feedback and mark_seen:
        await _lock_workspace(session, ws)
        await session.refresh(activity)
        activity.meta = {**(activity.meta or {}), 'feedback_seen':True}
        await session.commit()
    return result


async def path_view(session, ws, task_id):
    task = await _task(session, ws, task_id)
    key = digest(_origin(task) if task.source_evidence_id else {'manual_task_id':task.id})
    goal = await session.scalar(select(LearningGoal).where(LearningGoal.workspace_id == ws.id,
        LearningGoal.origin_key == key))
    output = {'task_id':task.id, 'task_status':task.status, 'goal':None, 'activities':[], 'submissions':[],
        'learning_content':{key:value for key,value in (task.content or {}).items()
            if key in ('analogy','example','principles','steps','verify','source')}}
    if goal is None: return output
    output['goal'] = {key:getattr(goal, key) for key in ('id','target_behavior','status','evidence_status',
        'plan_revision','success_criteria_version','origin_refs','verification','policy_version','benchmark_version')}
    output['goal']['allowed_actions'] = (['prepare'] + (['retest'] if task.status == 'DONE' else ['complete_reading'])
        if goal.status == 'ACTIVE' and goal.evidence_status not in BAD_SOURCE else [])
    activities = (await session.scalars(select(LearningActivity).where(LearningActivity.goal_id == goal.id,
        LearningActivity.workspace_id == ws.id).order_by(LearningActivity.position, LearningActivity.id))).all()
    submissions = (await session.scalars(select(LearningSubmission).where(LearningSubmission.goal_id == goal.id,
        LearningSubmission.workspace_id == ws.id).order_by(LearningSubmission.id.desc()))).all()
    views = {row.id:await submission_view(session, ws, row.id, include_feedback=False) for row in submissions}
    output['submissions'] = list(views.values())
    for activity in activities:
        rb = (activity.snapshot.get('assessment_question') or {}).get('rubric') or {}
        output['activities'].append({'id':activity.id, 'kind':activity.kind, 'title':activity.title,
            'status':activity.status, 'position':activity.position, 'revision':activity.version,
            'content_version':activity.content_version, 'prompt':activity.snapshot.get('prompt', ''),
            'authoring_source':activity.snapshot.get('authoring_source'),
            'required_output':activity.snapshot.get('required_output'),
            'success_criteria':rb.get('criteria') or rb.get('points'),
            'allowed_actions':['submit','hint','reference'] if goal.status == 'ACTIVE' and goal.evidence_status not in BAD_SOURCE
                and activity.status not in ('DEFERRED','SKIPPED','PAUSED') else [],
            'assistance':_assistance(activity), 'latest_submission':views.get(activity.latest_submission_id),
            'content':activity.snapshot.get('learning_content'), 'project_contract':activity.snapshot.get('project_contract')})
    if importlib.util.find_spec('app.services.learning_memory'):
        from app.services import learning_memory
        if hasattr(learning_memory, 'extra_path'): output.update(await learning_memory.extra_path(session, goal))
    return output


async def publish_practice(session, ws, exam, run, steps):
    submission = await session.scalar(select(LearningSubmission).where(LearningSubmission.scoring_run_id == run.id,
        LearningSubmission.workspace_id == ws.id).with_for_update())
    if submission is None: raise ValueError('Practice run has no submission binding')
    activity = await session.get(LearningActivity, submission.activity_id)
    goal = await session.get(LearningGoal, submission.goal_id)
    if activity.workspace_id != ws.id or goal.workspace_id != ws.id: raise ValueError('Practice scope mismatch')
    submission.status = 'COMPLETED'
    submission.completed_at = datetime.now(timezone.utc)
    exam.status = 'COMPLETED'
    exam.completed_at = submission.completed_at
    exam.summary = {'mode':'PRACTICE','submission_id':submission.id,'mastery_updated':False}
    latest = activity.latest_submission_id == submission.id
    if latest and goal.status == 'ACTIVE' and goal.evidence_status not in BAD_SOURCE:
        passed = bool(steps) and all(s.result_json.get('criterion_scores') and
            all(c['score'] >= 3 for c in s.result_json['criterion_scores']) and not s.result_json.get('critical_errors') for s in steps)
        assisted = any(submission.assistance.get(key) for key in ('hint_used','reference_viewed','feedback_seen','ai_declared','prior_attempt'))
        activity.status = 'COMPLETED' if passed else 'NEEDS_REVISION'
        meta = deepcopy(activity.meta or {})
        meta['understanding'] = ('ASSISTED_CORRECT' if assisted else 'PRACTICE_CORRECT') if passed else 'UNVERIFIED'
        activity.meta = meta
        # A project artifact cannot establish the author's understanding.
        if activity.kind == 'CONCEPT':
            goal.verification = {**goal.verification, 'understanding':meta['understanding']}
        else:
            goal.verification = {**goal.verification, 'practice':'CODE_REVIEWED' if passed else 'NEEDS_REVISION'}
    await emit_event(session, ws.id, 'learning.feedback_completed', {'submission_id':submission.id,
        'activity_id':activity.id,'goal_id':goal.id,'scoring_run_id':run.id,'is_latest':latest,'mastery_updated':False},
        'learning_submission', submission.id)
    if importlib.util.find_spec('app.services.learning_memory'):
        from app.services import learning_memory
        if hasattr(learning_memory, 'on_submission_completed'):
            await learning_memory.on_submission_completed(session, goal, activity, submission, run, steps)
