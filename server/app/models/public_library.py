"""Role profiles and starter generation state for the public library."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PublicLibraryRole(Base):
    __tablename__ = "public_library_roles"

    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    jd: Mapped[str | None] = mapped_column(Text)
    selected_topic: Mapped[str | None] = mapped_column(String(64))
    directions: Mapped[list[dict[str, str]]] = mapped_column(JSONB)


class PublicLibraryStarter(Base):
    __tablename__ = "public_library_starters"

    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    input_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(32))
    lease_token: Mapped[str] = mapped_column(String(32))
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    question_ids: Mapped[list[int] | None] = mapped_column(JSONB(none_as_null=True))
