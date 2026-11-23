from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('active','ended')", name="ck_study_session_status"),
        Index("ix_study_session_workspace", "workspace_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16), default="active")
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StudyInterviewQuestion(Base):
    __tablename__ = "study_interview_questions"
    __table_args__ = (
        UniqueConstraint("session_id", "snapshot_hash", name="uq_study_interview_question_snapshot"),
    )

    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("study_sessions.id", ondelete="CASCADE"))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    last_message_id: Mapped[int | None] = mapped_column(Integer)


class StudyMessage(Base):
    __tablename__ = "study_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "request_id", "role", name="uq_study_message_request"),
        CheckConstraint("role IN ('user','assistant')", name="ck_study_message_role"),
        CheckConstraint("status IN ('generating','completed','interrupted','failed')", name="ck_study_message_status"),
        Index("ix_study_message_history", "session_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("study_sessions.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))
    # Retain the original selection for idempotency even if a project is archived.
    project_id: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(24))
    coach: Mapped[dict] = mapped_column(JSONB, default=dict)
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(200))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
