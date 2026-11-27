"""Durable API admission balances, independent from billing and business transactions."""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ApiRateBucket(Base):
    __tablename__ = "api_rate_buckets"
    __table_args__ = (
        CheckConstraint("burst_tokens >= 0 AND daily_count >= 0"),
        CheckConstraint("category IN ('read', 'write', 'llm')"),
    )

    subject: Mapped[str] = mapped_column(String(80), primary_key=True)
    category: Mapped[str] = mapped_column(String(8), primary_key=True)
    burst_tokens: Mapped[float] = mapped_column(Float)
    quota_day: Mapped[date] = mapped_column(Date)
    daily_count: Mapped[int]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
