"""工业级 agent 观测: 每次 LLM 调用一行指标(工程/质量/成本维度)。

- AgentMetric: AgentAdapter 每次 chat_with 尽力而为记录(全局聚合, workspace 可空,
  供 /api/v1/ops/* 汇总; 不阻断主流程, 失败静默)。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AgentMetric(Base):
    __tablename__ = "agent_metrics"
    __table_args__ = (
        Index("ix_agent_metrics_created_at", "created_at"),
        Index("ix_agent_metrics_model_created", "model", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 调用来源: llm:<model> / study_chat.generate / exam.score / ... (span_name)
    kind: Mapped[str] = mapped_column(String(48), default="llm")
    provider: Mapped[str | None] = mapped_column(String(48), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    ttft_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 首字节/首 token 延迟
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_basis: Mapped[str] = mapped_column(String(32), default="unknown")
    billing_call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error_kind: Mapped[str | None] = mapped_column(String(48), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stream: Mapped[bool] = mapped_column(Boolean, default=False)
    # 全局观测: 不强制归属某工作空间(个人版=personal, 公开版跨账号聚合)
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
