"""Evolution Engine 模型: 策略候选 + 候选评估运行。

版本状态机(任务书第八阶段):
PROPOSED → TESTING → EVALUATED → APPROVED → ACTIVE → (回滚) ROLLED_BACK
                                   ↘ REJECTED

安全要求: 样本不足=INSUFFICIENT_EVIDENCE; 默认人工批准; 全部可回滚。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

EVO_STATUS_PROPOSED = "PROPOSED"
EVO_STATUS_TESTING = "TESTING"
EVO_STATUS_EVALUATED = "EVALUATED"
EVO_STATUS_APPROVED = "APPROVED"
EVO_STATUS_ACTIVE = "ACTIVE"
EVO_STATUS_REJECTED = "REJECTED"
EVO_STATUS_ROLLED_BACK = "ROLLED_BACK"


class EvolutionCandidate(Base):
    """一次策略改进候选(kind: exam_selection/rubric/learning/teaching)。

    body 承载策略版本内容本身(baseline/candidate 参数), 等价于 strategy_versions。
    """

    __tablename__ = "evolution_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # EVO-xxxx
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="exam_selection")
    status: Mapped[str] = mapped_column(String(24), default=EVO_STATUS_PROPOSED, index=True)
    body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)    # {baseline: {...}, candidate: {...}}
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)  # 为什么提出(引用真实数据)
    source_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 触发候选的真实数据摘要
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    runs = relationship("EvolutionRun", back_populates="candidate", cascade="all, delete-orphan", order_by="EvolutionRun.id.desc()")


class EvolutionRun(Base):
    """一次候选策略评估: 基线 vs 候选真实指标, 样本量与回归记录。"""

    __tablename__ = "evolution_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("evolution_candidates.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)     # {name: {baseline, candidate, delta}}
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    regression: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # {metric: drop} 或 None
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)  # POSITIVE / NEGATIVE / INSUFFICIENT_EVIDENCE
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    candidate = relationship("EvolutionCandidate", back_populates="runs")
