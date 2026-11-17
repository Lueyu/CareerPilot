"""Persisted generation request; model output is study material, never evidence."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class QuestionGeneration(Base):
    __tablename__ = "question_generations"
    __table_args__ = (UniqueConstraint("workspace_id", "request_key", name="uq_generation_request"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    mode: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="READY")
    request_key: Mapped[str] = mapped_column(String(120))
    request_json: Mapped[dict] = mapped_column(JSONB)
    input_snapshot: Mapped[dict] = mapped_column(JSONB)
    prompt_text: Mapped[str] = mapped_column(Text, default="")
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    import_session_id: Mapped[int | None] = mapped_column(ForeignKey("import_sessions.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
