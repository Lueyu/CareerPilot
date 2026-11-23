"""Role profiles for the public library."""

from sqlalchemy import ForeignKey, String, Text
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
