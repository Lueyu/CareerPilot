"""工业级 agent 观测 API: 云服务指标汇总(仅本地所有者可见, 不对普通用户体现)。

数据源:
- telemetry_events   业务/地域/设备/页面(匿名, 无 PII)
- agent_metrics      每次 LLM 调用的工程/质量/成本指标
- study_sessions / study_messages   业务量与回复质量(中断/失败/重试)
- trace_spans        引擎运行成功率与成本

设计: 全局聚合(跨工作空间), 不隶属某个 workspace; 由 owner token 访问,
既挂个人版 app.main 也挂公开版 public_main(云服务容器)。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_local_owner
from app.db import get_session
from app.models import AgentMetric, StudyMessage, StudySession, TelemetryEvent, Trace, TraceSpan

router = APIRouter()

_DAY = func.date_trunc("day", func.timezone("Asia/Shanghai", AgentMetric.created_at))


def _percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return int(ordered[index])


def _window_start(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


async def _telemetry_block(session: AsyncSession) -> dict:
    """业务指标: 用户/页面/功能/地域/设备/语言 (匿名, 去重 client_id)。"""
    summary: dict = {
        "users": {"total": 0, "active_1d": 0, "active_7d": 0, "active_30d": 0},
        "events_total": 0,
        "top_events": [],
        "top_pages": [],
        "devices": [],
        "regions": [],
        "locales": [],
    }
    try:
        summary["users"]["total"] = int(
            (await session.execute(select(func.count(func.distinct(TelemetryEvent.client_id))))).scalar() or 0
        )
        for label, days in (("active_1d", 1), ("active_7d", 7), ("active_30d", 30)):
            summary["users"][label] = int(
                (
                    await session.execute(
                        select(func.count(func.distinct(TelemetryEvent.client_id))).where(
                            TelemetryEvent.created_at >= _window_start(days)
                        )
                    )
                ).scalar()
                or 0
            )
        summary["events_total"] = int(
            (await session.execute(select(func.count(TelemetryEvent.id)))).scalar() or 0
        )

        def _top(rows, limit=12):
            return [{"name": name, "count": int(count)} for name, count in rows if name][:limit]

        events_rows = (
            await session.execute(
                select(TelemetryEvent.event, func.count(TelemetryEvent.id))
                .group_by(TelemetryEvent.event)
                .order_by(func.count(TelemetryEvent.id).desc())
                .limit(12)
            )
        ).all()
        summary["top_events"] = _top(events_rows)
        pages_rows = (
            await session.execute(
                select(TelemetryEvent.page, func.count(TelemetryEvent.id))
                .where(TelemetryEvent.event == "page_view", TelemetryEvent.page.is_not(None))
                .group_by(TelemetryEvent.page)
                .order_by(func.count(TelemetryEvent.id).desc())
                .limit(12)
            )
        ).all()
        summary["top_pages"] = _top(pages_rows)
        for field, column in (
            ("devices", TelemetryEvent.device),
            ("regions", TelemetryEvent.region),
            ("locales", TelemetryEvent.locale),
        ):
            rows = (
                await session.execute(
                    select(column, func.count(TelemetryEvent.id))
                    .where(column.is_not(None))
                    .group_by(column)
                    .order_by(func.count(TelemetryEvent.id).desc())
                    .limit(12)
                )
            ).all()
            summary[field] = _top(rows)
    except Exception:  # 表缺失时降级(个人库未开遥测)
        pass
    return summary


async def _agent_block(session: AsyncSession, days: int = 14) -> dict:
    """工程/质量/成本指标: AgentMetric 聚合。"""
    out: dict = {
        "calls": 0,
        "ok_rate": None,
        "errors": {},
        "latency_ms": {"p50": 0, "p95": 0, "p99": 0, "avg": 0},
        "ttft_ms": {"p50": None, "p95": None},
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
        "cost_estimate_cny": 0.0,
        "by_model": [],
        "finish_reasons": [],
        "stream_share": None,
    }
    try:
        metrics = (
            await session.execute(
                select(AgentMetric)
                .where(AgentMetric.provider.is_distinct_from("fixture"),
                       AgentMetric.created_at >= _window_start(days))
                .order_by(AgentMetric.id.desc()).limit(5000)
            )
        ).scalars().all()
    except Exception:
        return out
    total = len(metrics)
    if not total:
        return out
    ok = sum(1 for m in metrics if m.ok)
    durations = [int(m.duration_ms or 0) for m in metrics]
    ttfts = [int(m.ttft_ms) for m in metrics if m.ttft_ms is not None]
    out["calls"] = total
    out["ok_rate"] = round(100.0 * ok / total, 1)
    out["errors"] = dict(Counter(m.error_kind for m in metrics if m.error_kind).most_common(12))
    out["latency_ms"] = {
        "p50": _percentile(durations, 0.50),
        "p95": _percentile(durations, 0.95),
        "p99": _percentile(durations, 0.99),
        "avg": int(sum(durations) / total),
    }
    out["ttft_ms"] = {
        "p50": _percentile(ttfts, 0.50) if ttfts else None,
        "p95": _percentile(ttfts, 0.95) if ttfts else None,
    }
    out["tokens"] = {
        "prompt": sum(int(m.prompt_tokens or 0) for m in metrics),
        "completion": sum(int(m.completion_tokens or 0) for m in metrics),
        "total": sum(int(m.total_tokens or 0) for m in metrics),
    }
    out["cost_estimate_cny"] = round(sum(float(m.cost_cny or 0) for m in metrics), 4)
    out["finish_reasons"] = [
        {"name": name, "count": int(count)}
        for name, count in Counter(m.finish_reason for m in metrics if m.finish_reason).most_common(8)
    ]
    out["stream_share"] = round(100.0 * sum(1 for m in metrics if m.stream) / total, 1)
    by_model: dict = defaultdict(lambda: {"calls": 0, "errors": 0, "total_ms": 0, "tokens": 0, "cost": 0.0})
    for m in metrics:
        bucket = by_model[m.model or "unknown"]
        bucket["calls"] += 1
        if not m.ok:
            bucket["errors"] += 1
        bucket["total_ms"] += int(m.duration_ms or 0)
        bucket["tokens"] += int(m.total_tokens or 0)
        bucket["cost"] += float(m.cost_cny or 0)
    out["by_model"] = [
        {
            "model": name,
            "calls": b["calls"],
            "errors": b["errors"],
            "avg_ms": int(b["total_ms"] / b["calls"]) if b["calls"] else 0,
            "tokens": b["tokens"],
            "cost_cny": round(b["cost"], 4),
        }
        for name, b in sorted(by_model.items(), key=lambda kv: -kv[1]["calls"])
    ][:10]
    return out


async def _quality_block(session: AsyncSession) -> dict:
    """回复质量: 会话/消息量, 中断/失败/重试率。"""
    out: dict = {
        "sessions": 0,
        "messages": 0,
        "assistant": {"completed": 0, "failed": 0, "interrupted": 0, "generating": 0},
        "failure_rate": None,
        "retry_rate": None,
    }
    try:
        out["sessions"] = int((await session.execute(select(func.count(StudySession.id)))).scalar() or 0)
        out["messages"] = int((await session.execute(select(func.count(StudyMessage.id)))).scalar() or 0)
        rows = (
            await session.execute(
                select(StudyMessage.status, func.count(StudyMessage.id))
                .where(StudyMessage.role == "assistant")
                .group_by(StudyMessage.status)
            )
        ).all()
        for status, count in rows:
            out["assistant"][status] = int(count)
        assist = out["assistant"]
        answered = assist["completed"] + assist["failed"] + assist["interrupted"]
        if answered:
            out["failure_rate"] = round(100.0 * assist["failed"] / answered, 1)
        retried = int(
            (
                await session.execute(
                    select(func.count(StudyMessage.id)).where(
                        StudyMessage.role == "assistant", StudyMessage.attempt > 1
                    )
                )
            ).scalar()
            or 0
        )
        if answered:
            out["retry_rate"] = round(100.0 * retried / answered, 1)
    except Exception:
        pass
    return out


async def _engine_block(session: AsyncSession) -> dict:
    """引擎运行成功率(最近 500 条 trace 与 span)。"""
    out: dict = {"traces": 0, "success_rate": None, "spans": 0, "span_error_rate": None}
    try:
        traces = (
            await session.execute(select(Trace).order_by(Trace.id.desc()).limit(500))
        ).scalars().all()
        out["traces"] = len(traces)
        if traces:
            success = sum(1 for t in traces if t.status == "SUCCESS")
            out["success_rate"] = round(100.0 * success / len(traces), 1)
        spans = (
            await session.execute(select(TraceSpan).order_by(TraceSpan.id.desc()).limit(2000))
        ).scalars().all()
        out["spans"] = len(spans)
        if spans:
            errors = sum(1 for s in spans if s.status == "ERROR")
            out["span_error_rate"] = round(100.0 * errors / len(spans), 1)
    except Exception:
        pass
    return out


async def _trend_block(session: AsyncSession, days: int = 14) -> list[dict]:
    """统计窗口内: 调用数 + 成本 + 用户数(遥测)。"""
    try:
        rows = (
            await session.execute(
                select(
                    _DAY.label("day"),
                    func.count(AgentMetric.id),
                    func.sum(AgentMetric.cost_cny),
                )
                .where(AgentMetric.created_at >= _window_start(days),
                       AgentMetric.provider.is_distinct_from("fixture"))
                .group_by(_DAY)
                .order_by(_DAY)
            )
        ).all()
    except Exception:
        return []
    by_day = {
        str(day)[:10]: {"date": str(day)[:10], "calls": int(calls), "cost_cny": round(float(cost or 0), 4), "users": 0}
        for day, calls, cost in rows
    }
    try:
        user_rows = (
            await session.execute(
                select(
                    func.date_trunc("day", func.timezone("Asia/Shanghai", TelemetryEvent.created_at)).label("day"),
                    func.count(func.distinct(TelemetryEvent.client_id)),
                )
                .where(TelemetryEvent.created_at >= _window_start(days))
                .group_by(func.date_trunc("day", func.timezone("Asia/Shanghai", TelemetryEvent.created_at)))
            )
        ).all()
        for day, users in user_rows:
            key = str(day)[:10]
            if key in by_day:
                by_day[key]["users"] = int(users)
    except Exception:
        pass
    return list(by_day.values())


@router.get("/ops/metrics/summary")
async def ops_summary(
    days: int = Query(14, ge=1, le=90),
    session: AsyncSession = Depends(get_session),
    _owner=Depends(require_local_owner),
):
    """云服务指标汇总: 业务 / 工程 / 质量 / 成本 / 趋势。仅本地所有者可见。"""
    business = await _telemetry_block(session)
    agent = await _agent_block(session, days)
    quality = await _quality_block(session)
    engine = await _engine_block(session)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "sample_count": agent["calls"],
        "agent_denominator": "每次模型调用",
        "quality_denominator": "每条助手回复",
        "business": business,
        "agent": agent,
        "quality": quality,
        "engine": engine,
        "trend": await _trend_block(session, days),
    }


@router.get("/ops/metrics/recent")
async def ops_recent(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    _owner=Depends(require_local_owner),
):
    """最近调用明细(drill-down)。"""
    rows = (
        await session.execute(
            select(AgentMetric).where(AgentMetric.provider.is_distinct_from("fixture"))
            .order_by(AgentMetric.id.desc()).limit(limit)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": m.id,
                "kind": m.kind,
                "model": m.model,
                "provider": m.provider,
                "duration_ms": m.duration_ms,
                "ttft_ms": m.ttft_ms,
                "total_tokens": m.total_tokens,
                "cost_cny": m.cost_cny,
                "ok": m.ok,
                "error_kind": m.error_kind,
                "finish_reason": m.finish_reason,
                "stream": m.stream,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]
    }


@router.get("/ops/health")
async def ops_health(
    session: AsyncSession = Depends(get_session),
    _owner=Depends(require_local_owner),
):
    """服务健康: 数据库 / 模型配置 / 工作进程心跳。"""
    db_ok = True
    try:
        await session.execute(select(1))
    except Exception:
        db_ok = False
    worker = {"last_seen": None, "age_seconds": None, "alive": False}
    try:
        from app.models.project_analysis import WorkerHeartbeat

        beat = (
            await session.execute(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1))
        ).scalar_one_or_none()
        if beat is not None and beat.last_seen_at is not None:
            seen = beat.last_seen_at
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            age = int((datetime.now(timezone.utc) - seen).total_seconds())
            worker = {"last_seen": seen.isoformat(), "age_seconds": age, "alive": age < 120}
    except Exception:
        pass
    llm = {"configured": False, "model": None}
    try:
        from app.services.llm import get_llm_config

        cfg = await get_llm_config()
        llm = {"configured": bool(cfg), "model": (cfg or {}).get("model")}
    except Exception:
        pass
    return {"ok": db_ok, "database": db_ok, "llm": llm, "worker": worker}
