"""JD(岗位描述)来源。

Benchmark / 岗位对标升级路径: 种子假设(v1.0) → 导入真实 JD 样本 → 提炼目标等级与权重。
这里只存原始 JD 与解析产物; 权重/目标的生成是 Benchmark 服务的职责(确定性规则 + 可选 LLM 摘要)。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class JdSource(Base):
    """一条导入的 JD 原文(支持粘贴文本或文件抽取结果)。"""

    __tablename__ = "jd_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))            # 公司/岗位名, 可后续编辑
    company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    role: Mapped[str | None] = mapped_column(String(160), nullable=True)
    level: Mapped[str | None] = mapped_column(String(16), nullable=True)   # P5/P6/... 可为空
    source_type: Mapped[str] = mapped_column(String(24), default="paste")  # paste | file | url
    raw_text: Mapped[str] = mapped_column(Text)                # JD 原文
    # 解析产物: {requirements:[...], keywords:[...], skill_hits:[{skill_key,hits}], hard_requirements:[...]}
    parsed: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 状态: RAW(未解析) | PARSED(已解析) | APPLIED(已生成对标版本)
    status: Mapped[str] = mapped_column(String(16), default="RAW")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
