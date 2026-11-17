"""记忆中心。

记忆类型: CareerGoal | Preference | KnowledgeGapSignal | ProjectDecision | Feedback。
状态: ACTIVE | SUPERSEDED(被纠正/替代, 但保留历史) | DISMISSED(候选被忽略)。
来源: DIALOG_IMPORT(导入对话提取的候选, 待用户确认) | SYSTEM(系统内行为沉淀) | USER(用户直接录入)。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class MemoryItem(Base):
    __tablename__ = "memory_items"

    STATUS_CANDIDATE = "CANDIDATE"      # 导入对话提取的候选, 待确认
    STATUS_ACTIVE = "ACTIVE"
    STATUS_SUPERSEDED = "SUPERSEDED"
    STATUS_DISMISSED = "DISMISSED"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    mtype: Mapped[str] = mapped_column(String(32))   # CareerGoal / Preference / ...
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(24), default="SYSTEM")
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 来源对话/事件
    extract_confidence: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=STATUS_ACTIVE)
    superseded_by: Mapped[int | None] = mapped_column(ForeignKey("memory_items.id"), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    freshness: Mapped[str] = mapped_column(String(24), default='CURRENT')
