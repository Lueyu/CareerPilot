"""学习任务与延迟复测。

任务类型: UNDERSTAND(理解) | PRACTICE(实践) | RETEST(复测)。
复测遵循 1/3/7 天间隔; 任务由 Learning Planner 从差距生成, 带因果说明(为什么学)。
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class LearningTask(Base):
    __tablename__ = "learning_tasks"

    KIND_UNDERSTAND = "UNDERSTAND"
    KIND_PRACTICE = "PRACTICE"
    KIND_RETEST = "RETEST"

    STATUS_PENDING = "PENDING"
    STATUS_IN_PROGRESS = "IN_PROGRESS"
    STATUS_DONE = "DONE"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    # 为什么学: 关联差距/证据的因果说明
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 引用的证据/差距 ID
    # 任务内容: steps / principles / verify / acceptance
    content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    est_minutes: Mapped[int] = mapped_column(Integer, default=25)
    acceptance: Mapped[str | None] = mapped_column(Text, nullable=True)      # 完成标准
    # 复测调度: 生成后 1/3/7 天的复测链
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    retest_of: Mapped[int | None] = mapped_column(ForeignKey("learning_tasks.id"), nullable=True)
    retest_round: Mapped[int] = mapped_column(Integer, default=0)  # 0=主任务, 1/2/3=复测轮次
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    skill = relationship("Skill", lazy="joined")
