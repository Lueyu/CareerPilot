"""A study exercise links to the existing immutable learning and exam records."""
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StudyPractice(Base):
    __tablename__ = "study_practices"
    __table_args__ = (UniqueConstraint("session_id", "request_key", name="uq_study_practice_request"),)

    id: Mapped[int] = mapped_column(ForeignKey("learning_activities.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("study_sessions.id", ondelete="CASCADE"), index=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("study_cards.id", ondelete="SET NULL"))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"))
    kind: Mapped[str] = mapped_column(String(16))
    knowledge_point: Mapped[str] = mapped_column(String(180))
    request_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[dict] = mapped_column(JSONB)
    retest_exam_id: Mapped[int | None] = mapped_column(ForeignKey("exams.id", ondelete="SET NULL"), unique=True)
