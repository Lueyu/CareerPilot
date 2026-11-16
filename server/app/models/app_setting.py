"""运行时配置 KV 表: 设置页在线修改的配置(LLM 连接、GitHub token 等), 优先于 env。"""
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AppSetting(Base):
    """key-value 运行时配置。

    已知 key:
    - 'llm'    → {provider, api_key, base_url, model}
    - 'github' → {token}
    密钥存本地 DB(单用户 local-first 部署); API 永不回显原文, 只给尾 4 位 hint。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
