"""Learning links only; answer grades remain in the existing assessment tables."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LearningGoal(Base):
    __tablename__ = 'learning_goals'
    __table_args__ = (UniqueConstraint('workspace_id', 'origin_key', name='uq_learning_goal_origin'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    source_task_id: Mapped[int | None] = mapped_column(ForeignKey('learning_tasks.id', ondelete='SET NULL'), nullable=True, index=True)
    origin_key: Mapped[str] = mapped_column(String(64))
    origin_refs: Mapped[dict] = mapped_column(JSONB, default=dict)
    skill_ids: Mapped[list] = mapped_column(JSONB, default=list)
    gap_type: Mapped[str] = mapped_column(String(64), default='ObservedCriterionGap')
    target_behavior: Mapped[str] = mapped_column(Text)
    benchmark_version: Mapped[dict] = mapped_column(JSONB, default=dict)
    success_criteria: Mapped[dict] = mapped_column(JSONB, default=dict)
    success_criteria_version: Mapped[str] = mapped_column(String(64))
    evidence_status: Mapped[str] = mapped_column(String(32), default='NEEDS_DIAGNOSIS')
    status: Mapped[str] = mapped_column(String(24), default='ACTIVE')
    plan_revision: Mapped[int] = mapped_column(Integer, default=1)
    policy_version: Mapped[str] = mapped_column(String(64), default='learning-feedback-v1')
    verification: Mapped[dict] = mapped_column(JSONB, default=dict)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LearningActivity(Base):
    __tablename__ = 'learning_activities'
    __table_args__ = (UniqueConstraint('goal_id', 'content_version', name='uq_learning_activity_content'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey('learning_goals.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default='READY')
    position: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_version: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    question_id: Mapped[int | None] = mapped_column(ForeignKey('questions.id', ondelete='RESTRICT'), nullable=True)
    latest_submission_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LearningSubmission(Base):
    __tablename__ = 'learning_submissions'
    __table_args__ = (UniqueConstraint('workspace_id', 'activity_id', 'request_key', name='uq_learning_submission_request'),
                     UniqueConstraint('activity_id', 'revision', name='uq_learning_submission_revision'))
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey('learning_goals.id', ondelete='CASCADE'), index=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey('learning_activities.id', ondelete='CASCADE'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    parent_submission_id: Mapped[int | None] = mapped_column(ForeignKey('learning_submissions.id', ondelete='SET NULL'), nullable=True)
    request_key: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    assistance: Mapped[dict] = mapped_column(JSONB, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64))
    activity_version: Mapped[int] = mapped_column(Integer)
    exam_id: Mapped[int | None] = mapped_column(ForeignKey('exams.id', ondelete='SET NULL', deferrable=True, initially='DEFERRED'), nullable=True, unique=True)
    answer_revision_id: Mapped[int | None] = mapped_column(ForeignKey('exam_answer_revisions.id', ondelete='SET NULL', deferrable=True, initially='DEFERRED'), nullable=True)
    scoring_run_id: Mapped[int | None] = mapped_column(ForeignKey('assessment_scoring_runs.id', ondelete='SET NULL', deferrable=True, initially='DEFERRED'), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(24), default='QUEUED')
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
