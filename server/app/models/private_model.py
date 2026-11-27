"""Durable financial records, deliberately independent of business row lifetimes."""
from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PrivateModelCall(Base):
    __tablename__ = "private_model_calls"
    __table_args__ = (
        Index("ix_private_model_day", "day"),
        Index("ix_private_model_workspace_day", "workspace_id", "day"),
        CheckConstraint("state IN ('RESERVED', 'SETTLED', 'UNKNOWN')"),
        CheckConstraint("reserved_nano_cny >= 0 AND charged_nano_cny >= 0"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger)
    operation: Mapped[str] = mapped_column(String(160))
    day: Mapped[date] = mapped_column(Date)
    provider: Mapped[str] = mapped_column(String(120))
    endpoint: Mapped[str] = mapped_column(String(1024))
    model: Mapped[str] = mapped_column(String(200))
    input_price_nano_per_million: Mapped[int] = mapped_column(BigInteger)
    output_price_nano_per_million: Mapped[int] = mapped_column(BigInteger)
    daily_limit_nano_cny: Mapped[int] = mapped_column(BigInteger)
    input_ceiling: Mapped[int] = mapped_column(BigInteger)
    max_tokens: Mapped[int] = mapped_column(BigInteger)
    reserved_nano_cny: Mapped[int] = mapped_column(BigInteger)
    charged_nano_cny: Mapped[int] = mapped_column(BigInteger)
    actual_nano_cny: Mapped[int | None] = mapped_column(BigInteger)
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16), default="RESERVED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
