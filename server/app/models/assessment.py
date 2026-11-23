"""Durable per-question grading and append-only answer revisions."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AnswerRevision(Base):
    __tablename__='exam_answer_revisions'
    __table_args__=(UniqueConstraint('exam_question_id','revision',name='uq_answer_revision'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    exam_question_id: Mapped[int]=mapped_column(ForeignKey('exam_questions.id',ondelete='CASCADE'))
    revision: Mapped[int]=mapped_column(Integer)
    payload: Mapped[dict]=mapped_column(JSONB)
    content_hash: Mapped[str]=mapped_column(String(64))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())


class ScoringRun(Base):
    __tablename__='assessment_scoring_runs'
    __table_args__=(UniqueConstraint('exam_id','generation',name='uq_scoring_exam_generation'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    exam_id: Mapped[int]=mapped_column(ForeignKey('exams.id',ondelete='CASCADE'),index=True)
    workspace_id: Mapped[int]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    generation: Mapped[int]=mapped_column(Integer,default=1)
    mode: Mapped[str]=mapped_column(String(16),default='OFFICIAL')
    status: Mapped[str]=mapped_column(String(32),default='QUEUED')
    judge_version: Mapped[str]=mapped_column(String(32))
    model_fingerprint: Mapped[str]=mapped_column(String(64))
    input_hash: Mapped[str]=mapped_column(String(64))
    trace_id: Mapped[int | None]=mapped_column(ForeignKey('traces.id',ondelete='SET NULL'),nullable=True)
    error_category: Mapped[str | None]=mapped_column(String(48),nullable=True)
    error: Mapped[str | None]=mapped_column(Text,nullable=True)
    report: Mapped[dict | None]=mapped_column(JSONB,nullable=True)
    meta: Mapped[dict]=mapped_column(JSONB,default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    completed_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)


class ScoringStep(Base):
    __tablename__='assessment_scoring_steps'
    __table_args__=(UniqueConstraint('run_id','exam_question_id',name='uq_scoring_run_question'),)
    id: Mapped[int]=mapped_column(primary_key=True)
    run_id: Mapped[int]=mapped_column(ForeignKey('assessment_scoring_runs.id',ondelete='CASCADE'),index=True)
    exam_question_id: Mapped[int]=mapped_column(ForeignKey('exam_questions.id',ondelete='CASCADE'))
    order_no: Mapped[int]=mapped_column(Integer)
    status: Mapped[str]=mapped_column(String(24),default='QUEUED')
    input_hash: Mapped[str]=mapped_column(String(64))
    input_json: Mapped[dict]=mapped_column(JSONB)
    result_json: Mapped[dict | None]=mapped_column(JSONB,nullable=True)
    attempts: Mapped[list]=mapped_column(JSONB,default=list)
    attempt_count: Mapped[int]=mapped_column(Integer,default=0)
    retry_count: Mapped[int]=mapped_column(Integer,default=0)
    repair_count: Mapped[int]=mapped_column(Integer,default=0)
    lease_owner: Mapped[str | None]=mapped_column(String(120),nullable=True)
    lease_expires_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    fencing_token: Mapped[int]=mapped_column(Integer,default=0)
    next_retry_at: Mapped[datetime | None]=mapped_column(DateTime(timezone=True),nullable=True)
    error_category: Mapped[str | None]=mapped_column(String(48),nullable=True)
    error: Mapped[str | None]=mapped_column(Text,nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now())
