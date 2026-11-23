"""Grounded one-question exercises using CareerPilot's practice and official assessment paths."""
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, update

from app.models import (
    CompetencySnapshot,
    Event,
    Evidence,
    Exam,
    ExamQuestion,
    Question,
    ScoringRun,
    Skill,
    SkillState,
    Workspace,
)
from app.models.learning_feedback import LearningActivity, LearningGoal, LearningSubmission
from app.models.study_chat import StudyMessage, StudySession
from app.models.study_notes import StudyCard, StudyCardEvent, StudyNote, StudyNoteVersion
from app.models.study_practice import StudyPractice
from app.services import learning_feedback as learning
from app.services.assessment_versions import digest
from app.services.benchmark_jd import SKILL_KEYWORDS
from app.services.competency import assessed_observations
from app.services.study_teaching import practice_prompt


def _topic_text(value):
    text = value.casefold()
    for alias, term in (('precision', '精确率'), ('查准率', '精确率'),
                        ('recall', '召回'), ('查全率', '召回'), ('reranking', '重排'),
                        ('rerank', '重排'), ('kubernetes', 'k8s')):
        text = text.replace(alias, term)
    return text


def _has_skill_term(text, term):
    normalized = _topic_text(term)
    if normalized.isascii():
        return re.search(r'(?<![a-z0-9])' + re.escape(normalized) + r'(?![a-z0-9])', text) is not None
    return normalized in text


def matching_questions(pool, skill_key, topic):
    """Rank available scenarios by concrete shared bank terms; generic domain tags have lower precision."""
    eligible = [q for q in pool if q.skill and q.skill.key == skill_key and
                (q.generation_meta or {}).get('assessment_use') != 'PRACTICE_ONLY']
    topic = _topic_text(topic)
    retrieval_metrics = {'精确率', '召回'}
    metric_terms = retrieval_metrics if skill_key == 'rag' and all(term in topic for term in retrieval_metrics) else set()
    vocabulary = set(SKILL_KEYWORDS.get(skill_key, [])) | metric_terms
    for q in eligible:
        vocabulary.update(_topic_text(str(tag)) for tag in q.tags or [])
        vocabulary.update(re.findall(r'[\w+#.-]+', _topic_text(str(q.keywords or ''))))
    vocabulary.update(re.findall(r'[a-z][a-z0-9+#.-]{2,}', topic))
    terms = {t for t in vocabulary if len(t) >= 2 and t in topic}
    domain_labels = {skill_key, *(q.skill.name.casefold() for q in eligible)}
    terms -= {'the', 'and', 'what', 'how', 'why', '系统设计', '面试', '案例', '解释', '机制'}
    terms -= domain_labels
    if not terms and (not topic.strip() or topic.strip() in domain_labels):
        return {}
    scores = {}
    for q in eligible:
        material = _topic_text(' '.join([q.title, *map(str, q.tags or []), str(q.keywords or '')]))
        if not all(term in material for term in metric_terms):
            continue
        title = _topic_text(q.title)
        score = sum(len(term) for term in terms if term in material)
        score += (1 + sum(map(len, terms))) * sum(term in title for term in metric_terms)
        if topic in material or score:
            scores[q.id] = score
    return scores


async def owned_session(session, workspace_id, session_id):
    row = await session.scalar(select(StudySession).where(
        StudySession.workspace_id == workspace_id, StudySession.id == session_id))
    if row is None:
        raise HTTPException(404, '学习会话不存在')
    return row


async def owned_practice(session, workspace_id, practice_id):
    row = await session.scalar(select(StudyPractice).where(
        StudyPractice.workspace_id == workspace_id, StudyPractice.id == practice_id)
        .execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, '学习练习不存在')
    return row


async def _material(session, chat, card_id, focus_message_id=None):
    context = chat.context or {}
    origin = {'study_session_id': chat.id}
    if context.get('question_id'):
        origin['question_id'] = context['question_id']
    if card_id is not None:
        result = (await session.execute(select(StudyCard, StudyNoteVersion)
            .join(StudyNoteVersion, StudyNoteVersion.id == StudyCard.version_id)
            .join(StudyNote, StudyNote.id == StudyNoteVersion.note_id)
            .where(StudyCard.workspace_id == chat.workspace_id, StudyNote.workspace_id == chat.workspace_id,
                   StudyCard.id == card_id, StudyCard.status != 'DELETED', StudyCard.note_id == StudyNote.id))).first()
        if not result or chat.id not in result[1].session_ids:
            raise HTTPException(404, '这张卡片不属于本次学习会话')
        card, version = result
        origin.update(card_id=card.id, note_id=card.note_id, note_version_id=version.id,
                      card_content_version=digest([card.question, card.answer, card.knowledge_point]),
                      retest_topic=f'{card.question}\n{card.knowledge_point}',
                      _note_skill_context=(version.title, version.body))
        return card.question, card.answer, card.knowledge_point, origin
    query = select(StudyMessage).where(StudyMessage.session_id == chat.id, StudyMessage.role == 'assistant', StudyMessage.status == 'completed')
    if focus_message_id:
        focus = await session.scalar(select(StudyMessage).where(StudyMessage.id == focus_message_id, StudyMessage.session_id == chat.id, StudyMessage.status == 'completed'))
        if focus is None:
            raise HTTPException(404, '会话中找不到这条已保存的学习消息')
        query = query.where(StudyMessage.request_id == focus.request_id)
    reply = await session.scalar(query.order_by(StudyMessage.id.desc()).limit(1))
    if reply is None or not reply.content.strip():
        raise HTTPException(409, '请先围绕当前问题获得一条完整讲解，再开始练习')
    source = await session.scalar(select(StudyMessage).where(StudyMessage.session_id == chat.id,
        StudyMessage.request_id == reply.request_id, StudyMessage.role == 'user'))
    topic = context.get('topic') or chat.title
    origin.update(message_id=reply.id, user_message_id=source.id if source else None)
    prompt = source.content if source and source.action == 'explain' and source.content.strip() else topic
    if focus_message_id:
        origin['focus_message_id'] = focus_message_id
        topic = prompt
    return prompt[:600], reply.content, topic[:180], origin


async def _resolve_skill(session, context, point, requested, *, card_evidence=(), note_evidence=()):
    anchored = set(context.get('skill_ids') or [])
    skills = (await session.scalars(select(Skill))).all()
    def matches(value, *, named_only=False, card=False):
        text = _topic_text(value)
        return {s.id for s in skills if any((_has_skill_term(text, term) if card else _topic_text(term) in text)
            for term in {s.name, s.key, *([] if named_only else SKILL_KEYWORDS.get(s.key, []))}
            if term and (not card or term not in {'评估', '评测', '标注'}))}
    point_ids = matches(point)
    if card_evidence:
        question, reference = card_evidence
        card_text = '\n'.join((question, point, reference))
        note_text = '\n'.join(note_evidence)
        point_ids = (matches(question, named_only=True, card=True) or matches(card_text, named_only=True, card=True)
            or matches(question, card=True) or matches(card_text, card=True)
            or matches(note_text, named_only=True, card=True) or matches(note_text, card=True))
    if requested:
        skill = next((s for s in skills if s.key == requested), None)
        if (skill is None or (anchored and skill.id not in anchored) or
                (point_ids and skill.id not in point_ids)):
            raise HTTPException(422, '所选能力域与学习来源不一致')
        return skill
    topic_ids = matches(context.get('topic') or '') if not card_evidence else set()
    eligible = set(anchored or point_ids or topic_ids)
    if point_ids:
        eligible &= point_ids
    if len(eligible) != 1:
        if eligible & topic_ids:
            eligible &= topic_ids
    return next((s for s in skills if s.id in eligible), None) if len(eligible) == 1 else None


async def create_practice(session, ws, session_id, body):
    await learning._lock_workspace(session, ws)
    chat = await owned_session(session, ws.id, session_id)
    payload = body.model_dump(exclude={'request_key'})
    if not payload.get('focus_message_id'):
        payload.pop('focus_message_id', None)  # Keep persisted hashes compatible.
    request_hash = digest(payload)
    previous = await session.scalar(select(StudyPractice).where(
        StudyPractice.session_id == chat.id, StudyPractice.request_key == body.request_key))
    if previous:
        if previous.request_hash != request_hash:
            raise HTTPException(409, '请求标识已用于不同练习')
        return previous
    question, reference, point, origin = await _material(session, chat, body.card_id, getattr(body, 'focus_message_id', None))
    note_evidence = origin.pop('_note_skill_context', ())
    skill = await _resolve_skill(session, chat.context, point, body.skill_key,
        card_evidence=(question, reference) if body.card_id is not None else (), note_evidence=note_evidence)
    point = point[:180]
    prompt = practice_prompt(body.kind, question, from_card=body.card_id is not None)
    goal = LearningGoal(workspace_id=ws.id, origin_key=digest([chat.id, body.request_key]),
        origin_refs=origin, skill_ids=[skill.id] if skill else [], gap_type='StudyTopic',
        target_behavior=point, evidence_status='UNVERIFIED', verification=dict(learning.VERIFICATION),
        success_criteria={'title': point}, success_criteria_version=digest([point, reference]))
    session.add(goal)
    await session.flush()
    requirement = '结合题目说明关键机制和适用条件，按原话核对正确点、遗漏及错误。'
    spec = {'title': prompt, 'answer_html': reference, 'skill_id': skill.id if skill else None,
            'rubric': {'max_points': 5, 'points': [{'title': point, 'detail': requirement +
                ' 本题学习材料的评价要点：' + reference[:1200]}]}}
    snapshot = {'assessment_question': spec, 'prompt': prompt, 'study_source': origin,
        'study_hint': f'围绕「{point}」，先想一个真实场景：谁提供输入，哪个环节决定结果？先说你有把握的一步。'}
    activity = await learning.create_activity(session, ws, goal, 'CONCEPT', point, snapshot)
    row = StudyPractice(id=activity.id, workspace_id=ws.id, session_id=chat.id, card_id=body.card_id,
        skill_id=skill.id if skill else None, kind=body.kind, knowledge_point=point,
        request_key=body.request_key, request_hash=request_hash, source=origin)
    session.add(row)
    await session.commit()
    return row


async def sync_card_help(session, workspace_id, activity):
    row = await owned_practice(session, workspace_id, activity.id)
    if row.card_id is None:
        return
    event = await session.scalar(select(StudyCardEvent).join(StudyCard).where(
        StudyCard.workspace_id == workspace_id, StudyCard.id == row.card_id,
        StudyCardEvent.kind.in_(('REVEAL', 'EDIT_ANSWER'))).order_by(StudyCardEvent.id).limit(1))
    if event:
        activity.meta = {**(activity.meta or {}), 'reference_viewed': True,
                         'card_exposure_id': event.id}


async def mark_chat_help(session, workspace_id, session_id, *, question_text=''):
    """Call in the same transaction before releasing a new tutor explanation."""
    ws = await session.get(Workspace, workspace_id)
    await learning._lock_workspace(session, ws)
    chat = await owned_session(session, workspace_id, session_id)
    ids = select(StudyPractice.id).where(StudyPractice.workspace_id == workspace_id, StudyPractice.session_id == session_id)
    await session.execute(update(LearningActivity).where(LearningActivity.id.in_(ids),
        LearningActivity.status != 'SKIPPED', LearningActivity.meta['chat_hint_at'].as_string().is_(None))
        .values(meta=LearningActivity.meta.op('||')({'hint_used': True, 'chat_hint_at': datetime.now(timezone.utc).isoformat()}))
        .execution_options(synchronize_session=False))
    opened = (await session.execute(select(Exam.id, StudyPractice.session_id, Skill,
        ExamQuestion.creation_snapshot['short_code'].as_string(), ExamQuestion.creation_snapshot['title'].as_string())
        .join(ExamQuestion, ExamQuestion.exam_id == Exam.id)
        .join(StudyPractice, StudyPractice.retest_exam_id == Exam.id).join(Skill, Skill.id == StudyPractice.skill_id)
        .where(Exam.workspace_id == workspace_id, Exam.status == 'ACTIVE'))).all()
    for exam_id, origin_session_id, skill, short_code, title in opened:
        if (origin_session_id == session_id or tutoring_may_help(chat.context, skill, question_text) or
                short_code.casefold() in question_text.casefold() or title.casefold() in question_text.casefold()):
            await mark_active_retest_help(session, ws, exam_id)
    if question_text.strip():
        from app.services.learning_exposure import record_reference_exposure
        matches = (await session.scalars(select(Question).where(
            Question.workspace_id.is_(None) | (Question.workspace_id == workspace_id),
            (Question.title == question_text.strip()) | (Question.short_code == question_text.strip())
        ))).all()
        for question in matches:
            await record_reference_exposure(session, ws, question, source='study_chat_named_question')


def tutoring_may_help(context, skill, text):
    # An unanchored conversation may paraphrase the open paper. Only a separate
    # grounded subject with no matching skill indicators can establish separation.
    anchored = (context or {}).get('skill_ids') or []
    if not anchored or skill.id in anchored or context.get('source_type', 'free') == 'free':
        return True
    return any(term in text.casefold() for term in {skill.key, *SKILL_KEYWORDS.get(skill.key, [])})


async def assert_retest_ready(session, row, skill):
    if await session.scalar(select(LearningActivity.status).where(LearningActivity.id == row.id)) == 'SKIPPED':
        raise HTTPException(409, '本次练习已跳过，请开始新的练习后再复测')
    pending = (await session.execute(select(StudySession.id, StudySession.context)
        .join(StudyMessage).where(StudySession.workspace_id == row.workspace_id,
            StudyMessage.status == 'generating', StudyMessage.expires_at > func.now()))).all()
    if any(sid == row.session_id or tutoring_may_help(context, skill, '') for sid, context in pending):
        raise HTTPException(409, '请等待相关讲解结束或中断回复，再开始独立复测')


async def mark_active_retest_help(session, ws, exam_id):
    from app.services.tracing import emit_event
    active = await session.scalar(select(Exam.id).where(
        Exam.id == exam_id, Exam.workspace_id == ws.id, Exam.status == 'ACTIVE'))
    if active and not await retest_help_event(session, ws.id, exam_id):
        await emit_event(session, ws.id, 'study.retest_help_requested', {'exam_id': exam_id},
                         'exam', exam_id)


async def retest_help_event(session, workspace_id, exam_id):
    return await session.scalar(select(Event.id).where(Event.workspace_id == workspace_id,
        Event.kind == 'study.retest_help_requested', Event.payload['exam_id'].as_integer() == exam_id).limit(1))


async def retest_assistance(session, workspace_id, exam, frozen):
    row = await owned_practice(session, workspace_id, frozen['study_practice_id'])
    flags = {}
    chat_event = await retest_help_event(session, workspace_id, exam.id)
    if chat_event:
        flags.update(hint_used=True, study_help_event_id=chat_event)
    if row.card_id:
        reveal = await session.scalar(select(StudyCardEvent.id).where(
            StudyCardEvent.card_id == row.card_id, StudyCardEvent.kind.in_(('REVEAL', 'EDIT_ANSWER')),
            StudyCardEvent.id > frozen.get('study_card_event_through', 0)).order_by(StudyCardEvent.id).limit(1))
        if reveal:
            flags.update(reference_viewed=True, card_event_id=reveal)
    return flags


async def practice_view(session, ws, row, *, mark_seen=False):
    activity = await session.get(LearningActivity, row.id)
    skill = await session.get(Skill, row.skill_id) if row.skill_id else None
    ids = (await session.scalars(select(LearningSubmission.id).where(
        LearningSubmission.workspace_id == ws.id, LearningSubmission.activity_id == row.id)
        .order_by(LearningSubmission.revision))).all()
    submissions = [await learning.submission_view(session, ws, ix) for ix in ids]
    if mark_seen and any(s['has_feedback'] for s in submissions) and not (activity.meta or {}).get('feedback_seen'):
        await learning._lock_workspace(session, ws)
        await session.refresh(activity)
        activity.meta = {**activity.meta, 'feedback_seen': True}
        await session.commit()
    return {'id': row.id, 'session_id': row.session_id, 'card_id': row.card_id, 'kind': row.kind,
        'question': activity.snapshot['prompt'], 'skill_key': skill.key if skill else None,
        'skill_name': skill.name if skill else None, 'knowledge_point': row.knowledge_point,
        'status': activity.status, 'submissions': submissions, 'retest_exam_id': row.retest_exam_id,
        'assistance': learning._assistance(activity), 'source': row.source,
        'question_version': activity.snapshot['assessment_question']['question_version']}


async def skill_snapshot(session, workspace_id, skill_id):
    row = await session.scalar(select(SkillState).where(
        SkillState.workspace_id == workspace_id, SkillState.skill_id == skill_id))
    if row is None:
        return {'status': 'UNKNOWN', 'evidence_coverage': 0, 'estimated_level': None}
    return {key: getattr(row, key) for key in ('status', 'estimated_level', 'evidence_coverage', 'model_version')}


async def taught_question_ids(session, row):
    """Exclude bank prompts already used in this lesson, including unlinked pasted questions."""
    activity = await session.get(LearningActivity, row.id)
    private = activity.snapshot['assessment_question']
    lesson = (private['title'] + '\n' + private['answer_html']).casefold()
    title = func.lower(Question.title)
    discussed = select(StudyMessage.id).where(StudyMessage.session_id == row.session_id,
        StudyMessage.content != '', func.strpos(func.lower(StudyMessage.content), title) > 0).exists()
    return list(await session.scalars(select(Question.id).where(
        Question.workspace_id.is_(None) | (Question.workspace_id == row.workspace_id),
        Question.skill_id == row.skill_id, func.length(Question.title) >= 4,
        discussed | (func.strpos(lesson, title) > 0))))


async def resolve_retest_skill(session, row):
    skill = await session.get(Skill, row.skill_id) if row.skill_id else None
    if row.card_id is None or (skill and row.source.get('retest_topic')):
        return skill
    activity = await session.get(LearningActivity, row.id)
    if not activity or activity.workspace_id != row.workspace_id:
        return skill
    snapshot = activity.snapshot
    origin = snapshot.get('study_source') or {}
    if origin.get('card_id') != row.card_id or origin.get('study_session_id') != row.session_id:
        return skill
    version = await session.scalar(select(StudyNoteVersion)
        .join(StudyNote, StudyNote.id == StudyNoteVersion.note_id)
        .where(StudyNote.workspace_id == row.workspace_id, StudyNote.id == origin.get('note_id'),
               StudyNoteVersion.id == origin.get('note_version_id')))
    if not version or row.session_id not in version.session_ids:
        return skill
    prompt = snapshot.get('prompt') or ''
    if not prompt:
        return skill
    if not skill:
        reference = (snapshot.get('assessment_question') or {}).get('answer_html') or ''
        skill = await _resolve_skill(session, {}, row.knowledge_point, None, card_evidence=(prompt, reference),
            note_evidence=(version.title, version.body))
    if skill:
        row.skill_id = skill.id
        row.source = {**row.source, 'retest_topic': f'{prompt}\n{row.knowledge_point}'}
    return skill


async def card_event_through(session, practice):
    if practice.card_id is None:
        return 0
    return await session.scalar(select(func.max(StudyCardEvent.id)).where(
        StudyCardEvent.card_id == practice.card_id)) or 0


async def retest_result(session, ws, row):
    from app.api.v1.exams import _get_own_exam, _maybe_expire
    if row.retest_exam_id is None:
        raise HTTPException(404, '此练习还没有新情境复测')
    exam = await _get_own_exam(session, ws, row.retest_exam_id, lock=True)
    if _maybe_expire(exam):
        await session.commit()
    observed = (await session.scalars(select(Evidence).where(Evidence.workspace_id == ws.id,
        Evidence.skill_id == row.skill_id, Evidence.meta['exam_id'].as_integer() == exam.id))).all()
    skill = await session.get(Skill, row.skill_id)
    run = await session.get(ScoringRun, exam.active_scoring_run_id) if exam.active_scoring_run_id else None
    snapshot = await session.scalar(select(CompetencySnapshot).where(
        CompetencySnapshot.workspace_id == ws.id, CompetencySnapshot.trigger_ref == f'scoring:{run.id}'
    ).order_by(CompetencySnapshot.id.desc()).limit(1)) if run else None
    after = next((s for s in snapshot.skills if s['skill_id'] == row.skill_id), None) if snapshot else None
    return {'exam_id': exam.id, 'status': exam.status, 'score': exam.score, 'skill_key': skill.key,
            'closed_retests': row.source.get('closed_retests', []),
            'skill_state': await skill_snapshot(session, ws.id, row.skill_id),
            'skill_state_before': row.source.get('skill_state_before'),
            'skill_state_after': after, 'competency_snapshot_id': snapshot.id if snapshot else None,
            'evidence_ids': [ev.id for ev in assessed_observations(observed)],
            'observation_ids': [ev.id for ev in observed],
            'scoring_run_id': run.id if run else None,
            'model_fingerprint': run.model_fingerprint if run else None,
            'scoring_route': (run.meta or {}).get('scoring_route','default') if run else None,
            'requested_model': (run.meta or {}).get('model') if run else None,
            'report_url': f'/api/v1/exams/{exam.id}/report'}
