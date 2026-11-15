"""证据底座。

一切能力结论必须能回溯到证据。evidence_kind:
- EXAM_ANSWER   考试作答
- PROJECT_COMMIT 项目扫描
- MEMORY_NOTE   记忆中心确认的陈述
- IMPORTED_QA   面经题目
- EXTERNAL_LINK 外部材料
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # EV-031
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))          # EXAM_ANSWER / PROJECT_COMMIT / ...
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 证据等级: VERIFIED > HIGH > MEDIUM > LOW > UNVERIFIED_CLAIM
    grade: Mapped[str] = mapped_column(String(24), default="MEDIUM")
    # 对该技能的支撑强度 [-1, 1]: 正=支持高水平的证据, 负=暴露不足的证据
    support: Mapped[float] = mapped_column(Float, default=0.0)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 题目ID/commit/链接
    meta: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    skill = relationship("Skill", lazy="joined")
