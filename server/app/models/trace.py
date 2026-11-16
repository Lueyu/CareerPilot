"""系统观测台: 运行追踪 / 决策记录 / 事件底座。

- Trace: 一次 Agent/engine 执行(span 树 + 成本 + 延迟)
- Span: trace 内的步骤(含失败原因)
- DecisionRecord: Agent 为什么选了这个行动(候选+证据引用)
- Event: append-only 事件底座, 所有引擎输出的因果来源
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Trace(Base):
    __tablename__ = "traces"

    STATUS_RUNNING = "RUNNING"
    STATUS_SUCCESS = "SUCCESS"
    STATUS_FAILED = "FAILED"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # TR-1042
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(48))    # interview_import / diagnosis_scoring / project_scan ...
    title: Mapped[str] = mapped_column(String(255))
    # 触发来源: api(用户操作) / scheduler(定时) / system(内部) + 引用标识(如 E-12)
    trigger: Mapped[str | None] = mapped_column(String(24), nullable=True)
    trigger_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_RUNNING)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    fail_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    spans: Mapped[list["TraceSpan"]] = relationship(
        back_populates="trace", lazy="selectin", cascade="all, delete-orphan", order_by="TraceSpan.id"
    )


class TraceSpan(Base):
    __tablename__ = "trace_spans"

    STATUS_OK = "OK"
    STATUS_ERROR = "ERROR"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[int] = mapped_column(ForeignKey("traces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default=STATUS_OK)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_cny: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[str | None] = mapped_column(JSONB, nullable=True)

    trace: Mapped[Trace] = relationship(back_populates="spans")


class DecisionRecord(Base):
    """Agent 决策记录: chosen + alternatives + evidence_refs(可解释性)。"""

    __tablename__ = "decision_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # DC-204
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("traces.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text)      # 面临的决策问题
    chosen: Mapped[str] = mapped_column(Text)
    alternatives: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # [{option, reason}]
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    """append-only 事件底座: 所有引擎输出与用户行为的因果来源。"""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))    # exam.completed / evidence.created / ...
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
