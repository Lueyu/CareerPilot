from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StudyNote(Base):
    __tablename__ = "study_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    origin_session_id: Mapped[int] = mapped_column(ForeignKey("study_sessions.id", ondelete="CASCADE"), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    current_version_id: Mapped[int | None] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudyNoteVersion(Base):
    __tablename__ = "study_note_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("study_notes.id", ondelete="CASCADE"), index=True)
    base_version_id: Mapped[int | None] = mapped_column()
    source_version_id: Mapped[int | None] = mapped_column()
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(180))
    sources: Mapped[list] = mapped_column(JSONB)
    session_ids: Mapped[list] = mapped_column(JSONB)
    coverage: Mapped[list] = mapped_column(JSONB)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    cards_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudyNoteJob(Base):
    __tablename__ = "study_note_jobs"
    __table_args__ = (UniqueConstraint("session_id", "request_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    session_id: Mapped[int] = mapped_column(ForeignKey("study_sessions.id", ondelete="CASCADE"), index=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("study_notes.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(100))
    request_aliases: Mapped[list] = mapped_column(JSONB, default=list)
    base_version_id: Mapped[int | None] = mapped_column()
    version_id: Mapped[int | None] = mapped_column()
    snapshot: Mapped[list] = mapped_column(JSONB)
    conflict: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", index=True)
    generation: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(250))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudyCard(Base):
    __tablename__ = "study_cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    note_id: Mapped[int] = mapped_column(ForeignKey("study_notes.id", ondelete="CASCADE"))
    version_id: Mapped[int] = mapped_column(ForeignKey("study_note_versions.id", ondelete="CASCADE"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    knowledge_point: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(16), default="CANDIDATE")
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StudyCardEvent(Base):
    __tablename__ = "study_card_events"
    __table_args__ = (UniqueConstraint("card_id", "request_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("study_cards.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    rating: Mapped[str | None] = mapped_column(String(12))
    request_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
