from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from app.api.deps import get_workspace
from app.db import get_session
from app.models import Workspace
from app.models.study_notes import StudyCard, StudyCardEvent, StudyNote, StudyNoteJob, StudyNoteVersion
from app.services import study_notes as service

router = APIRouter(prefix="/study", tags=["study-notes"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class JobIn(Input):
    focus_message_id: int | None = Field(default=None, gt=0)
    request_id: str = Field(min_length=1, max_length=100)
    note_id: int | None = Field(default=None, gt=0)


class EditIn(Input):
    expected_version_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=80000)
    source_version_id: int | None = Field(default=None, gt=0)


class CardGenIn(Input):
    version_id: int = Field(gt=0)


class CardEditIn(Input):
    question: str | None = Field(default=None, min_length=1, max_length=3000)
    answer: str | None = Field(default=None, min_length=1, max_length=5000)
    knowledge_point: str | None = Field(default=None, min_length=1, max_length=180)
    status: Literal["ACTIVE", "SKIPPED"] | None = None


class ReviewIn(Input):
    rating: Literal["again", "hard", "good"]
    request_id: str = Field(min_length=1, max_length=100)


def expired(job):
    return job.status == "RUNNING" and job.lease_until and job.lease_until < service.utcnow()


def job_view(job):
    stale = expired(job)
    return {"id": job.id, "session_id": job.session_id, "status": "FAILED" if stale else job.status,
            "base_version_id": job.base_version_id, "version_id": job.version_id, "note_id": job.note_id,
            "request_id": job.request_id, "conflict": job.conflict, "created_at": job.created_at,
            "message_ids": [m["id"] for m in job.snapshot],
            "error": "上次整理已中断，可以重试" if stale else job.error}


@router.post("/sessions/{session_id}/note-jobs", status_code=202)
async def enqueue_note(session_id: int, body: JobIn, ws=Depends(get_workspace), db=Depends(get_session)):
    return job_view(await service.enqueue(db, ws.id, session_id, body.request_id, body.note_id, body.focus_message_id))


@router.get("/sessions/{session_id}/note-jobs")
async def session_jobs(session_id: int, ws=Depends(get_workspace), db=Depends(get_session)):
    await service.own_session(db, ws.id, session_id)
    jobs = (await db.scalars(select(StudyNoteJob).where(StudyNoteJob.workspace_id == ws.id,
              StudyNoteJob.session_id == session_id).order_by(StudyNoteJob.id.desc()))).all()
    return {"items": [job_view(job) for job in jobs]}


async def own_job(db, workspace_id, job_id, *, lock=False):
    query = select(StudyNoteJob).where(StudyNoteJob.workspace_id == workspace_id, StudyNoteJob.id == job_id)
    job = await db.scalar(query.with_for_update() if lock else query)
    if not job:
        raise HTTPException(404, "整理任务不存在")
    return job


@router.get("/note-jobs/{job_id}")
async def get_job(job_id: int, ws=Depends(get_workspace), db=Depends(get_session)):
    return job_view(await own_job(db, ws.id, job_id))


@router.post("/note-jobs/{job_id}/retry", status_code=202)
async def retry_job(job_id: int, ws=Depends(get_workspace), db=Depends(get_session)):
    job = await own_job(db, ws.id, job_id, lock=True)
    if job.status == "FAILED" or expired(job):
        job.generation += 1
        job.status, job.error, job.lease_until = "QUEUED", None, None
        await db.commit()
    return job_view(job)


def version_view(row):
    return {"id": row.id, "version_id": row.id, "note_id": row.note_id, "title": row.title,
            "author": row.author, "model": row.model, "created_at": row.created_at,
            "base_version_id": row.base_version_id, "source_version_id": row.source_version_id,
            "session_ids": row.session_ids, "message_ids": row.coverage, "conflict": row.conflict}


@router.get("/notes")
async def list_notes(q: str = "", ws=Depends(get_workspace), db=Depends(get_session)):
    stmt = (select(StudyNote, StudyNoteVersion).join(StudyNoteVersion, StudyNoteVersion.id == StudyNote.current_version_id)
            .where(StudyNote.workspace_id == ws.id).order_by(StudyNote.updated_at.desc(), StudyNote.id.desc()))
    if q.strip():
        term = q.strip()[:300]
        stmt = stmt.where(or_(StudyNote.title.icontains(term, autoescape=True),
                              StudyNoteVersion.body.icontains(term, autoescape=True)))
    rows = (await db.execute(stmt)).all()
    return {"items": [{"id": n.id, "title": n.title, "version_id": v.id, "session_ids": v.session_ids,
                       "updated_at": n.updated_at} for n, v in rows]}


@router.get("/notes/{note_id}")
async def get_note(note_id: int, ws=Depends(get_workspace), db=Depends(get_session)):
    note = await service.own_note(db, ws.id, note_id)
    if not note.current_version_id:
        raise HTTPException(404, "尚未生成可查看的笔记版本")
    versions = (await db.scalars(select(StudyNoteVersion).where(StudyNoteVersion.note_id == note.id)
                                 .order_by(StudyNoteVersion.id.desc()))).all()
    active = next(v for v in versions if v.id == note.current_version_id)
    return {**version_view(active), "body": active.body, "sources": active.sources, "id": note.id,
            "updated_at": note.updated_at, "versions": [version_view(v) for v in versions]}


@router.get("/notes/{note_id}/versions/{version_id}")
async def get_version(note_id: int, version_id: int, ws=Depends(get_workspace), db=Depends(get_session)):
    await service.own_note(db, ws.id, note_id)
    row = await service.own_version(db, note_id, version_id)
    return {**version_view(row), "body": row.body, "sources": row.sources}


@router.patch("/notes/{note_id}")
async def edit_note(note_id: int, body: EditIn, ws=Depends(get_workspace), db=Depends(get_session)):
    note = await service.own_note(db, ws.id, note_id, lock=True)
    if note.current_version_id != body.expected_version_id:
        raise HTTPException(409, "当前笔记已更新，请保留编辑内容并对照新版本")
    source = await service.own_version(db, note.id, body.source_version_id or note.current_version_id)
    version = StudyNoteVersion(note_id=note.id, title=body.title, body=body.body, author="USER",
                               base_version_id=note.current_version_id, source_version_id=source.id,
                               session_ids=source.session_ids, coverage=source.coverage, sources=source.sources)
    db.add(version)
    await db.flush()
    note.current_version_id, note.title, note.updated_at = version.id, version.title, service.utcnow()
    await db.commit()
    return await get_note(note.id, ws, db)


def card_view(card):
    return {"id": card.id, "note_id": card.note_id, "version_id": card.version_id, "question": card.question,
            "knowledge_point": card.knowledge_point, "status": card.status, "next_review_at": card.next_review_at}


async def own_card(db, workspace_id, card_id, *, lock=False):
    stmt = select(StudyCard).where(StudyCard.workspace_id == workspace_id, StudyCard.id == card_id,
                                  StudyCard.status != "DELETED")
    card = await db.scalar(stmt.with_for_update() if lock else stmt)
    if not card:
        raise HTTPException(404, "复习卡不存在")
    return card


async def lock_answer_exposure(db, workspace_id):
    # Practice submissions take the same lock before freezing answer exposure.
    await db.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())


@router.post("/notes/{note_id}/cards")
async def create_cards(note_id: int, body: CardGenIn, ws=Depends(get_workspace), db=Depends(get_session)):
    await service.own_note(db, ws.id, note_id)
    # Only generation on this immutable version waits on the bounded provider call.
    # If the request is interrupted, its transaction releases the lock for a retry.
    version = await service.own_version(db, note_id, body.version_id, lock=True)
    if not version.cards_generated:
        try:
            generated = await service.generate_cards(version.body)
        except service.GenerationError as exc:
            raise HTTPException(502, str(exc)) from exc
        for item in generated:
            db.add(StudyCard(workspace_id=ws.id, note_id=note_id, version_id=version.id, **item.model_dump()))
        version.cards_generated = True
        await db.commit()
    return {"items": [card_view(card) for card in await service.cards_for_version(db, version.id)]}


@router.get("/cards")
async def list_cards(status: Literal["CANDIDATE", "ACTIVE", "DUE", "SKIPPED"] | None = None,
                     ws=Depends(get_workspace), db=Depends(get_session)):
    stmt = select(StudyCard).where(StudyCard.workspace_id == ws.id, StudyCard.status != "DELETED")
    if status == "DUE":
        stmt = stmt.where(StudyCard.status == "ACTIVE", StudyCard.next_review_at <= service.utcnow())
    elif status:
        stmt = stmt.where(StudyCard.status == status)
    cards = (await db.scalars(stmt.order_by(StudyCard.next_review_at.asc().nulls_first(), StudyCard.id))).all()
    return {"items": [card_view(card) for card in cards]}


@router.post("/cards/{card_id}/reveal")
async def reveal(card_id: int, ws=Depends(get_workspace), db=Depends(get_session)):
    await lock_answer_exposure(db, ws.id)
    card = await own_card(db, ws.id, card_id, lock=True)
    db.add(StudyCardEvent(card_id=card.id, kind="REVEAL"))
    await db.commit()
    return {**card_view(card), "answer": card.answer}


@router.patch("/cards/{card_id}")
async def edit_card(card_id: int, body: CardEditIn, ws=Depends(get_workspace), db=Depends(get_session)):
    if body.answer is not None:
        await lock_answer_exposure(db, ws.id)
    card = await own_card(db, ws.id, card_id, lock=True)
    changes = body.model_dump(exclude_none=True)
    if changes.get("answer"):
        db.add(StudyCardEvent(card_id=card.id, kind="EDIT_ANSWER"))
    if changes.get("status") == "ACTIVE" and card.status != "ACTIVE":
        card.next_review_at = service.utcnow()
    if changes.get("status") == "SKIPPED":
        card.next_review_at = None
    for key, value in changes.items():
        setattr(card, key, value)
    await db.commit()
    return card_view(card)


@router.delete("/cards/{card_id}")
async def delete_card(card_id: int, ws=Depends(get_workspace), db=Depends(get_session)):
    card = await own_card(db, ws.id, card_id, lock=True)
    card.status, card.next_review_at = "DELETED", None
    await db.commit()
    return {"id": card_id, "deleted": True}


@router.post("/cards/{card_id}/review")
async def review_card(card_id: int, body: ReviewIn, ws=Depends(get_workspace), db=Depends(get_session)):
    card = await own_card(db, ws.id, card_id, lock=True)
    if card.status != "ACTIVE":
        raise HTTPException(409, "请先将卡片加入复习")
    event = await db.scalar(select(StudyCardEvent).where(StudyCardEvent.card_id == card.id,
                                                        StudyCardEvent.request_id == body.request_id))
    if event:
        if event.rating != body.rating:
            raise HTTPException(409, "该自评请求已提交其他结果")
    else:
        now = service.utcnow()
        card.next_review_at = now + {"again": timedelta(minutes=10), "hard": timedelta(days=1),
                                    "good": timedelta(days=3)}[body.rating]
        db.add(StudyCardEvent(card_id=card.id, kind="SELF_REVIEW", rating=body.rating, request_id=body.request_id))
        await db.commit()
    return card_view(card)
