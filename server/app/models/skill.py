"""能力域 / 岗位标准 / 技能状态。

SkillState 遵循 PRD: estimated_level ∈ [0,5] 或 NULL(未知),
confidence ∈ [0,1], status ∈ UNKNOWN|NEEDS_VALIDATION|BELOW_TARGET|MEETS_TARGET|ABOVE_TARGET。
UNKNOWN ≠ 0: 未知是没有证据, 0 是有证据的低水平。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Skill(Base):
    """技能节点。首期一层: 8 个能力域; 结构上支持父子树(parent_id)。"""

    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)  # agent / tools / memory / rag / eval / backend / reliability / deploy
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"), nullable=True)
    # 维度分解: 理论理解 / 工程实践 / 问题诊断 / 表达沟通 / AI 协作
    dimensions: Mapped[list | None] = mapped_column(JSONB, nullable=True)


class JobStandard(Base):
    """岗位标准(对标基准): 某岗位某职级的各能力域目标等级与权重。

    weight_pct 之和应为 100。PRD 种子: agent18/tools14/memory12/rag10/eval16/backend12/reliability12/deploy6。
    """

    __tablename__ = "job_standards"
    __table_args__ = (UniqueConstraint("role", "level", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(160))
    level: Mapped[str] = mapped_column(String(16))
    version: Mapped[str] = mapped_column(String(32), default="v1.0")
    jd_sample_count: Mapped[int] = mapped_column(Integer, default=3)
    is_seed: Mapped[bool] = mapped_column(default=False)  # 种子假设, 非统计结论
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    targets: Mapped[list["JobStandardTarget"]] = relationship(back_populates="standard", lazy="selectin", cascade="all, delete-orphan")


class JobStandardTarget(Base):
    __tablename__ = "job_standard_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    standard_id: Mapped[int] = mapped_column(ForeignKey("job_standards.id", ondelete="CASCADE"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    weight_pct: Mapped[int] = mapped_column(Integer)
    target_level: Mapped[float] = mapped_column(Float)  # 0-5

    standard: Mapped[JobStandard] = relationship(back_populates="targets")
    skill: Mapped[Skill] = relationship(lazy="joined")


class SkillState(Base):
    """工作空间级的技能估计状态(Competency Engine 的输出快照)。"""

    __tablename__ = "skill_states"
    __table_args__ = (UniqueConstraint("workspace_id", "skill_id"),)

    STATUS_UNKNOWN = "UNKNOWN"
    STATUS_NEEDS_VALIDATION = "NEEDS_VALIDATION"
    STATUS_BELOW_TARGET = "BELOW_TARGET"
    STATUS_MEETS_TARGET = "MEETS_TARGET"
    STATUS_ABOVE_TARGET = "ABOVE_TARGET"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"))
    estimated_level: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL = UNKNOWN
    confidence: Mapped[float] = mapped_column(Float, default=0.0)                # 0-1
    evidence_coverage: Mapped[int] = mapped_column(Integer, default=0)           # 支撑证据条数
    status: Mapped[str] = mapped_column(String(32), default=STATUS_UNKNOWN)
    # 维度分解分数(理论理解/工程实践/问题诊断/表达沟通/AI 协作)
    dimension_scores: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    last_assessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_version: Mapped[str] = mapped_column(String(32), default="rule-v1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    skill: Mapped[Skill] = relationship(lazy="joined")
