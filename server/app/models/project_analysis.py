"""Immutable, versioned repository understanding; never a competency record."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ProjectAnalysis(Base):
    __tablename__ = 'project_analyses'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_key', name='uq_project_analysis_request'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    request_key: Mapped[str] = mapped_column(String(120))
    focus: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(16), default='READY')
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default='full')
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    pipeline_version: Mapped[str] = mapped_column(String(64), default='legacy')
    prompt_bundle_version: Mapped[str] = mapped_column(String(64), default='legacy')
    model_config_fingerprint: Mapped[str] = mapped_column(String(64), default='')
    budget: Mapped[dict] = mapped_column(JSONB, default=dict)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    coverage_status: Mapped[str] = mapped_column(String(16), default='UNASSESSED')
    report_status: Mapped[str] = mapped_column(String(16), default='NONE')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    report_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnalysisStep(Base):
    __tablename__ = 'project_analysis_steps'
    __table_args__ = (UniqueConstraint('run_id', 'step_key', name='uq_project_step_key'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('project_analyses.id', ondelete='CASCADE'), index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id', ondelete='CASCADE'), index=True)
    step_key: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(64))
    module_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires: Mapped[list] = mapped_column(JSONB, default=list)
    input_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64))
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default='QUEUED', index=True)
    uses_model: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    error_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    attempts: Mapped[list] = mapped_column(JSONB, default=list)


class WorkerHeartbeat(Base):
    __tablename__ = 'project_worker_heartbeats'
    worker_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    current_step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
