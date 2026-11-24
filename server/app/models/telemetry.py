"""产品遥测: 匿名事件收集底座(无感、无 PII、不落用户内容)。

- 前端静默上报 page_view / 功能事件(匿名 client_id, 仅设备/locale/时区/路径)
- 全局表(不绑定 workspace): 跨用户产品分析, 与工作空间业务数据完全隔离
- region 由时区推导(粗粒度地域分布), 不解析/不存储 IP 原文
"""
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)   # 匿名 UUID(浏览器 localStorage)
    event: Mapped[str] = mapped_column(String(64), index=True)       # page_view / feature.*
    page: Mapped[str | None] = mapped_column(String(255), nullable=True)
    props: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    device: Mapped[str | None] = mapped_column(String(16), nullable=True)   # mobile/tablet/desktop
    locale: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)  # IANA, 如 Asia/Shanghai
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)    # 由时区推导的粗粒度地域
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)   # sha256(ip), 仅防刷
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
