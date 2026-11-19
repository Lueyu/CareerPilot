"""Explicit workspace review of a criterion's assessed behavior."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RetestCriterionMapping(Base):
    __tablename__ = "retest_criterion_mappings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "question_id", "rubric_version", "criterion_id",
                         name="uq_retest_criterion_mapping"),
        UniqueConstraint("workspace_id", "question_id", "rubric_version", "behavior_key", "behavior_version",
                         name="uq_retest_rubric_behavior"),
        CheckConstraint("behavior_version > 0", name="ck_retest_behavior_version_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    rubric_version: Mapped[str] = mapped_column(String(100))
    criterion_id: Mapped[str] = mapped_column(String(64))
    behavior_key: Mapped[str] = mapped_column(String(128))
    behavior_version: Mapped[int]
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
