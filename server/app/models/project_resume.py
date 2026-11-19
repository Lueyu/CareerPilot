from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProjectResumeDraft(Base):
    __tablename__ = 'project_resume_drafts'
    __table_args__ = (CheckConstraint('revision > 0', name='ck_project_resume_revision'),)

    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int]
    contribution: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, server_default='')
    content: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
