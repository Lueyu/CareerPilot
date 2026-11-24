"""CareerPilot 云服务指标汇总 —— 独立后端容器服务。

只读同一 PostgreSQL：聚合 agent_metrics / telemetry_events / study_messages /
traces 等表，向所有者暴露指标汇总 JSON 与一个独立的 HTML 仪表盘。
不对终端用户暴露；用 OPS_TOKEN 保护。

运行：
    DATABASE_URL=postgresql://user:pass@host:5432/careerpilot OPS_TOKEN=xxx \
    uvicorn app:app --host 0.0.0.0 --port 8099
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPS_TOKEN = os.environ.get("OPS_TOKEN", "")

app = FastAPI(title="CareerPilot Ops Metrics", docs_url=None, openapi_url=None)


def _dsn() -> str:
    dsn = DATABASE_URL
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    if not dsn:
        raise HTTPException(503, "DATABASE_URL is not configured")
    return dsn


async def _connect() -> asyncpg.Connection:
    return await asyncpg.connect(_dsn(), timeout=10)


def _check(request: Request) -> None:
    if not OPS_TOKEN:
        return
    token = request.headers.get("x-ops-token") or request.query_params.get("token")
    if token != OPS_TOKEN:
        raise HTTPException(401, "ops token required")


def _pct(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    return int(values[min(len(values) - 1, int(round(ratio * (len(values) - 1))))])


@app.get("/healthz")
async def healthz() -> dict:
    try:
        conn = await _connect()
        await conn.execute("SELECT 1")
        await conn.close()
        return {"ok": True, "service": "careerpilot-ops"}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc


@app.get("/api/v1/ops/metrics")
async def metrics(request: Request, days: int = Query(14, ge=1, le=90)) -> dict:
    _check(request)
    conn = await _connect()
    try:
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)

        # ---- 业务（匿名遥测） ----
        users_total = await conn.fetchval("SELECT count(DISTINCT client_id) FROM telemetry_events") or 0
        users_1d = await conn.fetchval(
            "SELECT count(DISTINCT client_id) FROM telemetry_events WHERE created_at >= $1",
            now - timedelta(days=1)) or 0
        users_7d = await conn.fetchval(
            "SELECT count(DISTINCT client_id) FROM telemetry_events WHERE created_at >= $1",
            now - timedelta(days=7)) or 0
        users_30d = await conn.fetchval(
            "SELECT count(DISTINCT client_id) FROM telemetry_events WHERE created_at >= $1",
            now - timedelta(days=30)) or 0
        top_events = await conn.fetch(
            "SELECT event AS name, count(*) AS count FROM telemetry_events GROUP BY event ORDER BY count DESC LIMIT 12")
        top_pages = await conn.fetch(
            "SELECT page AS name, count(*) AS count FROM telemetry_events WHERE event='page_view' AND page IS NOT NULL "
            "GROUP BY page ORDER BY count DESC LIMIT 12")
        regions = await conn.fetch(
            "SELECT region AS name, count(*) AS count FROM telemetry_events WHERE region IS NOT NULL "
            "GROUP BY region ORDER BY count DESC LIMIT 12")

        # ---- 工程 / 质量 / 成本（agent_metrics） ----
        rows = await conn.fetch(
            "SELECT model, duration_ms, ttft_ms, prompt_tokens, completion_tokens, total_tokens, "
            "cost_cny, ok, error_kind, finish_reason, stream FROM agent_metrics "
            "ORDER BY id DESC LIMIT 5000")
        calls = len(rows)
        ok = sum(1 for r in rows if r["ok"])
        durations = [int(r["duration_ms"] or 0) for r in rows]
        ttfts = [int(r["ttft_ms"]) for r in rows if r["ttft_ms"] is not None]
        by_model: dict = defaultdict(lambda: {"calls": 0, "errors": 0, "total_ms": 0, "tokens": 0, "cost": 0.0})
        for r in rows:
            b = by_model[r["model"] or "unknown"]
            b["calls"] += 1
            b["errors"] += 0 if r["ok"] else 1
            b["total_ms"] += int(r["duration_ms"] or 0)
            b["tokens"] += int(r["total_tokens"] or 0)
            b["cost"] += float(r["cost_cny"] or 0)

        # ---- 质量（study_messages） ----
        msg_rows = await conn.fetch(
            "SELECT status, count(*) AS c FROM study_messages WHERE role='assistant' GROUP BY status")
        statuses = {r["status"]: int(r["c"]) for r in msg_rows}
        answered = statuses.get("completed", 0) + statuses.get("failed", 0) + statuses.get("interrupted", 0)
        sessions = await conn.fetchval("SELECT count(*) FROM study_sessions") or 0
        messages = await conn.fetchval("SELECT count(*) FROM study_messages") or 0
        retried = await conn.fetchval(
            "SELECT count(*) FROM study_messages WHERE role='assistant' AND attempt > 1") or 0

        # ---- 引擎（traces / trace_spans） ----
        traces = await conn.fetch("SELECT status FROM traces ORDER BY id DESC LIMIT 500")
        spans = await conn.fetch("SELECT status FROM trace_spans ORDER BY id DESC LIMIT 2000")

        # ---- 趋势 ----
        trend_rows = await conn.fetch(
            "SELECT date_trunc('day', timezone('Asia/Shanghai', created_at)) AS day, count(*) AS calls, "
            "sum(cost_cny) AS cost FROM agent_metrics WHERE created_at >= $1 GROUP BY day ORDER BY day", since)
        trend = [{"date": str(r["day"])[:10], "calls": int(r["calls"]),
                  "cost_cny": round(float(r["cost"] or 0), 4), "users": 0} for r in trend_rows]

        return {
            "generated_at": now.isoformat(),
            "business": {
                "users": {"total": int(users_total), "active_1d": int(users_1d),
                          "active_7d": int(users_7d), "active_30d": int(users_30d)},
                "top_events": [dict(r) for r in top_events],
                "top_pages": [dict(r) for r in top_pages],
                "regions": [dict(r) for r in regions],
            },
            "agent": {
                "calls": calls,
                "ok_rate": round(100.0 * ok / calls, 1) if calls else None,
                "errors": dict(Counter(r["error_kind"] for r in rows if r["error_kind"]).most_common(12)),
                "latency_ms": {"p50": _pct(durations, .5), "p95": _pct(durations, .95),
                               "p99": _pct(durations, .99),
                               "avg": int(sum(durations) / calls) if calls else 0},
                "ttft_ms": {"p50": _pct(ttfts, .5) if ttfts else None,
                            "p95": _pct(ttfts, .95) if ttfts else None},
                "tokens": {"prompt": sum(int(r["prompt_tokens"] or 0) for r in rows),
                           "completion": sum(int(r["completion_tokens"] or 0) for r in rows),
                           "total": sum(int(r["total_tokens"] or 0) for r in rows)},
                "cost_estimate_cny": round(sum(float(r["cost_cny"] or 0) for r in rows), 4),
                "by_model": [{"model": k, "calls": v["calls"], "errors": v["errors"],
                              "avg_ms": int(v["total_ms"] / v["calls"]),
                              "tokens": v["tokens"], "cost_cny": round(v["cost"], 4)}
                             for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["calls"])][:10],
                "finish_reasons": [{"name": name, "count": c} for name, c in
                                   Counter(r["finish_reason"] for r in rows if r["finish_reason"]).most_common(8)],
                "stream_share": round(100.0 * sum(1 for r in rows if r["stream"]) / calls, 1) if calls else None,
            },
            "quality": {
                "sessions": int(sessions),
                "messages": int(messages),
                "assistant": {"completed": statuses.get("completed", 0), "failed": statuses.get("failed", 0),
                              "interrupted": statuses.get("interrupted", 0),
                              "generating": statuses.get("generating", 0)},
                "failure_rate": round(100.0 * statuses.get("failed", 0) / answered, 1) if answered else None,
                "retry_rate": round(100.0 * int(retried) / answered, 1) if answered else None,
            },
            "engine": {
                "traces": len(traces),
                "success_rate": round(100.0 * sum(1 for r in traces if r["status"] == "SUCCESS") / len(traces), 1)
                if traces else None,
                "spans": len(spans),
                "span_error_rate": round(100.0 * sum(1 for r in spans if r["status"] == "ERROR") / len(spans), 1)
                if spans else None,
            },
            "trend": trend,
        }
    finally:
        await conn.close()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> str:
    _check(request)
    token = request.query_params.get("token") or ""
    return _DASHBOARD.replace("__TOKEN__", token)


_DASHBOARD = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CareerPilot · 云服务指标</title>
<style>
:root{--bg:#0b1020;--card:#141a2e;--text:#e7ebf5;--dim:#8b93a8;--primary:#6b7cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;padding:24px}
h1{font-size:20px;margin:0 0 4px}p.sub{color:var(--dim);margin:0 0 20px}
.card{background:var(--card);border:1px solid #222b47;border-radius:12px;padding:16px 18px;margin-bottom:16px}
h2{font-size:15px;margin:0 0 12px;color:#c7cdfa}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.stat{background:#0f1528;border-radius:10px;padding:12px}
.stat b{display:block;font-size:22px;margin-top:2px}.stat span{color:var(--dim);font-size:12px}
ul{margin:0;padding-left:18px}li{display:flex;justify-content:space-between;padding:2px 0}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #222b47}
th{color:var(--dim);font-weight:500}.cols3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
#err{color:#ff8a8a}@media(max-width:760px){.grid{grid-template-columns:repeat(2,1fr)}.cols3{grid-template-columns:1fr}}
</style></head><body>
<h1>CareerPilot · 云服务指标汇总</h1><p class="sub">内部观测面板 · 仅所有者可见 · <span id="gen">—</span></p>
<div id="err"></div>
<div class="card"><h2>业务</h2><div class="grid" id="biz"></div></div>
<div class="card"><h2>工程 / 质量</h2><div class="grid" id="eng"></div><div class="cols3"><div><h3>延迟</h3><ul id="lat"></ul></div><div><h3>错误</h3><ul id="errs"></ul></div><div><h3>Token</h3><ul id="tok"></ul></div></div>
<div class="table-wrap"><table><thead><tr><th>模型</th><th>调用</th><th>错误</th><th>平均 ms</th><th>Tokens</th><th>成本 ¥</th></tr></thead><tbody id="models"></tbody></table></div></div>
<div class="card"><h2>回复质量</h2><div class="grid" id="qual"></div></div>
<div class="card"><h2>近 14 天趋势（调用数）</h2><div id="trend" style="display:flex;gap:4px;align-items:flex-end;height:120px"></div></div>
<script>
const TOKEN="__TOKEN__";
const q = v => (v === null || v === undefined) ? "—" : v;
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const stat = (label, value) => `<div class="stat"><span>${esc(label)}</span><b>${esc(q(value))}</b></div>`;
const list = (id, rows) => document.getElementById(id).innerHTML = rows.map(r => `<li><span>${esc(r.name)}</span><b>${esc(r.count)}</b></li>`).join("") || '<li>—</li>';
async function load(){
  try{
    const r = await fetch('/api/v1/ops/metrics?token='+encodeURIComponent(TOKEN));
    if(!r.ok) throw new Error(r.status+" "+r.statusText);
    const d = await r.json();
    document.getElementById('gen').textContent = d.generated_at;
    document.getElementById('biz').innerHTML = stat('用户总数',d.business.users.total)+stat('日活',d.business.users.active_1d)+stat('周活',d.business.users.active_7d)+stat('月活',d.business.users.active_30d);
    document.getElementById('eng').innerHTML = stat('调用数',d.agent.calls)+stat('成功率',(d.agent.ok_rate===null?'—':d.agent.ok_rate+'%'))+stat('P95 延迟',d.agent.latency_ms.p95+' ms')+stat('估算成本',"¥"+d.agent.cost_estimate_cny);
    document.getElementById('lat').innerHTML = `<li><span>P50</span><b>${d.agent.latency_ms.p50} ms</b></li><li><span>P95</span><b>${d.agent.latency_ms.p95} ms</b></li><li><span>P99</span><b>${d.agent.latency_ms.p99} ms</b></li>`;
    document.getElementById('errs').innerHTML = Object.entries(d.agent.errors).map(([k,v])=>`<li><span>${esc(k)}</span><b>${v}</b></li>`).join("") || '<li>—</li>';
    document.getElementById('tok').innerHTML = `<li><span>输入</span><b>${d.agent.tokens.prompt}</b></li><li><span>输出</span><b>${d.agent.tokens.completion}</b></li><li><span>总计</span><b>${d.agent.tokens.total}</b></li>`;
    document.getElementById('models').innerHTML = d.agent.by_model.map(m=>`<tr><td>${esc(m.model)}</td><td>${m.calls}</td><td>${m.errors}</td><td>${m.avg_ms}</td><td>${m.tokens}</td><td>${m.cost_cny}</td></tr>`).join("");
    document.getElementById('qual').innerHTML = stat('会话数',d.quality.sessions)+stat('消息数',d.quality.messages)+stat('失败率',(d.quality.failure_rate===null?'—':d.quality.failure_rate+'%'))+stat('重试率',(d.quality.retry_rate===null?'—':d.quality.retry_rate+'%'));
    const max = Math.max(1, ...d.trend.map(x=>x.calls));
    document.getElementById('trend').innerHTML = d.trend.map(x=>`<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px"><div style="flex:1;width:100%;display:flex;align-items:flex-end"><div title="${x.calls}" style="width:100%;height:${Math.max(3,Math.round(x.calls/max*100))}%;background:var(--primary);border-radius:4px 4px 0 0"></div></div><span style="font-size:10px;color:var(--dim)">${x.date.slice(5)}</span></div>`).join("");
  }catch(e){ document.getElementById('err').textContent = "加载失败: " + e.message; }
}
load();
</script></body></html>"""
