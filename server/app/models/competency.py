"""能力画像历史快照。

Competency Engine 每次全量重算后落一份快照, 用于:
- 画像趋势(仪表盘"近期进展"时间线 / 差距缩小率曲线);
- Evolution 实验的 before/after 对比(加权差距、覆盖、置信度);
- 审计: 任何时点的画像可复现。

snapshot 不替代 skill_states(实时态), 而是其追加式历史。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class CompetencySnapshot(Base):
    """一次全量重算后的画像快照(工作空间级, 追加式)。"""

    __tablename__ = "competency_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    # 触发来源: exam_completed / import_approved / evidence_added / manual / evolution_eval
    trigger: Mapped[str] = mapped_column(String(48), default="manual")
    trigger_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. TR-1042 / E-12
    # 全域汇总
    weighted_gap: Mapped[float | None] = mapped_column(Float, nullable=True)     # 加权差距(目标-估计, 0-5)
    overall_level: Mapped[float | None] = mapped_column(Float, nullable=True)    # 加权平均估计
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    avg_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 各技能逐条: [{skill_key, estimated_level, confidence, status, evidence_coverage, target_level, weight_pct}]
    skills: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    model_version: Mapped[str] = mapped_column(String(32), default="rule-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
