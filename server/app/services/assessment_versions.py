"""Freeze question meaning at exam creation and append immutable answer revisions."""
import hashlib
import json
import math
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from app.models import AnswerRevision, ExamAnswer


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def mcq_rubric(raw,options):
    from app.services.assessment_rubric import AssessmentJudgeError
    raw=deepcopy(raw or {});index=raw.get('answer_index');maximum=raw.get('max_points',5)
    if (not isinstance(options,list) or len(options)<2 or len(options)>50
            or type(index) is not int or not 0<=index<len(options)
            or type(maximum) not in (int,float) or not math.isfinite(maximum) or not 0<maximum<=1000):
        raise AssessmentJudgeError('INVALID_RUBRIC','选择题的正确选项或满分不合法，需要复核评分标准')
    value={**raw,'answer_index':index,'max_points':float(maximum),'rule_schema_version':'mcq-v1'}
    prior=value.pop('rubric_version',None)
    value['rubric_version']='mcq-v1:'+digest(value)[:32]
    if prior is not None and prior!=value['rubric_version']:
        raise AssessmentJudgeError('INVALID_RUBRIC','选择题评分标准与冻结版本不一致')
    return value


def question_snapshot(q, standard=None):
    from app.services.assessment_rubric import AssessmentJudgeError, compile_rubric
    material={"question_id":q.id,"canonical_question_id":q.canonical_question_id or q.id,
        "short_code":q.short_code,"title":q.title,"qtype":q.qtype,"options":deepcopy(q.options),
        "skill_id":q.skill_id,"skill_key":q.skill.key if q.skill else None,
        "skill_name":q.skill.name if q.skill else None,"difficulty":q.difficulty,
        "answer_html":q.answer_html,"role_level":q.level or '目标层级尚未确认',
        "job_standard":deepcopy(standard or {}),
        "project_provenance":deepcopy({'generation_id':(q.generation_meta or {}).get('task_id'),
            'issue_targets':((q.generation_meta or {}).get('input_snapshot') or {}).get('project_issue_targets',[]),
            'fact_refs':((q.generation_meta or {}).get('item') or {}).get('fact_refs',[])})}
    raw=deepcopy(q.rubric or {})
    if raw.get('review_required'):
        raise HTTPException(409,'题意或能力映射已修改，评分标准需要复核后才能创建新考试')
    try:
        material['rubric']=compile_rubric(raw,question_id=q.id,title=q.title,
            skill_ids=[q.skill_id] if q.skill_id else [],role_level=material['role_level'],
            reference_answer=q.answer_html) if q.qtype=='open' else mcq_rubric(raw,q.options) if q.qtype=='mcq' else raw
    except AssessmentJudgeError as exc:
        raise HTTPException(409,{'code':'RUBRIC_REVIEW_REQUIRED','message':str(exc)}) from None
    material['question_version']=digest(material)
    return material


def answer_payload(answer):
    return {key:getattr(answer,key,None) for key in ('choice_index','text_answer','transcript')}


async def ensure_revision(session,eq):
    if eq.answer is None:
        eq.answer=ExamAnswer(exam_question_id=eq.id,revision=0)
        session.add(eq.answer);await session.flush()
    answer=eq.answer
    row=await session.scalar(select(AnswerRevision).where(AnswerRevision.exam_question_id==eq.id,
        AnswerRevision.revision==answer.revision))
    payload=answer_payload(answer)
    if row is None:
        row=AnswerRevision(exam_question_id=eq.id,revision=answer.revision,payload=payload,content_hash=digest(payload))
        session.add(row);await session.flush()
    elif row.content_hash!=digest(payload):
        raise HTTPException(409,'答卷版本与保存内容不一致，请保留草稿并复核')
    return row


async def save_revision(session,eq,req):
    current=eq.answer
    number=current.revision if current else 0
    payload={key:getattr(req,key,None) for key in ('choice_index','text_answer','transcript')}
    snapshot=eq.creation_snapshot or eq.frozen_snapshot or {}
    qtype=snapshot.get('qtype',eq.question.qtype)
    options=snapshot.get('options',eq.question.options) or []
    if payload['choice_index'] is not None and (qtype!='mcq' or not 0<=payload['choice_index']<len(options)):
        raise HTTPException(422,'选项不属于本题')
    expected=getattr(req,'expected_revision',None)
    if current and answer_payload(current)==payload:
        return current
    if expected is not None and expected!=number or current is not None and expected is None and number>0:
        raise HTTPException(409,{'code':'ANSWER_REVISION_CONFLICT','message':'该答案已在别处更新，保留当前编辑后选择版本。','server_revision':number})
    if current is None:
        current=ExamAnswer(exam_question_id=eq.id,revision=0,max_points=(snapshot.get('rubric') or eq.question.rubric or {}).get('max_points',5))
        session.add(current);eq.answer=current
    else:
        await ensure_revision(session,eq)
    for key,value in payload.items():setattr(current,key,value)
    current.revision=number+1
    session.add(AnswerRevision(exam_question_id=eq.id,revision=current.revision,payload=payload,content_hash=digest(payload)))
    await session.flush()
    return current
