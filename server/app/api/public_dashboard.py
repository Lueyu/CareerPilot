"""Read-only home summary for the authenticated public workspace."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.public_auth import current_workspace
from app.api.public_library import _imports, _questions, _role_rows
from app.db import get_session
from app.models import ImportSession, Question, Workspace
from app.models.public_coach import CoachThread, CoachTurn
from app.models.public_library import PublicLibraryRole

router = APIRouter(tags=["public-dashboard"])
Db = Annotated[AsyncSession, Depends(get_session)]
Owner = Annotated[Workspace, Depends(current_workspace)]


class Practice(BaseModel):
    id: int
    mode: Literal["interview", "study"]
    title: str
    updated_at: datetime


class PendingImport(BaseModel):
    id: int
    filename: str | None
    created_at: datetime


class DashboardSummary(BaseModel):
    selected_role: str | None
    role_count: int
    question_count: int
    import_count: int
    pending_import_count: int
    practice_count: int
    latest_activity: Practice | None
    resume_interview: Practice | None
    resume_study: Practice | None
    pending_imports: list[PendingImport]


@router.get("/dashboard", response_model=DashboardSummary)
async def dashboard(db: Db, ws: Owner) -> DashboardSummary:
    roles = _role_rows(ws.id).with_only_columns(PublicLibraryRole.name)
    questions = _questions(ws.id).with_only_columns(Question.id)
    imports = _imports(ws.id).with_only_columns(ImportSession.id)
    pending = imports.where(ImportSession.status == ImportSession.STATUS_REVIEW)
    threads = select(CoachThread.id).where(CoachThread.workspace_id == ws.id)
    supported = threads.where(CoachThread.mode.in_(("interview", "study")))

    def count(query):
        return select(func.count()).select_from(query.subquery()).scalar_subquery()

    role, role_count, question_count, import_count, pending_count, practice_count = (
        await db.execute(select(
            roles.where(PublicLibraryRole.name == ws.target_role).scalar_subquery(),
            count(roles), count(questions), count(imports), count(pending), count(supported),
        ))
    ).one()

    practice = supported.with_only_columns(
        CoachThread.id, CoachThread.mode, CoachThread.title, CoachThread.updated_at,
    ).order_by(CoachThread.updated_at.desc(), CoachThread.id.desc()).limit(1)
    finished = select(CoachTurn.id).where(
        CoachTurn.thread_id == CoachThread.id,
        CoachTurn.action == "finish",
        CoachTurn.status == "completed",
    ).exists()
    latest = (await db.execute(practice)).mappings().one_or_none()
    interview = (await db.execute(practice.where(CoachThread.mode == "interview", ~finished))).mappings().one_or_none()
    study = (await db.execute(practice.where(CoachThread.mode == "study"))).mappings().one_or_none()
    batches = await db.execute(pending.with_only_columns(
        ImportSession.id, ImportSession.filename, ImportSession.created_at,
    ).order_by(ImportSession.created_at.desc(), ImportSession.id.desc()).limit(3))
    return DashboardSummary(
        selected_role=role,
        role_count=role_count,
        question_count=question_count,
        import_count=import_count,
        pending_import_count=pending_count,
        practice_count=practice_count,
        latest_activity=Practice.model_validate(latest) if latest is not None else None,
        resume_interview=Practice.model_validate(interview) if interview is not None else None,
        resume_study=Practice.model_validate(study) if study is not None else None,
        pending_imports=[PendingImport.model_validate(batch) for batch in batches.mappings()],
    )
