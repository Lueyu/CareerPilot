"""Public model usage and workspace-private replay records."""
from datetime import date, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PublicModelMonth(Base):
    __tablename__ = "public_model_months"

    month: Mapped[date] = mapped_column(primary_key=True)
    charged_nano_cny: Mapped[int] = mapped_column(BigInteger)


class PublicModelCall(Base):
    __tablename__ = "public_model_calls"
    __table_args__ = (Index("ix_public_model_calls_workspace_day", "workspace_id", "day"),)

    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reserved_nano_cny: Mapped[int | None] = mapped_column(BigInteger)
    charged_nano_cny: Mapped[int | None] = mapped_column(BigInteger)
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    result: Mapped[str | None] = mapped_column(Text)
