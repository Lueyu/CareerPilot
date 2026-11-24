"""Delivery metadata. Feedback text is never persisted."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class FeedbackSubmission(Base):
    __tablename__ = "feedback_submissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress', 'sent', 'rejected', 'retryable', 'unknown')",
            name="feedback_submission_status",
        ),
    )

    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    submission_id: Mapped[UUID] = mapped_column(primary_key=True)
    message_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))


class FeedbackAttempt(Base):
    __tablename__ = "feedback_attempts"
    __table_args__ = (Index("ix_feedback_attempts_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
