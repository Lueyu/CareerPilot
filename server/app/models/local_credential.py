"""Local access credentials and scope survive token rotation and configuration edits."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LocalWorkspaceBoundary(Base):
    __tablename__ = "local_workspace_boundaries"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(unique=True)


class LocalCredential(Base):
    __tablename__ = "local_credentials"
    __table_args__ = (
        CheckConstraint("kind IN ('OWNER', 'WORKSPACE')"),
        CheckConstraint("(kind = 'OWNER' AND workspace_id IS NULL) OR (kind = 'WORKSPACE' AND workspace_id IS NOT NULL)"),
        CheckConstraint("expires_at > created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    # Retain digest tombstones even if a workspace is deleted.
    workspace_id: Mapped[int | None]
    generation: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LocalAuthFailure(Base):
    __tablename__ = "local_auth_failures"
    __table_args__ = (CheckConstraint("id = 1 AND tokens >= 0 AND tokens <= 10"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tokens: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
