"""Credentials and durable throttles for the public entry point."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PublicAccount(Base):
    __tablename__ = "seed_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    recovery_hash: Mapped[str] = mapped_column(String(64))


class PublicSession(Base):
    __tablename__ = "seed_auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("seed_accounts.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PublicAuthThrottle(Base):
    __tablename__ = "seed_auth_throttles"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
