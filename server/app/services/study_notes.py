from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import or_, select

from app.i18n import Locale, request_language, using_language
from app.i18n import message as localized_message
from app.models.study_chat import StudyMessage, StudySession
from app.models.study_notes import StudyCard, StudyNote, StudyNoteJob, StudyNoteVersion
from app.services.llm import AgentAdapter, get_llm_config
from app.services.model_outbound import OutboundPolicyError
from app.services.note_store import create_note, own_note, own_version, utcnow
from app.services.private_model_billing import BillingScope, BudgetPolicyError
from app.services.study_teaching import teaching_prompt

log = logging.getLogger(__name__)


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message_id: int
    quote: str = Field(min_length=1, max_length=2000)


class NotePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["question", "explanation", "example", "misconception", "boundary", "unresolved"]
    text: str = Field(min_length=1, max_length=5000)
    citations: list[Citation] = Field(min_length=1, max_length=8)


class NoteOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    points: list[NotePoint] = Field(min_length=1, max_length=20)


class CardOutput(BaseModel):
    question: str = Field(min_length=1, max_length=3000)
    answer: str = Field(min_length=1, max_length=5000)
    knowledge_point: str = Field(min_length=1, max_length=180)


class CardBatch(BaseModel):
    cards: list[CardOutput] = Field(min_length=1, max_length=3)


class GenerationError(ValueError):
    pass


async def read_json(system, payload, schema, *, config=None, billing_scope=None):
    cfg = await get_llm_config() if config is None else config
    adapter = AgentAdapter()
    try:
        result = await asyncio.wait_for(adapter.chat_with(
            cfg, system, json.dumps(payload, ensure_ascii=False), json_mode=True,
            timeout=90, stream=True, total_timeout=95, billing_scope=billing_scope,
        ), timeout=100)
        if not result:
            raise GenerationError(localized_message("errors.the_model_returned_incomplete_content_try_again"))
        value = schema.model_validate_json(result["content"])
    except (TimeoutError, KeyError, TypeError, ValidationError) as exc:
        raise GenerationError(localized_message("errors.the_model_timed_out_or_returned_an_incomplete_response")) from exc
    return value, cfg.get("model")


async def own_session(db, workspace_id, session_id, *, lock=False):
    stmt = select(StudySession).where(StudySession.id == session_id, StudySession.workspace_id == workspace_id)
    row = await db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise HTTPException(404, localized_message("errors.study_session_not_found"))
    return row


async def enqueue(db, workspace_id, session_id, request_id, requested_note, focus_message_id=None):
    chat = await own_session(db, workspace_id, session_id, lock=True)
    reused = await db.scalar(select(StudyNoteJob).where(
        StudyNoteJob.workspace_id == workspace_id, StudyNoteJob.session_id == session_id,
        or_(StudyNoteJob.request_id == request_id, StudyNoteJob.request_aliases.contains([request_id])),
    ))
    if reused:
        previous_focus = next((item['id'] for item in reused.snapshot if item.get('study_focus')), None)
        if previous_focus != focus_message_id:
            raise HTTPException(409, localized_message("errors.this_request_id_has_already_been_used_for_another_study_message"))
        if requested_note is not None and reused.note_id != requested_note:
            raise HTTPException(409, localized_message("errors.this_request_id_has_already_been_used_for_another_note"))
        return reused
    if requested_note:
        note = await own_note(db, workspace_id, requested_note, lock=True)
    else:
        note = await db.scalar(select(StudyNote).where(
            StudyNote.origin_session_id == session_id, StudyNote.workspace_id == workspace_id
        ).with_for_update())
    if not note:
        note = await create_note(db, workspace_id, session_id, chat.title)
    base = await own_version(db, note.id, note.current_version_id) if note.current_version_id else None
    session_ids = sorted(set([session_id, *(base.session_ids if base else [])]))
    messages = (await db.scalars(select(StudyMessage).join(StudySession, StudySession.id == StudyMessage.session_id)
        .where(StudySession.workspace_id == workspace_id, StudyMessage.session_id.in_(session_ids),
               StudyMessage.status == "completed")
        .order_by(StudyMessage.id))).all()
    snapshot = [{"id": m.id, "session_id": m.session_id, "role": m.role, "text": m.content,
                 "materials": m.sources or [], **({"blocks": m.coach["blocks"]} if (m.coach or {}).get("blocks") else {}),
                 **({"revision_of": m.coach["revision_of"]} if (m.coach or {}).get("revision_of") else {})} for m in messages]
    if focus_message_id:
        focus = next((item for item in snapshot if item['session_id'] == session_id and item['id'] == focus_message_id), None)
        if not focus:
            raise HTTPException(404, localized_message("errors.the_saved_study_message_could_not_be_found_in"))
        focus['study_focus'] = True
    if not snapshot:
        raise HTTPException(409, localized_message("errors.this_session_has_no_complete_messages_to_organize_yet"))
    if request_language() == "en":
        snapshot[0]["output_language"] = "en"
    if len(json.dumps(snapshot, ensure_ascii=False)) > 150000:
        raise HTTPException(422, localized_message("errors.these_messages_are_too_long_to_organize_together_start"))
    prior = await db.scalar(select(StudyNoteJob).where(
        StudyNoteJob.note_id == note.id, StudyNoteJob.session_id == session_id,
    ).order_by(StudyNoteJob.id.desc()).limit(1).with_for_update())
    if prior and prior.snapshot == snapshot and (
        prior.base_version_id == note.current_version_id or prior.version_id == note.current_version_id
    ):
        prior.request_aliases = [*prior.request_aliases, request_id]
        await db.commit()
        return prior
    job = StudyNoteJob(workspace_id=workspace_id, note_id=note.id, session_id=chat.id,
                       base_version_id=note.current_version_id, request_id=request_id, snapshot=snapshot)
    db.add(job)
    await db.commit()
    return job


SECTIONS = ('question', 'explanation', 'example', 'misconception', 'boundary', 'unresolved')


def note_output_language(snapshot: list[dict]) -> Locale:
    return "en" if snapshot and snapshot[0].get("output_language") == "en" else "zh-CN"


def interrupted_note_error(job: StudyNoteJob) -> str:
    return job.error or localized_message("note.interrupted", note_output_language(job.snapshot))


def render_note(output, snapshot):
    language = note_output_language(snapshot)
    messages = {item["id"]: item for item in snapshot}
    sections = {kind: [] for kind in SECTIONS}
    sources = []
    for point in output.points:
        refs = []
        for cite in point.citations:
            msg = messages.get(cite.message_id)
            # 引用须逐字存在于原消息中；仅容忍空白差异(换行/连续空格)。
            folded_quote = " ".join(cite.quote.split())
            folded_text = " ".join(msg["text"].split()) if msg else ""
            if not msg or not cite.quote.strip() or folded_quote not in folded_text:
                raise GenerationError(localized_message("errors.the_citation_differs_from_the_original_message_try_organizing"))
            source = {"session_id": msg["session_id"], "message_id": msg["id"], "role": msg["role"],
                      "quote": cite.quote, "materials": msg["materials"]}
            if source not in sources:
                sources.append(source)
            refs.append(localized_message("note.citation", language).format(id=msg["id"]))
        sections[point.kind].append(point.text.strip() + " " + " ".join(dict.fromkeys(refs)))
    body = "\n\n".join("## " + localized_message(f"note.section.{kind}", language) + "\n" + "\n".join(points)
                        for kind, points in sections.items() if points)
    return body, sources


async def generate_note(snapshot, *, billing_scope=None):
    cfg = await get_llm_config()
    cfg = {**cfg, "output_limit": 8000}
    language = note_output_language(snapshot)
    output, model = await read_json(
        teaching_prompt("notes", language) + "\n" + localized_message("note.schema", language),
        snapshot, NoteOutput, config=cfg, billing_scope=billing_scope
    )
    body, sources = render_note(output, snapshot)
    return {"title": output.title, "body": body, "sources": sources, "model": model}


async def finish_job(db, job_id, generation, result=None, error=None):
    # Lock the note before the job: enqueue takes the same lock order.
    note_id = await db.scalar(select(StudyNoteJob.note_id).where(StudyNoteJob.id == job_id))
    if not note_id:
        return False
    note = await db.scalar(select(StudyNote).where(StudyNote.id == note_id).with_for_update())
    job = await db.scalar(select(StudyNoteJob).where(StudyNoteJob.id == job_id).with_for_update())
    if not job or job.status != "RUNNING" or job.generation != generation or not job.lease_until or job.lease_until <= utcnow():
        return False
    job.lease_until = None
    if error:
        job.status, job.error = "FAILED", error
    else:
        base = await db.get(StudyNoteVersion, job.base_version_id) if job.base_version_id else None
        conflict = note.current_version_id != job.base_version_id or bool(base and base.author == "USER")
        version = StudyNoteVersion(note_id=note.id, base_version_id=job.base_version_id, author="MODEL",
                                   conflict=conflict, coverage=[m["id"] for m in job.snapshot],
                                   session_ids=sorted({m["session_id"] for m in job.snapshot}), **result)
        db.add(version)
        await db.flush()
        job.status, job.version_id, job.conflict = "SUCCEEDED", version.id, conflict
        if not conflict:
            note.title, note.current_version_id, note.updated_at = version.title, version.id, utcnow()
    await db.commit()
    return True


async def process_next_note_job(factory):
    async with factory() as db:
        from app.services.recovery_control import require_normal
        await require_normal(db)
        job = await db.scalar(select(StudyNoteJob).where(or_(
            StudyNoteJob.status == "QUEUED",
            (StudyNoteJob.status == "RUNNING") & (StudyNoteJob.lease_until < utcnow()),
        )).order_by(StudyNoteJob.id).limit(1).with_for_update(skip_locked=True))
        if not job:
            return False
        if job.status == "RUNNING":
            job.status, job.error, job.lease_until = "FAILED", interrupted_note_error(job), None
            await db.commit()
            return True
        job.status, job.generation = "RUNNING", job.generation + 1
        job.lease_until = utcnow() + timedelta(seconds=120)
        job_id, generation, snapshot = job.id, job.generation, job.snapshot
        billing_scope = BillingScope(job.workspace_id, f"note_job:{job.id}")
        await db.commit()
    error, result = None, None
    language = note_output_language(snapshot)
    try:
        with using_language(language):
            result = await generate_note(snapshot, billing_scope=billing_scope)
    except (BudgetPolicyError, OutboundPolicyError) as exc:
        error = str(exc)
    except GenerationError as exc:
        error = str(exc)
    except Exception:
        log.exception("note generation failed, job=%s", job_id)
        error = localized_message("note.failed", language)
    async with factory() as db:
        await finish_job(db, job_id, generation, result, error)
    return True


async def generate_cards(body, *, billing_scope=None):
    prompt = (
        localized_message("note.cards") + "\n" + localized_message("language")
    )
    cfg = await get_llm_config()
    cfg = {**cfg, "output_limit": 4000}
    result, _model = await read_json(prompt, body, CardBatch, config=cfg, billing_scope=billing_scope)
    return result.cards


async def cards_for_version(db, version_id):
    return list((await db.scalars(select(StudyCard).where(StudyCard.version_id == version_id, StudyCard.status != "DELETED")
                                              .order_by(StudyCard.id))).all())
