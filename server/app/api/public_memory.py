"""Confirmed personal experiences, feedback and data portability in public workspaces."""
import os
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public_auth import current_workspace
from app.db import get_session
from app.models import Event, MemoryItem, Question, Workspace

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_session)]
Owner = Annotated[Workspace, Depends(current_workspace)]
EntryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2400)]
MEMORY_TYPE = "InterviewExperience"


class Experience(BaseModel):
    text: EntryText
    source: Literal["user_statement"] = "user_statement"
    confirmed: Literal[True]


class Feedback(BaseModel):
    question_id: int
    text: EntryText


def memory_query(workspace_id):
    return select(MemoryItem).where(
        MemoryItem.workspace_id == workspace_id,
        MemoryItem.mtype == MEMORY_TYPE,
        MemoryItem.status == MemoryItem.STATUS_ACTIVE,
        MemoryItem.confirmed_at.is_not(None),
    )


def memory_view(row):
    return {"id": row.id, "text": row.content, "confirmed_at": row.confirmed_at,
            "source": "user_statement", "revision": row.revision}


async def confirmed_context(db: AsyncSession, workspace_id: int) -> list[str]:
    query = memory_query(workspace_id).order_by(MemoryItem.confirmed_at.desc(), MemoryItem.id.desc()).limit(16)
    return [row.content for row in (await db.scalars(query)).all()]


@router.get("/privacy")
async def privacy():
    return {"region": os.environ.get("SEED_STORAGE_REGION", "本机隔离数据库"),
            "provider": os.environ.get("SEED_MODEL_PROVIDER_LABEL", "未配置模型服务")}


@router.get("/memories")
async def memories(db: Db, ws: Owner):
    rows = await db.scalars(memory_query(ws.id).order_by(MemoryItem.id.desc()))
    return [memory_view(row) for row in rows]


@router.post("/memories", status_code=201)
async def add_memory(body: Experience, db: Db, ws: Owner):
    row = MemoryItem(workspace_id=ws.id, mtype=MEMORY_TYPE, content=body.text, source="USER",
                     status=MemoryItem.STATUS_ACTIVE, confirmed_at=datetime.now(timezone.utc))
    db.add(row)
    await db.commit()
    return memory_view(row)


async def owned_memory(db: Db, ws: Owner, memory_id: int):
    row = await db.scalar(memory_query(ws.id).where(MemoryItem.id == memory_id).with_for_update())
    if row is None:
        raise HTTPException(404, "找不到这条经历")
    return row


@router.put("/memories/{memory_id}")
async def edit_memory(memory_id: int, body: Experience, db: Db, ws: Owner):
    row = await owned_memory(db, ws, memory_id)
    row.content = body.text
    row.confirmed_at = datetime.now(timezone.utc)
    row.revision += 1
    await db.commit()
    return memory_view(row)


@router.delete("/memories/{memory_id}", status_code=204)
async def remove_memory(memory_id: int, db: Db, ws: Owner):
    row = await owned_memory(db, ws, memory_id)
    await db.delete(row)
    await db.commit()
    return Response(status_code=204)


@router.post("/feedback", status_code=201)
async def submit_feedback(body: Feedback, db: Db, ws: Owner):
    question = await db.scalar(select(Question.id).where(
        Question.id == body.question_id, Question.workspace_id == ws.id))
    if question is None:
        raise HTTPException(404, "找不到这道题")
    event = Event(workspace_id=ws.id, kind="seed.question_feedback", ref_type="question",
                  ref_id=question, payload={"text": body.text})
    db.add(event)
    await db.commit()
    return {"id": event.id}


@router.get("/export")
async def download_export(db: Db, ws: Owner):
    from app.api.public_coach import export_coach
    from app.api.public_library import export_library

    feedback = await db.scalars(select(Event).where(
        Event.workspace_id == ws.id, Event.kind == "seed.question_feedback").order_by(Event.id))
    return {"format_version": 1, "exported_at": datetime.now(timezone.utc),
            "target_role": ws.target_role, "library": await export_library(db, ws.id),
            "coach": await export_coach(db, ws.id), "memories": await memories(db, ws),
            "feedback": [{"question_id": item.ref_id, "text": item.payload["text"]} for item in feedback]}
