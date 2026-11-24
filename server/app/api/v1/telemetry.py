"""产品遥测 API: 无感匿名事件收集 + 所有者汇总(用户数/功能使用/地域分布)。

设计原则:
- 无感: 前端静默上报(sendBeacon), 不打扰用户, 失败零影响
- 无 PII: 只收匿名 client_id / 事件名 / 页面路径 / 设备 / locale / 时区;
  不存 IP 原文(只存 sha256 用于防刷), 不存任何消息内容或用户名
- 全局表: 与工作空间业务数据隔离, 跨用户汇总
- 汇总仅本地所有者可见(require_local_owner)
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_local_owner
from app.db import get_session
from app.models import TelemetryEvent

router = APIRouter()

# ---------- 匿名事件写入(无认证, 限流) ----------

_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_DEVICES = {"mobile", "tablet", "desktop", "unknown"}

# 简单的进程内滑动窗口限流: ip -> [(ts, count)] (60s 窗口)
_RATE: dict[str, list[float]] = defaultdict(list)
_RATE_MAX = 90  # 每 IP 每分钟最多 90 条事件(页面/功能埋点足够)
_RATE_WINDOW = 60.0


class TelemetryItem(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    page: str | None = Field(default=None, max_length=255)
    props: dict | None = None


class TelemetryBatch(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)
    events: list[TelemetryItem] = Field(min_length=1, max_length=50)
    locale: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=64)
    device: str | None = Field(default=None, max_length=16)
    viewport: str | None = Field(default=None, max_length=24)


def _region_for_timezone(tz: str | None) -> str | None:
    """由 IANA 时区推导粗粒度地域(客户端上报, 无 IP 地理库依赖)。"""
    if not tz:
        return None
    known = {
        "Asia/Shanghai": "中国大陆", "Asia/Chongqing": "中国大陆", "Asia/Harbin": "中国大陆", "Asia/Urumqi": "中国大陆",
        "Asia/Hong_Kong": "中国香港", "Asia/Macau": "中国澳门", "Asia/Taipei": "中国台湾",
        "Asia/Tokyo": "日本", "Asia/Seoul": "韩国", "Asia/Singapore": "新加坡", "Asia/Kuala_Lumpur": "马来西亚",
        "Asia/Bangkok": "泰国", "Asia/Jakarta": "印度尼西亚", "Asia/Manila": "菲律宾", "Asia/Ho_Chi_Minh": "越南",
        "Asia/Dubai": "中东", "Asia/Kolkata": "印度",
        "Europe/London": "英国", "Europe/Paris": "法国", "Europe/Berlin": "德国", "Europe/Amsterdam": "荷兰",
        "Europe/Stockholm": "瑞典", "Europe/Zurich": "瑞士", "Europe/Dublin": "爱尔兰", "Europe/Madrid": "西班牙",
        "Europe/Rome": "意大利", "Europe/Moscow": "俄罗斯",
        "America/Los_Angeles": "美国", "America/New_York": "美国", "America/Chicago": "美国", "America/Denver": "美国",
        "America/Phoenix": "美国", "America/Seattle": "美国", "America/Toronto": "加拿大", "America/Vancouver": "加拿大",
        "Australia/Sydney": "澳大利亚", "Australia/Melbourne": "澳大利亚", "Australia/Perth": "澳大利亚",
        "Pacific/Auckland": "新西兰",
    }
    if tz in known:
        return known[tz]
    if tz.startswith("America/"):
        return "美洲"
    if tz.startswith("Europe/"):
        return "欧洲"
    if tz.startswith("Africa/"):
        return "非洲"
    if tz.startswith("Australia/") or tz.startswith("Pacific/"):
        return "大洋洲"
    if tz.startswith("Asia/"):
        return "亚洲"
    return tz


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    window = _RATE[ip]
    _RATE[ip] = [ts for ts in window if now - ts < _RATE_WINDOW]
    if len(_RATE[ip]) >= _RATE_MAX:
        return True
    _RATE[ip].append(now)
    return False


@router.post("/telemetry/events", status_code=204)
async def collect_telemetry(
    batch: TelemetryBatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """无感收集一批匿名事件。不认证(登录前页面也需要上报), 每 IP 限流。"""
    if not _CLIENT_ID.match(batch.client_id):
        raise HTTPException(status_code=422, detail="invalid client_id")
    if _rate_limited(_client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limited")

    ip_hash = hashlib.sha256(_client_ip(request).encode()).hexdigest()
    region = _region_for_timezone(batch.timezone)
    rows = []
    for item in batch.events:
        if not _EVENT_NAME.match(item.name):
            raise HTTPException(status_code=422, detail=f"invalid event name: {item.name}")
        rows.append(
            TelemetryEvent(
                client_id=batch.client_id,
                event=item.name,
                page=item.page,
                props=item.props,
                device=(batch.device or "unknown") if batch.device in _DEVICES else "unknown",
                locale=batch.locale,
                timezone=batch.timezone,
                region=region,
                ip_hash=ip_hash,
            )
        )
    session.add_all(rows)
    await session.commit()
    return None


# ---------- 所有者汇总(require_local_owner) ----------

_DAY = func.date_trunc("day", func.timezone("Asia/Shanghai", TelemetryEvent.created_at))


@router.get("/telemetry/summary")
async def telemetry_summary(
    _owner=Depends(require_local_owner),
    session: AsyncSession = Depends(get_session),
):
    """所有者仪表: 用户数(总/日活/周活/月活)、事件分布、页面、设备、地域、趋势。"""
    total_users = await session.scalar(select(func.count(func.distinct(TelemetryEvent.client_id)))) or 0
    total_events = await session.scalar(select(func.count(TelemetryEvent.id))) or 0
    now_utc = datetime.now(timezone.utc)

    def distinct_since(days: int):
        cutoff = now_utc - timedelta(days=days)
        return select(func.count(func.distinct(TelemetryEvent.client_id))).where(
            TelemetryEvent.created_at >= cutoff
        )

    active_1d = await session.scalar(distinct_since(1)) or 0
    active_7d = await session.scalar(distinct_since(7)) or 0
    active_30d = await session.scalar(distinct_since(30)) or 0

    top_events = (await session.execute(
        select(TelemetryEvent.event, func.count(TelemetryEvent.id).label("n"))
        .group_by(TelemetryEvent.event).order_by(func.count(TelemetryEvent.id).desc()).limit(12)
    )).all()
    top_pages = (await session.execute(
        select(TelemetryEvent.page, func.count(TelemetryEvent.id).label("n"))
        .where(TelemetryEvent.event == "page_view", TelemetryEvent.page.is_not(None))
        .group_by(TelemetryEvent.page).order_by(func.count(TelemetryEvent.id).desc()).limit(12)
    )).all()
    devices = (await session.execute(
        select(TelemetryEvent.device, func.count(TelemetryEvent.id).label("n"))
        .group_by(TelemetryEvent.device).order_by(func.count(TelemetryEvent.id).desc())
    )).all()
    regions = (await session.execute(
        select(TelemetryEvent.region, func.count(TelemetryEvent.id).label("n"))
        .where(TelemetryEvent.region.is_not(None))
        .group_by(TelemetryEvent.region).order_by(func.count(TelemetryEvent.id).desc())
    )).all()
    locales = (await session.execute(
        select(TelemetryEvent.locale, func.count(TelemetryEvent.id).label("n"))
        .where(TelemetryEvent.locale.is_not(None))
        .group_by(TelemetryEvent.locale).order_by(func.count(TelemetryEvent.id).desc())
    )).all()

    # 近 14 天每日: 事件数 + 活跃用户数(Asia/Shanghai 自然日)
    trend_rows = (await session.execute(
        select(
            _DAY.label("day"),
            func.count(TelemetryEvent.id).label("events"),
            func.count(func.distinct(TelemetryEvent.client_id)).label("users"),
        )
        .where(TelemetryEvent.created_at >= now_utc - timedelta(days=13))
        .group_by(_DAY).order_by(_DAY)
    )).all()
    trend = [
        {"date": str(row.day)[:10], "events": row.events, "users": row.users}
        for row in trend_rows
    ]

    return {
        "window": "all",
        "users": {"total": total_users, "active_1d": active_1d, "active_7d": active_7d, "active_30d": active_30d},
        "events_total": total_events,
        "top_events": [{"name": r[0], "count": r[1]} for r in top_events],
        "top_pages": [{"page": r[0], "count": r[1]} for r in top_pages],
        "devices": [{"device": r[0] or "unknown", "count": r[1]} for r in devices],
        "regions": [{"region": r[0], "count": r[1]} for r in regions],
        "locales": [{"locale": r[0], "count": r[1]} for r in locales],
        "trend": trend,
    }
