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
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from hmac import compare_digest, digest
from time import time

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from study_chat_trace import study_chat_projection

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPS_TOKEN = os.environ.get("OPS_TOKEN", "")
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
DASHBOARD_COOKIE = "careerpilot_ops_dashboard"
DASHBOARD_SESSION_SECONDS = 900

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


def _check(request: Request, *, required: bool = False, dashboard_session: bool = False) -> None:
    if not OPS_TOKEN:
        if required:
            raise HTTPException(503, "OPS_TOKEN is not configured", headers=PRIVATE_HEADERS)
        return
    token = request.headers.get("x-ops-token")
    if not token and not required:
        token = request.query_params.get("token")
    if not token and dashboard_session and _valid_dashboard_session(request.headers.get("x-ops-session", "")):
        return
    if not token or not compare_digest(token.encode("utf-8"), OPS_TOKEN.encode("utf-8")):
        raise HTTPException(401, "ops token required", headers=PRIVATE_HEADERS)


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
    _check(request, dashboard_session=True)
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
            "WHERE provider IS DISTINCT FROM 'fixture' ORDER BY id DESC LIMIT 5000")
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
            "sum(cost_cny) AS cost FROM agent_metrics WHERE created_at >= $1 "
            "AND provider IS DISTINCT FROM 'fixture' GROUP BY day ORDER BY day", since)
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


@app.get("/api/v1/ops/study-chat/messages/{message_id}")
async def study_chat_detail(request: Request, response: Response, message_id: str) -> dict:
    _check(request, required=True, dashboard_session=True)
    if not re.fullmatch(r"[0-9]{1,10}", message_id) or not 1 <= int(message_id) <= 2_147_483_647:
        raise HTTPException(422, "positive integer message ID required", headers=PRIVATE_HEADERS)
    try:
        conn = await _connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT id, status, attempt, created_at, coach->>'response_language' AS locale,
                       coach->'trace' AS trace,
                       jsonb_typeof(coach->'library_inventory') = 'object' AS is_library_inventory,
                       CASE WHEN coach->'trace'->'library_retrieval' IS NULL THEN sources END AS sources
                FROM study_messages WHERE id=$1 AND role='assistant'
                """,
                int(message_id),
            )
        finally:
            await conn.close()
    except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
        raise HTTPException(503, "study chat diagnostics unavailable", headers=PRIVATE_HEADERS) from exc
    if row is None:
        raise HTTPException(404, "assistant message not found", headers=PRIVATE_HEADERS)
    response.headers.update(PRIVATE_HEADERS)
    return study_chat_projection(row)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    session = request.cookies.get(DASHBOARD_COOKIE, "")
    valid_session = _valid_dashboard_session(session)
    explicit_token = "x-ops-token" in request.headers or "token" in request.query_params
    if explicit_token or not valid_session:
        _check(request)
    issue_session = bool(OPS_TOKEN) and (explicit_token or not valid_session)
    if issue_session:
        expires = str(int(time()) + DASHBOARD_SESSION_SECONDS)
        session = f"{expires}.{_dashboard_signature(expires)}"
    response = HTMLResponse(
        _DASHBOARD.replace("__SESSION__", session if OPS_TOKEN else ""), headers=PRIVATE_HEADERS,
    )
    if issue_session:
        response.set_cookie(
            DASHBOARD_COOKIE, session,
            max_age=DASHBOARD_SESSION_SECONDS, httponly=True, samesite="strict",
            secure=request.url.scheme == "https",
        )
    return response


def _dashboard_signature(expires: str) -> str:
    return digest(OPS_TOKEN.encode("utf-8"), f"ops-dashboard:v1:{expires}".encode("ascii"), "sha256").hex()


def _valid_dashboard_session(session: str) -> bool:
    if not OPS_TOKEN or not re.fullmatch(r"[0-9]{1,12}\.[0-9a-f]{64}", session):
        return False
    expires, signature = session.split(".")
    now = int(time())
    return now < int(expires) <= now + DASHBOARD_SESSION_SECONDS and compare_digest(
        signature, _dashboard_signature(expires),
    )


_DASHBOARD = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="ops-session" content="__SESSION__">
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
#trace-form{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
#trace-form input,#trace-form button{font:inherit;padding:6px 10px;border-radius:6px;border:1px solid #3d4772}
#trace-form input{background:#0f1528;color:var(--text);width:11rem}
#trace-form button{background:#a6afff;color:#0b1020;cursor:pointer}
#trace-form button:disabled{opacity:.6;cursor:default}
#trace-result{display:grid;gap:12px;margin-top:12px}
#trace-result section{background:#0f1528;border-radius:8px;padding:12px 16px}
#trace-result h3{margin:0 0 8px;font-size:14px}
#trace-result dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin:0}
#trace-result dt{color:var(--dim);font-size:12px}
#trace-result dd{margin:0;overflow-wrap:anywhere}
#err{color:#ff8a8a}@media(max-width:760px){.grid{grid-template-columns:repeat(2,1fr)}.cols3{grid-template-columns:1fr}}
</style></head><body>
<h1>CareerPilot · 云服务指标汇总</h1><p class="sub">内部观测面板 · 仅所有者可见 · <span id="gen">—</span></p>
<div id="err"></div>
<div class="card"><h2>业务</h2><div class="grid" id="biz"></div></div>
<div class="card"><h2>工程 / 质量</h2><div class="grid" id="eng"></div><div class="cols3"><div><h3>延迟</h3><ul id="lat"></ul></div><div><h3>错误</h3><ul id="errs"></ul></div><div><h3>Token</h3><ul id="tok"></ul></div></div>
<div class="table-wrap"><table><thead><tr><th>模型</th><th>调用</th><th>错误</th><th>平均 ms</th><th>Tokens</th><th>成本 ¥</th></tr></thead><tbody id="models"></tbody></table></div></div>
<div class="card"><h2>回复质量</h2><div class="grid" id="qual"></div></div>
<div class="card"><h2>学习聊天消息诊断</h2>
<form id="trace-form"><label for="trace-id">助手消息 ID</label>
<input id="trace-id" type="text" inputmode="numeric" pattern="[0-9]{1,10}" required autocomplete="off">
<button type="submit">查询</button></form>
<p id="trace-status" role="status"></p><div id="trace-result"></div></div>
<div class="card"><h2>近 14 天趋势（调用数）</h2><div id="trend" style="display:flex;gap:4px;align-items:flex-end;height:120px"></div></div>
<script>
const sessionMeta = document.querySelector('meta[name="ops-session"]');
const SESSION = sessionMeta.content;
sessionMeta.remove();
const authHeaders = SESSION ? {'X-Ops-Session': SESSION} : {};
const pageUrl = new URL(location.href);
if (pageUrl.searchParams.has('token')) {
  pageUrl.searchParams.delete('token');
  history.replaceState(null, '', pageUrl);
}
const q = v => (v === null || v === undefined) ? "—" : v;
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const stat = (label, value) => `<div class="stat"><span>${esc(label)}</span><b>${esc(q(value))}</b></div>`;
const list = (id, rows) => document.getElementById(id).innerHTML = rows.map(r => `<li><span>${esc(r.name)}</span><b>${esc(r.count)}</b></li>`).join("") || '<li>—</li>';
async function load(){
  try{
    const r = await fetch('/api/v1/ops/metrics', {headers: authHeaders, cache: 'no-store'});
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

const traceForm = document.getElementById('trace-form');
const traceId = document.getElementById('trace-id');
const traceButton = traceForm.querySelector('button');
const traceStatus = document.getElementById('trace-status');
const traceResult = document.getElementById('trace-result');
const known = v => v ?? '未知';
const ms = v => v == null ? '未知' : v + ' ms';
const labels = {
  matched: '有命中', empty: '零命中', not_requested: '未请求检索', unknown: '未知',
  trace: '当前记录', saved_sources: '历史来源', recorded: '有调用记录', not_called: '未调用',
  library_inventory: '由资料库直接生成 · 0 次模型调用',
  message_request: '本次请求开始', worker_start: '后台任务开始',
  stream: '流式输出', terminal: '最终保存', queued: '排队', starting: '启动',
  retrieving: '获取上下文', routing: '路由', generating: '生成中', escalating: '切换档位',
  repairing: '修复产物', completed: '已完成', interrupted: '已中断', failed: '失败',
};
const label = v => labels[v] ?? known(v);
const detail = (heading, fields) => '<section><h3>' + esc(heading) + '</h3><dl>' +
  fields.map(([key, value]) => '<div><dt>' + esc(key) + '</dt><dd>' + esc(value) + '</dd></div>').join('') +
  '</dl></section>';
const counts = (hits, returned) => ['questions', 'interview_sets', 'notes'].map((key, index) => [
  ['题目', '面经套题', '学习资料'][index] + '（命中 / 返回）', known(hits[key]) + ' / ' + known(returned[key]),
]);
function renderTrace(d) {
  const {retrieval, routing, provider, persistence} = d;
  return [
    detail('资料库检索', [
      ['结果', label(retrieval.state)], ['证据', label(retrieval.evidence)],
      ['首次请求后开始', ms(retrieval.started_ms)], ['首次请求后结束', ms(retrieval.finished_ms)],
      ...counts(retrieval.hits, retrieval.returned),
    ]),
    detail('路由', [
      ['计时起点', label(routing.timing_origin)], ['开始', ms(routing.started_ms)], ['结束', ms(routing.finished_ms)],
      ['模式', known(routing.mode)], ['偏好档位', known(routing.preferred_tier)],
      ['初始档位', known(routing.initial_tier)], ['所选档位', known(routing.selected_tier)],
    ]),
    detail('供应商与正文', [
      ['当前尝试', label(d.reply_kind ?? provider.state)], ['首段可见正文', ms(provider.first_visible_body_ms)],
      ['正文发布阶段', label(provider.body_release_stage)],
    ]),
    ...provider.calls.map(call => detail('尝试 ' + known(call.attempt) + ' · 调用 ' + known(call.generation), [
      ['供应商', known(call.provider)], ['档位', known(call.tier)], ['结果', label(call.outcome)],
      ['开始', ms(call.provider_start_ms)], ['结束', ms(call.provider_end_ms)], ['耗时', ms(call.elapsed_ms)],
      ['调用开始后首段正文', ms(call.first_visible_body_ms)], ['失败类别', known(call.error)],
      ['HTTP 状态', known(call.http_status)], ['再次调用原因', known(call.retry_kind)],
      ['阻止回退原因', known(call.fallback_blocked)], ['输入 token', known(call.usage?.prompt_tokens)],
      ['输出 token', known(call.usage?.completion_tokens)], ['总 token', known(call.usage?.total_tokens)],
    ])),
    detail('保存状态', [
      ['状态', label(persistence.status)], ['阶段', label(persistence.stage)], ['当前尝试次数', persistence.attempt],
      ['创建时间', persistence.created_at], ['开始保存', ms(persistence.finalizing_ms)],
      ['最终状态写入时间', known(persistence.terminal_write_at)], ['错误类别', known(persistence.error_kind)],
    ]),
  ].join('');
}
if (!SESSION) {
  traceButton.disabled = traceId.disabled = true;
  traceStatus.textContent = '配置 OPS_TOKEN 后可查询消息诊断。';
}
traceForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!SESSION || traceButton.disabled) return;
  traceStatus.textContent = '查询中…';
  traceResult.replaceChildren();
  traceButton.disabled = true;
  try {
    const response = await fetch('/api/v1/ops/study-chat/messages/' + encodeURIComponent(traceId.value),
      {headers: authHeaders, cache: 'no-store'});
    if (!response.ok) {
      traceStatus.textContent = ({
        401: '令牌无效。', 404: '未找到该助手消息。', 422: '请输入有效的正整数消息 ID。',
        503: '消息诊断暂不可用。',
      })[response.status] ?? '查询失败。';
      return;
    }
    const data = await response.json();
    traceResult.innerHTML = renderTrace(data);
    traceStatus.textContent = '助手消息 ' + data.message_id + ' · 回复语言 ' + known(data.locale);
  } catch {
    traceStatus.textContent = '查询失败。';
  } finally {
    traceButton.disabled = false;
  }
});
</script></body></html>"""
