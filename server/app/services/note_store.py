"""Workspace-scoped storage and version views for native study notes."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from app.models.study_notes import StudyNote, StudyNoteVersion


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_version_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=80000)
    source_version_id: int | None = Field(default=None, gt=0)


def utcnow():
    return datetime.now(timezone.utc)


async def create_note(db, workspace_id, origin_session_id, title):
    note = StudyNote(workspace_id=workspace_id, origin_session_id=origin_session_id, title=title)
    db.add(note)
    await db.flush()
    return note


async def own_note(db, workspace_id, note_id, *, lock=False):
    stmt = select(StudyNote).where(StudyNote.id == note_id, StudyNote.workspace_id == workspace_id)
    row = await db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise HTTPException(404, "笔记不存在")
    return row


async def own_version(db, note_id, version_id, *, lock=False):
    stmt = select(StudyNoteVersion).where(StudyNoteVersion.id == version_id, StudyNoteVersion.note_id == note_id)
    row = await db.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise HTTPException(404, "笔记版本不存在")
    return row


def version_view(row):
    return {"id": row.id, "version_id": row.id, "note_id": row.note_id, "title": row.title,
            "author": row.author, "model": row.model, "created_at": row.created_at,
            "base_version_id": row.base_version_id, "source_version_id": row.source_version_id,
            "session_ids": row.session_ids, "message_ids": row.coverage, "conflict": row.conflict}


async def list_notes(db, workspace_id, q: str = ""):
    stmt = (select(StudyNote, StudyNoteVersion).join(StudyNoteVersion, StudyNoteVersion.id == StudyNote.current_version_id)
            .where(StudyNote.workspace_id == workspace_id).order_by(StudyNote.updated_at.desc(), StudyNote.id.desc()))
    if q.strip():
        term = q.strip()[:300]
        stmt = stmt.where(or_(StudyNote.title.icontains(term, autoescape=True),
                              StudyNoteVersion.body.icontains(term, autoescape=True)))
    rows = (await db.execute(stmt)).all()
    return {"items": [{"id": n.id, "title": n.title, "version_id": v.id, "session_ids": v.session_ids,
                       "updated_at": n.updated_at} for n, v in rows]}


async def get_note(db, workspace_id, note_id: int):
    note = await own_note(db, workspace_id, note_id)
    if not note.current_version_id:
        raise HTTPException(404, "尚未生成可查看的笔记版本")
    versions = (await db.scalars(select(StudyNoteVersion).where(StudyNoteVersion.note_id == note.id)
                                 .order_by(StudyNoteVersion.id.desc()))).all()
    active = next(v for v in versions if v.id == note.current_version_id)
    return {**version_view(active), "body": active.body, "sources": active.sources, "id": note.id,
            "updated_at": note.updated_at, "versions": [version_view(v) for v in versions]}


async def get_version(db, workspace_id, note_id: int, version_id: int):
    await own_note(db, workspace_id, note_id)
    row = await own_version(db, note_id, version_id)
    return {**version_view(row), "body": row.body, "sources": row.sources}


async def edit_note(db, workspace_id, note_id: int, body: EditIn):
    note = await own_note(db, workspace_id, note_id, lock=True)
    if note.current_version_id != body.expected_version_id:
        raise HTTPException(409, "当前笔记已更新，请保留编辑内容并对照新版本")
    source = await own_version(db, note.id, body.source_version_id or note.current_version_id)
    version = StudyNoteVersion(note_id=note.id, title=body.title, body=body.body, author="USER",
                               base_version_id=note.current_version_id, source_version_id=source.id,
                               session_ids=source.session_ids, coverage=source.coverage, sources=source.sources)
    db.add(version)
    await db.flush()
    note.current_version_id, note.title, note.updated_at = version.id, version.title, utcnow()
    await db.commit()
    return await get_note(db, workspace_id, note.id)
