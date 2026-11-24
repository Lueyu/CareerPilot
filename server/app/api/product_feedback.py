"""Authenticated product feedback shared by the native API and registered gateway."""
import hashlib
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.product_feedback import FeedbackAttempt, FeedbackSubmission
from app.services import feedback_delivery

_MAX_BODY_BYTES = 32 * 1024
_WORKSPACE_LIMIT = 5
_GLOBAL_LIMIT = 100
_WINDOW = timedelta(hours=1)
# Serializes quota checks and reservation writes across all processes on this database.
_RESERVATION_LOCK = text("SELECT pg_advisory_xact_lock(725806112)")
_CONFLICT = "Feedback delivery is in progress or its outcome cannot be confirmed."
_UNAVAILABLE = "Feedback delivery is temporarily unavailable."
_REJECTED = "Feedback content was rejected."


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


async def _read_feedback(request: Request) -> FeedbackInput:
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
        raise HTTPException(422, "Expected a JSON feedback submission.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_BODY_BYTES:
            raise HTTPException(413, "Feedback request is too large.")
        body.extend(chunk)
    try:
        return FeedbackInput.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "Invalid feedback submission.") from None


async def _reserve(
    db: AsyncSession, workspace_id: int, body: FeedbackInput
) -> FeedbackSubmission | None:
    digest = hashlib.sha256(body.message.encode()).hexdigest()
    await db.execute(_RESERVATION_LOCK)
    row = await db.get(FeedbackSubmission, (workspace_id, body.submission_id))
    if row is not None:
        if row.message_digest != digest:
            raise HTTPException(409, "Submission ID was already used with different feedback.")
        if row.status == "sent":
            return None
        if row.status == "rejected":
            raise HTTPException(422, _REJECTED)
        if row.status != "retryable":
            raise HTTPException(409, _CONFLICT)

    cutoff = await db.scalar(select(func.clock_timestamp())) - _WINDOW
    await db.execute(delete(FeedbackAttempt).where(FeedbackAttempt.created_at <= cutoff))
    total, workspace_total = (
        await db.execute(select(
            func.count(),
            func.count().filter(FeedbackAttempt.workspace_id == workspace_id),
        ).select_from(FeedbackAttempt))
    ).one()
    if total >= _GLOBAL_LIMIT or workspace_total >= _WORKSPACE_LIMIT:
        raise HTTPException(429, "Feedback quota exceeded.")
    db.add(FeedbackAttempt(workspace_id=workspace_id))
    if row is None:
        row = FeedbackSubmission(
            workspace_id=workspace_id,
            submission_id=body.submission_id,
            message_digest=digest,
            status="in_progress",
        )
        db.add(row)
    else:
        row.status = "in_progress"
    await db.commit()
    return row


async def submit(request: Request, db: AsyncSession, workspace_id: int) -> dict[str, str]:
    # Release the auth connection before receiving the body.
    await db.rollback()
    body = await _read_feedback(request)
    row = await _reserve(db, workspace_id, body)
    if row is None:
        return {"status": "sent"}

    try:
        await feedback_delivery.review_and_send(body.message, body.submission_id)
    except feedback_delivery.FeedbackRejected:
        row.status = "rejected"
    except feedback_delivery.FeedbackUnavailable:
        row.status = "retryable"
    except feedback_delivery.FeedbackDeliveryUnknown:
        row.status = "unknown"
    except Exception:
        # An unexpected exception provides no proof that SMTP did not accept.
        row.status = "unknown"
    else:
        row.status = "sent"

    status = row.status
    try:
        await db.commit()
    except (SQLAlchemyError, OSError):
        raise HTTPException(409, _CONFLICT) from None

    if status == "rejected":
        raise HTTPException(422, _REJECTED)
    if status == "retryable":
        raise HTTPException(503, _UNAVAILABLE)
    if status == "unknown":
        raise HTTPException(409, _CONFLICT)
    return {"status": "sent"}


class FeedbackRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def feedback_handler(request: Request):
            try:
                return await handle(request)
            except (SQLAlchemyError, OSError):
                raise HTTPException(503, _UNAVAILABLE) from None

        return feedback_handler


Database = Annotated[AsyncSession, Depends(get_session)]
