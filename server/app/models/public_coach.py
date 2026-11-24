from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CoachThread(Base):
    __tablename__ = "public_coach_threads"
    __table_args__ = (Index("ix_public_coach_threads_workspace", "workspace_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(160))
    direction: Mapped[str] = mapped_column(String(120), default="")
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"))
    question_title: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoachTurn(Base):
    __tablename__ = "public_coach_turns"
    __table_args__ = (
        UniqueConstraint("thread_id", "request_id", name="uq_public_coach_turn_request"),
        Index("ix_public_coach_turn_thread", "thread_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("public_coach_threads.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(24))
    user_text: Mapped[str | None] = mapped_column(Text)
    response_language: Mapped[str] = mapped_column(String(8), server_default="zh-CN")
    assisted: Mapped[bool | None] = mapped_column()
    target_number: Mapped[int | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(16))
    reply: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CoachCandidate(Base):
    __tablename__ = "public_coach_candidates"
    __table_args__ = (Index("ix_public_coach_candidate_thread", "thread_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("public_coach_threads.id", ondelete="CASCADE"))
    turn_id: Mapped[int] = mapped_column(ForeignKey("public_coach_turns.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(Text)
    superseded: Mapped[bool] = mapped_column(default=False)
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"))
