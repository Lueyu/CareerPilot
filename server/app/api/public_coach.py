"""Workspace-scoped coach routes for the public FastAPI app."""

from collections import defaultdict
from html import escape
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from app.i18n import message as localized_message
from app.api.public_auth import current_workspace
from app.db import get_session
from app.i18n import message
from app.models.public_coach import CoachCandidate, CoachThread, CoachTurn
from app.models.question import SOURCE_GENERATED, Question
from app.models.study_chat import StudySession
from app.models.study_notes import StudyNote, StudyNoteVersion
from app.models.workspace import Workspace
from app.services import note_store as notebook
from app.services.public_coach import (
    STUDY_ACTIONS,
    TURN_LEASE,
    Mode,
    ThreadInput,
    TurnInput,
    admit_turn,
    effective_status,
    interview_actions,
    now,
    run_turn,
    thread_candidates,
    thread_turns,
    turn_events,
    turn_view,
)

router = APIRouter(prefix="/coach", tags=["public-coach"])
Database = Annotated[AsyncSession, Depends(get_session)]
MemberWorkspace = Annotated[Workspace, Depends(current_workspace)]


class RetryInput(BaseModel):
    attempt: int = Field(ge=1)


def _thread_view(row):
    return {
        "id": row.id,
        "mode": row.mode,
        "role": row.role,
        "direction": row.direction,
        "question_id": row.question_id,
        "question_title": row.question_title,
        "title": row.title,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _candidate_view(row):
    return {
        "id": row.id,
        "turn_id": row.turn_id,
        "number": row.number,
        "title": row.title,
        "reference": row.reference,
        "superseded": row.superseded,
        "question_id": row.question_id,
        "origin": "model_candidate",
    }


def _detail(row, turns, candidates):
    latest = turns[-1] if turns else None
    actions = STUDY_ACTIONS if row.mode == "study" else interview_actions(turns)
    return {
        **_thread_view(row),
        "turns": [turn_view(turn) for turn in turns],
        "candidates": [_candidate_view(candidate) for candidate in candidates],
        "actions": [] if latest and effective_status(latest) == "generating" else list(actions),
        "ability_status": "unknown" if row.mode == "interview" else None,
    }


def _threads(workspace_id):
    return select(CoachThread).where(CoachThread.workspace_id == workspace_id)


async def _thread(db, workspace_id, thread_id, *, lock=False):
    stmt = _threads(workspace_id).where(CoachThread.id == thread_id)
    thread = await db.scalar(stmt.with_for_update() if lock else stmt)
    if thread is None:
        raise HTTPException(404, localized_message("errors.conversation_not_found"))
    return thread


@router.get("/threads")
async def list_threads(
    db: Database,
    ws: MemberWorkspace,
    mode: Mode,
    question_id: int | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    stmt = _threads(ws.id).where(CoachThread.mode == mode)
    if question_id is not None:
        stmt = stmt.where(CoachThread.question_id == question_id)
    rows = await db.scalars(
        stmt.order_by(CoachThread.updated_at.desc(), CoachThread.id.desc()).offset(offset).limit(31)
    )
    page = list(rows)
    return {
        "threads": [_thread_view(row) for row in page[:30]],
        "next_offset": offset + 30 if len(page) > 30 else None,
    }


@router.post("/threads", status_code=201)
async def create_thread(body: ThreadInput, db: Database, ws: MemberWorkspace):
    question = None
    if body.question_id is not None:
        question = await db.scalar(
            select(Question)
            .options(lazyload("*"))
            .where(
                Question.id == body.question_id,
                Question.workspace_id == ws.id,
                Question.is_demo.is_(False),
            )
        )
        if question is None:
            raise HTTPException(404, localized_message("errors.question_not_found"))
    if body.mode == "interview" and question is None:
        raise HTTPException(422, localized_message("errors.choose_an_interview_question_from_the_question_bank_first"))
    role, direction = ws.target_role, body.direction
    if question is not None:
        meta = question.generation_meta or {}
        role = meta.get("target_role") or question.role or role
        direction = meta.get("direction") or question.topic or direction
    elif not direction:
        from app.models.public_library import PublicLibraryRole

        direction = (
            await db.scalar(
                select(PublicLibraryRole.selected_topic).where(
                    PublicLibraryRole.workspace_id == ws.id,
                    PublicLibraryRole.name == role,
                )
            )
            or ""
        )

    title = question.title if question is not None else direction or message("session.study")
    row = CoachThread(
        workspace_id=ws.id,
        mode=body.mode,
        role=role[:160],
        direction=direction[:120],
        question_id=question.id if question is not None else None,
        question_title=question.title if question is not None else None,
        title=title[:160],
    )
    db.add(row)
    await db.commit()
    return _detail(row, [], [])


@router.get("/threads/{thread_id}")
async def read_thread(thread_id: int, db: Database, ws: MemberWorkspace):
    row = await _thread(db, ws.id, thread_id)
    return _detail(row, await thread_turns(db, row.id), await thread_candidates(db, row.id))


@router.post("/threads/{thread_id}/turns")
async def create_turn(
    thread_id: int,
    body: TurnInput,
    background: BackgroundTasks,
    db: Database,
    ws: MemberWorkspace,
):
    thread = await _thread(db, ws.id, thread_id, lock=True)
    turns = await thread_turns(db, thread_id)
    prior = next((item for item in turns if item.request_id == str(body.request_id)), None)
    if prior is not None:
        if (prior.action, prior.user_text, prior.target_number) != (body.action, body.text, body.target_number):
            raise HTTPException(409, localized_message("errors.this_request_id_is_already_in_use_for_a"))
        return turn_view(prior)
    turn = admit_turn(thread, turns, body)
    if body.target_number is not None:
        exists = await db.scalar(
            select(CoachCandidate.id).where(
                CoachCandidate.thread_id == thread_id,
                CoachCandidate.number == body.target_number,
                CoachCandidate.superseded.is_(False),
            )
        )
        if exists is None:
            raise HTTPException(409, localized_message("errors.question_number_not_found_refresh_the_list_of_draft"))
    if not turns and thread.mode == "study" and thread.question_title is None:
        thread.title = (body.text or thread.title)[:64]
    thread.updated_at = now()
    workspace_id = ws.id
    db.add(turn)
    await db.commit()
    if turn.status == "generating":
        background.add_task(run_turn, workspace_id, thread_id, turn.id, turn.attempt)
    return turn_view(turn)


@router.post("/threads/{thread_id}/turns/{turn_id}/retry")
async def retry_turn(
    thread_id: int,
    turn_id: int,
    body: RetryInput,
    background: BackgroundTasks,
    db: Database,
    ws: MemberWorkspace,
):
    thread = await _thread(db, ws.id, thread_id, lock=True)
    turns = await thread_turns(db, thread_id)
    turn = next((item for item in turns if item.id == turn_id), None)
    if turn is None:
        raise HTTPException(404, localized_message("errors.message_not_found"))
    if effective_status(turn) != "failed" or body.attempt != turn.attempt:
        return turn_view(turn)
    if turn.id != turns[-1].id:
        raise HTTPException(409, localized_message("errors.retry_the_latest_turn_or_continue_the_conversation"))
    workspace_id = ws.id
    turn.attempt += 1
    # A hint may already have been read when its first attempt failed.
    if turn.action != "hint":
        turn.reply = ""
    turn.status = "generating"
    turn.error = None
    turn.expires_at = now() + TURN_LEASE
    thread.updated_at = now()
    await db.commit()
    background.add_task(run_turn, workspace_id, thread_id, turn.id, turn.attempt)
    return turn_view(turn)


@router.get("/threads/{thread_id}/turns/{turn_id}/events")
async def stream_turn(thread_id: int, turn_id: int, db: Database, ws: MemberWorkspace):
    workspace_id = ws.id
    owns_turn = await db.scalar(
        select(CoachTurn.id)
        .join(CoachThread)
        .where(
            CoachThread.id == thread_id,
            CoachThread.workspace_id == workspace_id,
            CoachTurn.id == turn_id,
        )
    )
    if owns_turn is None:
        raise HTTPException(404, localized_message("errors.message_not_found"))
    # Release the dependency's transaction and connection before entering the SSE loop.
    await db.rollback()
    return StreamingResponse(
        turn_events(workspace_id, thread_id, turn_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/threads/{thread_id}/candidates/{candidate_id}/collect")
async def collect_candidate(thread_id: int, candidate_id: int, db: Database, ws: MemberWorkspace):
    thread = await _thread(db, ws.id, thread_id, lock=True)
    candidate = await db.scalar(
        select(CoachCandidate).where(
            CoachCandidate.id == candidate_id,
            CoachCandidate.thread_id == thread_id,
        )
    )
    if candidate is None:
        raise HTTPException(404, localized_message("errors.draft_question_not_found"))
    if candidate.question_id is not None:
        return {"question_id": candidate.question_id}
    if candidate.superseded:
        raise HTTPException(409, localized_message("errors.a_newer_version_of_this_draft_question_is_available"))
    reference = escape(candidate.reference).replace("\n", "<br>") if candidate.reference else None
    question = Question(
        short_code=f"PC-{uuid4().hex[:24]}",
        workspace_id=ws.id,
        title=candidate.title,
        topic=thread.direction[:64] or "待分类",
        role=thread.role[:120],
        source_type=SOURCE_GENERATED,
        is_demo=False,
        answer_html=f"<p>{reference}</p>" if reference else None,
        generation_meta={
            "origin": "public_coach",
            "assessment_use": "PRACTICE_ONLY",
            "thread_id": thread.id,
            "candidate_id": candidate.id,
            "turn_id": candidate.turn_id,
            "target_role": thread.role,
            "direction": thread.direction or "待分类",
            "library_answer": candidate.reference or "",
        },
    )
    db.add(question)
    await db.flush()
    candidate.question_id = question.id
    await db.commit()
    return {"question_id": question.id}


async def export_coach(db: AsyncSession, workspace_id: int) -> dict:
    """Return the account's full coach history for the parent export endpoint."""
    rows = list(await db.scalars(_threads(workspace_id).order_by(CoachThread.id)))
    turns_by_thread = defaultdict(list)
    candidates_by_thread = defaultdict(list)
    if rows:
        for item in await db.scalars(
            select(CoachTurn)
            .join(CoachThread)
            .where(
                CoachThread.workspace_id == workspace_id,
            )
            .order_by(CoachTurn.id)
        ):
            turns_by_thread[item.thread_id].append(item)
        for item in await db.scalars(
            select(CoachCandidate)
            .join(CoachThread)
            .where(
                CoachThread.workspace_id == workspace_id,
            )
            .order_by(CoachCandidate.number, CoachCandidate.id)
        ):
            candidates_by_thread[item.thread_id].append(item)
    note_versions = (await db.execute(
        select(StudyNote.id, StudyNote.current_version_id, StudySession.context, StudyNoteVersion)
        .select_from(StudyNote)
        .join(StudySession, StudySession.id == StudyNote.origin_session_id)
        .join(StudyNoteVersion, StudyNoteVersion.note_id == StudyNote.id)
        .where(StudyNote.workspace_id == workspace_id, StudySession.workspace_id == workspace_id)
        .order_by(StudyNote.id, StudyNoteVersion.id)
    )).all()
    notes = {}
    for note_id, current_version_id, context, version in note_versions:
        entry = notes.setdefault(note_id, {
            "id": note_id, "current_version_id": current_version_id,
            "thread_id": context.get("public_coach_thread_id"), "turn_id": context.get("public_coach_turn_id"),
            "versions": [],
        })
        entry["versions"].append({**notebook.version_view(version), "body": version.body})
    export = {"threads": [_detail(row, turns_by_thread[row.id], candidates_by_thread[row.id]) for row in rows]}
    if notes:
        export["notes"] = list(notes.values())
    return export


@router.post("/threads/{thread_id}/turns/{turn_id}/note")
async def save_reply(thread_id: int, turn_id: int, db: Database, ws: MemberWorkspace):
    thread = await _thread(db, ws.id, thread_id, lock=True)
    turn = await db.scalar(select(CoachTurn).where(CoachTurn.thread_id == thread.id, CoachTurn.id == turn_id))
    if thread.mode != "study" or not turn or turn.status != "completed" or not turn.reply.strip():
        raise HTTPException(404, localized_message("errors.no_study_reply_is_available_to_save"))

    # The native notebook owns version history. Its origin session anchors this explicit snapshot.
    existing = await db.scalar(
        select(StudyNote.id)
        .join(StudySession, StudySession.id == StudyNote.origin_session_id)
        .where(
            StudyNote.workspace_id == ws.id,
            StudySession.workspace_id == ws.id,
            StudySession.context["public_coach_turn_id"].as_integer() == turn.id,
        )
    )
    if existing is not None:
        return {"id": existing}
    origin = StudySession(
        workspace_id=ws.id, title=thread.title, status="ended",
        context={"public_coach_thread_id": thread.id, "public_coach_turn_id": turn.id},
    )
    db.add(origin)
    await db.flush()
    note = await notebook.create_note(db, ws.id, origin.id, thread.title)
    version = StudyNoteVersion(
        note_id=note.id, title=note.title, body=turn.reply,
        author="AI", sources=[], session_ids=[], coverage=[],
    )
    db.add(version)
    await db.flush()
    note.current_version_id = version.id
    await db.commit()
    return {"id": note.id}


@router.get("/notes")
async def list_notes(db: Database, ws: MemberWorkspace, q: str = ""):
    return await notebook.list_notes(db, ws.id, q)


@router.get("/notes/{note_id}")
async def get_note(note_id: int, db: Database, ws: MemberWorkspace):
    return await notebook.get_note(db, ws.id, note_id)


@router.get("/notes/{note_id}/versions/{version_id}")
async def get_version(note_id: int, version_id: int, db: Database, ws: MemberWorkspace):
    return await notebook.get_version(db, ws.id, note_id, version_id)


@router.patch("/notes/{note_id}")
async def edit_note(note_id: int, body: notebook.EditIn, db: Database, ws: MemberWorkspace):
    return await notebook.edit_note(db, ws.id, note_id, body)
