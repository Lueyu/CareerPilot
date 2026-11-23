"""Immutable previews of explicitly selected project submissions."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LearningProjectSnapshot(Base):
    __tablename__ = 'learning_project_snapshots'
    __table_args__ = (UniqueConstraint('workspace_id', 'activity_id', 'content_hash', name='uq_learning_project_snapshot'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey('learning_goals.id', ondelete='CASCADE'), index=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey('learning_activities.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    activity_version: Mapped[int]
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
