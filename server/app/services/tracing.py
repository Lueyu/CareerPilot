"""trace / event 辅助: 引擎执行时落 Trace + Spans + 事件底座。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Trace, TraceSpan


async def start_trace(session: AsyncSession, workspace_id: int, kind: str, title: str, short_code: str) -> Trace:
    trace = Trace(workspace_id=workspace_id, kind=kind, title=title, short_code=short_code)
    session.add(trace)
    await session.flush()
    return trace


def add_span(session: AsyncSession, trace: Trace, name: str, duration_ms: int, cost_cny: float = 0.0, detail: dict | None = None, status: str = TraceSpan.STATUS_OK) -> TraceSpan:
    span = TraceSpan(trace_id=trace.id, name=name, duration_ms=duration_ms, cost_cny=cost_cny, detail=detail, status=status)
    session.add(span)
    return span


async def finish_trace(session: AsyncSession, trace: Trace, status: str, duration_ms: int, cost_cny: float = 0.0, fail_reason: str | None = None) -> Trace:
    trace.status = status
    trace.duration_ms = duration_ms
    trace.cost_cny = cost_cny
    trace.fail_reason = fail_reason
    trace.finished_at = datetime.now(timezone.utc)
    await session.flush()
    return trace


async def emit_event(session: AsyncSession, workspace_id: int, kind: str, payload: dict | None = None, ref_type: str | None = None, ref_id: int | None = None) -> Event:
    event = Event(workspace_id=workspace_id, kind=kind, payload=payload, ref_type=ref_type, ref_id=ref_id)
    session.add(event)
    await session.flush()
    return event
