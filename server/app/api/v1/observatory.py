"""系统观测台 API: 运行统计 / Trace 列表与详情 / 决策记录 / 引擎健康 / 事件流。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import DecisionRecord, Event, Evidence, Trace, TraceSpan, Workspace

router = APIRouter()

# 引擎注册表(健康状态由最近 trace 推断)
ENGINES = [
    {"key": "project_autopilot", "name": "Project Autopilot", "desc": "项目自动分析与可恢复阶段", "trace_kind": "project_autopilot"},
    {"key": "job_intelligence", "name": "Job Intelligence", "desc": "JD 解析与岗位画像", "trace_kind": "benchmark"},
    {"key": "memory_engine", "name": "Memory Engine", "desc": "记忆提取与生命周期", "trace_kind": "bank_import"},
    {"key": "project_analyzer", "name": "Project Analyzer", "desc": "项目证据扫描", "trace_kind": "project_scan"},
    {"key": "competency_engine", "name": "Competency Engine", "desc": "能力估计与状态判定", "trace_kind": "diagnosis_scoring"},
    {"key": "learning_planner", "name": "Learning Planner", "desc": "学习任务编排", "trace_kind": "diagnosis_scoring"},
]


@router.get("/observatory")
async def observatory(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    traces = (
        await session.execute(
            select(Trace).where(Trace.workspace_id == ws.id).order_by(Trace.id.desc()).limit(200)
        )
    ).scalars().all()
    total = len(traces)
    success = sum(1 for t in traces if t.status == "SUCCESS")
    failed = [t for t in traces if t.status == "FAILED"]
    durations = [t.duration_ms or 0 for t in traces]
    avg_latency = round(sum(durations) / total, 0) if total else 0
    cost = round(sum(t.cost_cny or 0 for t in traces), 2)

    return {
        "stats": {
            "total_runs": total,
            "success_rate": round(100 * success / total, 1) if total else 100.0,
            "avg_latency_ms": avg_latency,
            "cost_cny": None if any(t.kind=='project_autopilot' for t in traces) else cost,
            "failures": len(failed),
        },
        "engines": await _engines(session, ws, traces),
        "recent_failures": [
            {"short_code": t.short_code, "title": t.title, "fail_reason": t.fail_reason}
            for t in failed[:5]
        ],
    }


async def _engines(session: AsyncSession, ws: Workspace, traces: list[Trace]) -> list[dict]:
    out = []
    for eng in ENGINES:
        related = [t for t in traces if t.kind == eng["trace_kind"]]
        last = related[0] if related else None
        if not related:
            health, health_note = "IDLE", "尚未运行"
        elif any(t.status == "FAILED" for t in related[:3]):
            health, health_note = "DEGRADED", "最近运行存在失败"
        else:
            health, health_note = "HEALTHY", "运行正常"
        out.append(
            {
                **eng,
                "health": health,
                "health_note": health_note,
                "runs": len(related),
                "last_run": {
                    "short_code": last.short_code,
                    "title": last.title,
                    "status": last.status,
                    "finished_at": last.finished_at.isoformat() if last.finished_at else None,
                }
                if last
                else None,
            }
        )
    return out


@router.get("/observatory/traces")
async def list_traces(
    limit: int = 30,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    traces = (
        await session.execute(
            select(Trace).where(Trace.workspace_id == ws.id).order_by(Trace.id.desc()).limit(limit)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": t.id,
                "short_code": t.short_code,
                "kind": t.kind,
                "title": t.title,
                "status": t.status,
                "duration_ms": t.duration_ms,
                "cost_cny": None if t.kind=='project_autopilot' else t.cost_cny,
                "fail_reason": t.fail_reason,
                "started_at": t.started_at.isoformat() if t.started_at else None,
                "finished_at": t.finished_at.isoformat() if t.finished_at else None,
                "span_count": len(t.spans),
            }
            for t in traces
        ]
    }


@router.get("/observatory/traces/{trace_id}")
async def trace_detail(
    trace_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    t = (
        await session.execute(select(Trace).where(Trace.id == trace_id, Trace.workspace_id == ws.id))
    ).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return {
        "id": t.id,
        "short_code": t.short_code,
        "kind": t.kind,
        "title": t.title,
        "status": t.status,
        "duration_ms": t.duration_ms,
        "cost_cny": None if t.kind=='project_autopilot' else t.cost_cny,
        "fail_reason": t.fail_reason,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "spans": [
            {
                "id": s.id,
                "name": s.name,
                "status": s.status,
                "duration_ms": s.duration_ms,
                "cost_cny": None if t.kind=='project_autopilot' else s.cost_cny,
                "detail": s.detail,
            }
            for s in sorted(t.spans, key=lambda x: x.id)
        ],
    }


@router.get("/observatory/decisions")
async def list_decisions(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    dcs = (
        await session.execute(
            select(DecisionRecord).where(DecisionRecord.workspace_id == ws.id).order_by(DecisionRecord.id.desc())
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": d.id,
                "short_code": d.short_code,
                "question": d.question,
                "chosen": d.chosen,
                "alternatives": d.alternatives or [],
                "evidence_refs": d.evidence_refs or [],
                "rationale": d.rationale,
                "created_at": d.created_at.isoformat(),
            }
            for d in dcs
        ]
    }


@router.get("/observatory/events")
async def recent_events(
    limit: int = 30,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    events = (
        await session.execute(
            select(Event).where(Event.workspace_id == ws.id).order_by(Event.id.desc()).limit(limit)
        )
    ).scalars().all()
    return {
        "items": [
            {"id": e.id, "kind": e.kind, "payload": e.payload, "ref_type": e.ref_type, "ref_id": e.ref_id, "created_at": e.created_at.isoformat()}
            for e in events
        ]
    }


@router.get("/observatory/agents")
async def agent_runs(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """Agent Run 聚合: LLM 调用 span(llm:*) 按模型分组的调用次数/延迟/token/估算成本。"""
    rows = (
        await session.execute(
            select(TraceSpan, Trace.started_at)
            .join(Trace, TraceSpan.trace_id == Trace.id)
            .where(Trace.workspace_id == ws.id, TraceSpan.name.like("llm:%"))
            .order_by(TraceSpan.id.desc())
            .limit(500)
        )
    ).all()
    spans = [(s, started) for s, started in rows]
    groups: dict[str, dict] = {}
    for s, started_at in spans:
        d = s.detail or {}
        model = d.get("model") or (s.name.split(":", 1)[1] if ":" in s.name else "unknown")
        g = groups.setdefault(model, {"model": model, "provider": d.get("provider"), "calls": 0, "errors": 0, "total_ms": 0, "total_tokens": 0, "last_used": None})
        g["calls"] += 1
        if s.status == "ERROR":
            g["errors"] += 1
        g["total_ms"] += s.duration_ms or 0
        g["total_tokens"] += d.get("total_tokens") or 0
        if g["last_used"] is None and started_at is not None:
            g["last_used"] = started_at.isoformat()
    items = []
    for g in groups.values():
        calls = g.pop("calls")
        total_ms = g.pop("total_ms")
        tokens = g["total_tokens"]
        items.append({
            **g,
            "calls": calls,
            "avg_ms": round(total_ms / calls) if calls else 0,
            "estimated_cost": round(tokens * 2e-6, 4),
            "cost_note": "estimate ¥2/M tokens",
        })
    items.sort(key=lambda x: -x["calls"])
    return {"items": items, "note": "统计口径: TraceSpan(llm:*) · 最近 500 条 · 成本为估算值"}
