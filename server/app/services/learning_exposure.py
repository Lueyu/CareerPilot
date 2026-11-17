"""Known answer exposure and question families; never claims to observe outside AI use."""
from datetime import datetime, timezone
from sqlalchemy import select
from app.models import Event, Exam, ExamQuestion, Question, Workspace
from app.services.tracing import emit_event

EVENT_KIND = 'learning.reference_exposed'


async def _families(session, workspace_id):
    rows = (await session.execute(select(Question.id, Question.canonical_question_id).where(
        Question.workspace_id.is_(None) | (Question.workspace_id == workspace_id)))).all()
    parents = {row.id:row.canonical_question_id for row in rows}
    def root(qid):
        seen = set()
        while parents.get(qid) and qid not in seen:
            seen.add(qid)
            qid = parents[qid]
        return qid
    return {qid:root(qid) for qid in parents}


async def exposed_families(session, workspace_id):
    families = await _families(session, workspace_id)
    events = (await session.scalars(select(Event).where(Event.workspace_id == workspace_id,
        Event.kind == EVENT_KIND))).all()
    return {families.get(qid, qid) for event in events for qid in
        ((event.payload or {}).get('question_id'), (event.payload or {}).get('canonical_question_id')) if qid is not None}


async def record_reference_exposure(session, ws, question, *, source):
    await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
    if question.workspace_id not in (None, ws.id): raise ValueError('Reference scope mismatch')
    families = await _families(session, ws.id)
    canonical = families.get(question.id, question.canonical_question_id or question.id)
    existing = await session.scalar(select(Event).where(Event.workspace_id == ws.id,
        Event.kind == EVENT_KIND, Event.payload['question_id'].as_integer() == question.id).limit(1))
    if existing: return existing
    return await emit_event(session, ws.id, EVENT_KIND, {'question_id':question.id,
        'canonical_question_id':canonical, 'source':source, 'material_returned':True,
        'scope':'reference_or_rubric_material_delivered_not_proof_of_reading'}, 'question', question.id)


async def independence_snapshot(session, workspace_id, exam, eq, frozen):
    families = await _families(session, workspace_id)
    family = families.get(eq.question_id, frozen.get('canonical_question_id') or eq.question_id)
    members = [qid for qid, root in families.items() if root == family]
    events = (await session.scalars(select(Event).where(Event.workspace_id == workspace_id,
        Event.kind == EVENT_KIND))).all()
    exposures = [event.id for event in events if any(families.get(qid, qid) == family
        for qid in ((event.payload or {}).get('question_id'), (event.payload or {}).get('canonical_question_id')))]
    prior = await session.scalar(select(ExamQuestion.id).join(Exam).where(Exam.workspace_id == workspace_id,
        Exam.id != exam.id, ExamQuestion.question_id.in_(members)).limit(1))
    assistance = dict(frozen.get('assistance') or {})
    assistance['reference_viewed'] = bool(exposures) or bool(assistance.get('reference_viewed'))
    assistance['prior_attempt'] = prior is not None or bool(assistance.get('prior_attempt'))
    assisted = any(assistance.get(k) for k in ('reference_viewed','prior_attempt','hint_used','feedback_seen','ai_declared'))
    independent = not assisted and exam.kind != 'practice'
    return {'eligible_for_independent_verification':independent, 'canonical_question_id':family,
        'exposure_refs':exposures, 'prior_exam_question_id':prior,
        'checked_at':datetime.now(timezone.utc).isoformat(),
        'reason':'没有已登记的答案曝光或同族既有试卷；外部辅助情况仍依赖用户声明' if independent
            else '练习、已有同族试卷或已登记辅助条件，不属于未辅助独立验证'}, assistance
