import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, StatCard } from '../components/ui';
import { api, ObservatoryData, TracesData } from '../api/client';
import { DECISIONS, ENGINES, OBS_STATS, TRACES } from '../data/demo';

const HEALTH_CLS: Record<string, string> = { HEALTHY: 'chip-green', IDLE: 'chip-slate', DEGRADED: 'chip-amber' };
const HEALTH_NAME: Record<string, string> = { HEALTHY: '健康', IDLE: '空闲', DEGRADED: '降级' };
const ENGINE_IC: Record<string, string> = {
  job_intelligence: '🎯', memory_engine: '🧠', project_analyzer: '💼', competency_engine: '📊', learning_planner: '🧩',
};

interface TraceApi {
  id: number; short_code: string; kind: string; title: string; status: string;
  duration_ms: number; cost_cny?: number | null; fail_reason?: string | null;
  started_at?: string | null; span_count?: number;
  spans?: { id: number; name: string; status: string; duration_ms: number; cost_cny?: number | null }[];
}

interface DecisionApi {
  id: number; short_code: string; question: string; chosen: string;
  alternatives?: { text: string; why?: string }[] | string[];
  evidence_refs?: string[] | string; rationale?: string; created_at?: string;
}

export default function Observatory() {
  const navigate = useNavigate();
  const [obs, setObs] = useState<ObservatoryData | null>(null);
  const [traces, setTraces] = useState<TraceApi[]>([]);
  const [decisions, setDecisions] = useState<DecisionApi[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [openTrace, setOpenTrace] = useState<number | null>(null);
  const [openDecision, setOpenDecision] = useState<number | null>(null);
  const [tick, setTick] = useState(0);

  const load = () => {
    Promise.all([
      api.get<ObservatoryData>('/observatory'),
      api.get<TracesData>('/observatory/traces'),
      api.get<{ items: DecisionApi[] }>('/observatory/decisions'),
    ])
      .then(([o, t, d]) => {
        setObs(o); setTraces(t.items as TraceApi[]); setDecisions(d.items); setErr(null);
      })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, [tick]);

  const toggleTrace = (t: TraceApi) => {
    if (openTrace === t.id) { setOpenTrace(null); return; }
    setOpenTrace(t.id);
    if (!t.spans) {
      api.get<TraceApi>(`/observatory/traces/${t.id}`)
        .then((full) => {
          setTraces((prev) => prev.map((x) => (x.id === full.id ? { ...x, spans: full.spans } : x)));
        })
        .catch(() => undefined);
    }
  };

  const fmtDur = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);
  const fmtCost = (c?: number | null) => (c != null ? `¥${c < 0.01 && c > 0 ? c.toFixed(3) : c.toFixed(2)}` : '—');

  return (
    <div>
      <PageHead
        title="系统观测台"
        sub="每一次执行、决策、证据写入都可追踪 · 失败可定位、成本可核算"
      >
        <button className="btn btn-outline" onClick={() => setTick((t) => t + 1)}>↻ 刷新</button>
        <button className="btn btn-primary" onClick={() => navigate('/evolution')}>查看实验</button>
      </PageHead>
      <DemoBanner />

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载运行数据…</div>}
      {err && (
        <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>
          后端不可用（{err}），以下为静态演示数据
        </div>
      )}

      {/* 统计卡 */}
      {obs && !err ? (
        <div className="grid-4" style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0,1fr))', gap: 14, marginBottom: 18 }}>
          <StatCard label="执行次数" value={obs.stats.total_runs.toLocaleString()} ic="⚡" foot="所有引擎累计" />
          <StatCard label="成功率" value={`${obs.stats.success_rate.toFixed(1)}%`} ic="✅" foot={err ? '' : '近 30 天'} />
          <StatCard label="平均延迟" value={fmtDur(obs.stats.avg_latency_ms)} ic="⏱" foot="端到端" />
          <StatCard label="成本" value={`¥${obs.stats.cost_cny.toFixed(2)}`} ic="💰" foot="LLM + 工具调用" />
          <StatCard label="失败" value={String(obs.stats.failures)} ic="⚠️" foot="可一键重放" />
        </div>
      ) : (
        <div className="grid-4" style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0,1fr))', gap: 14, marginBottom: 18 }}>
          {OBS_STATS.map((s) => (
            <StatCard key={s.label} label={s.label} value={s.value} ic={s.ic} foot={s.foot} />
          ))}
        </div>
      )}

      <div className="grid-2-1">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* 运行追踪 */}
          <div className="card">
            <div className="card-head">
              <span className="card-title">运行追踪</span>
              <span className="card-sub">点击展开 span 明细</span>
            </div>
            {!err && traces.length > 0 ? (
              traces.map((t) => (
                <div key={t.id} className="trace-item">
                  <div className="list-row" style={{ borderBottom: 'none', cursor: 'pointer' }} onClick={() => toggleTrace(t)}>
                    <div className="list-ic">{t.status === 'SUCCESS' ? '✅' : t.status === 'RUNNING' ? '⏳' : '⚠️'}</div>
                    <div className="list-main">
                      <div className="list-title">{t.short_code} · {t.title}</div>
                      <div className="list-sub">
                        {t.kind} · {(t.started_at || '').replace('T', ' ').slice(5, 16)} · 耗时 {fmtDur(t.duration_ms)} · 成本 {fmtCost(t.cost_cny)}{t.span_count ? ` · ${t.span_count} 个 span` : ''}
                      </div>
                    </div>
                    <span className={`chip ${t.status === 'SUCCESS' ? 'chip-green' : t.status === 'RUNNING' ? 'chip-purple' : 'chip-red'}`}>
                      {t.status === 'SUCCESS' ? '成功' : t.status === 'RUNNING' ? '运行中' : '失败'}
                    </span>
                    <span style={{ color: 'var(--text-4)', fontSize: 12 }}>{openTrace === t.id ? '▴' : '▾'}</span>
                  </div>
                  {openTrace === t.id && (
                    <div className="trace-spans">
                      {(t.spans || []).map((s) => (
                        <div className="span-row" key={s.id}>
                          <span className={`status-dot ${s.status === 'OK' ? 'dot-green' : 'dot-red'}`} />
                          <span className="span-name">{s.name}</span>
                          <span className="span-bar">
                            <span className="span-bar-fill" style={{ width: `${Math.min(100, (s.duration_ms / Math.max(1, t.duration_ms)) * 100)}%`, background: s.status === 'OK' ? 'var(--primary)' : 'var(--red)' }} />
                          </span>
                          <span className="span-dur">{fmtDur(s.duration_ms)}</span>
                          <span className="span-cost">{fmtCost(s.cost_cny)}</span>
                        </div>
                      ))}
                      {t.fail_reason && <div className="trace-fail">❌ 失败原因：{t.fail_reason}</div>}
                    </div>
                  )}
                </div>
              ))
            ) : (
              TRACES.map((t) => (
                <div key={t.id} className="trace-item">
                  <div className="list-row" style={{ borderBottom: 'none', cursor: 'pointer' }} onClick={() => setOpenTrace(openTrace === (t.id as any) ? null : (t.id as any))}>
                    <div className="list-ic">{t.status === '成功' ? '✅' : '⚠️'}</div>
                    <div className="list-main">
                      <div className="list-title">{t.id} · {t.name}</div>
                      <div className="list-sub">{t.time} · 成本 {t.cost} · {t.spans.length} 个 span</div>
                    </div>
                    <span className={`chip ${t.status === '成功' ? 'chip-green' : 'chip-red'}`}>{t.status}</span>
                    <span style={{ color: 'var(--text-4)', fontSize: 12 }}>{openTrace === (t.id as any) ? '▴' : '▾'}</span>
                  </div>
                  {openTrace === (t.id as any) && (
                    <div className="trace-spans">
                      {t.spans.map((s: any, i: number) => (
                        <div className="span-row" key={i}>
                          <span className={`status-dot ${s.status === '成功' ? 'dot-green' : 'dot-red'}`} />
                          <span className="span-name">{s.name}</span>
                          <span className="span-bar">
                            <span className="span-bar-fill" style={{ width: `${Math.min(100, parseFloat(s.dur) * 40)}%`, background: s.status === '成功' ? 'var(--primary)' : 'var(--red)' }} />
                          </span>
                          <span className="span-dur">{s.dur}</span>
                          <span className="span-cost">{s.cost}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          {/* 决策记录 */}
          <div className="card">
            <div className="card-head">
              <span className="card-title">决策记录</span>
              <span className="card-sub">为什么选这个行动</span>
            </div>
            {!err && decisions.length > 0 ? (
              decisions.map((d) => (
                <div key={d.id} className="list-row" style={{ alignItems: 'flex-start', cursor: 'pointer' }} onClick={() => setOpenDecision(openDecision === d.id ? null : d.id)}>
                  <div className="list-ic">🧭</div>
                  <div className="list-main">
                    <div className="list-title">{d.short_code} · {d.question}</div>
                    <div className="list-sub">
                      {(d.created_at || '').replace('T', ' ').slice(5, 16)} · 证据 {Array.isArray(d.evidence_refs) ? d.evidence_refs.join(', ') : d.evidence_refs || '—'}
                    </div>
                    {openDecision === d.id && (
                      <div className="decision-body">
                        <div className="decision-chosen">✓ 选择：{d.chosen}</div>
                        {(Array.isArray(d.alternatives) ? d.alternatives : []).map((a: any, i: number) => (
                          <div className="decision-alt" key={i}>✗ 候选：{typeof a === 'string' ? a : a.text}{typeof a === 'object' && a.why ? <span className="hint"> — {a.why}</span> : null}</div>
                        ))}
                        {d.rationale && <div className="hint" style={{ marginTop: 6 }}>依据：{d.rationale}</div>}
                      </div>
                    )}
                  </div>
                  <span className="chip chip-green">已执行</span>
                </div>
              ))
            ) : (
              DECISIONS.map((d) => (
                <div key={d.id} className="list-row" style={{ alignItems: 'flex-start', cursor: 'pointer' }} onClick={() => setOpenDecision(openDecision === (d.id as any) ? null : (d.id as any))}>
                  <div className="list-ic">🧭</div>
                  <div className="list-main">
                    <div className="list-title">{d.id} · {d.title}</div>
                    <div className="list-sub">{d.time} · 证据 {d.evidence.join(', ')}</div>
                    {openDecision === (d.id as any) && (
                      <div className="decision-body">
                        <div className="decision-chosen">✓ 选择：{d.chosen}</div>
                        {d.alternatives.map((a) => (
                          <div className="decision-alt" key={a.text}>✗ 候选：{a.text} <span className="hint">— {a.why}</span></div>
                        ))}
                      </div>
                    )}
                  </div>
                  <span className={`chip ${d.status === '已执行' ? 'chip-green' : 'chip-slate'}`}>{d.status}</span>
                </div>
              ))
            )}
          </div>
        </div>

        {/* 引擎检查 + 证据源 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div className="card">
            <div className="card-head">
              <span className="card-title">引擎检查</span>
              <span className="card-sub">{obs && !err ? '健康状态由最近运行推断' : '示例'}</span>
            </div>
            {obs && !err ? (
              obs.engines.map((e) => {
                const lr = e.last_run;
                const lrText = !lr ? '' : typeof lr === 'string' ? lr : `${lr.short_code} ${lr.title}`;
                const lrTime = !lr ? '' : typeof lr === 'string' ? lr.slice(5, 16) : (lr.finished_at || '').replace('T', ' ').slice(5, 16);
                return (
                  <div className="engine-card" key={e.key} style={{ border: 'none', padding: '10px 0', borderBottom: '1px solid var(--border-2)', borderRadius: 0 }}>
                    <div className="engine-ic">{ENGINE_IC[e.key] || '⚙️'}</div>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span className="engine-name">{e.name}</span>
                        <span className={`chip ${HEALTH_CLS[e.health] || 'chip-slate'}`}>{HEALTH_NAME[e.health] || e.health}</span>
                      </div>
                      <div className="engine-desc">{e.health_note || e.desc || e.key}</div>
                      <div className="engine-stats">
                        {e.runs != null && <span>运行 <b>{e.runs}</b></span>}
                        {lrText && <span>最近 {lrTime} · {lrText}</span>}
                      </div>
                    </div>
                  </div>
                );
              })
            ) : (
              ENGINES.map((e) => (
                <div className="engine-card" key={e.key} style={{ border: 'none', padding: '10px 0', borderBottom: '1px solid var(--border-2)', borderRadius: 0 }}>
                  <div className="engine-ic">
                    {e.key === 'job' ? '🎯' : e.key === 'memory' ? '🧠' : e.key === 'project' ? '💼' : e.key === 'competency' ? '📊' : '🧩'}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span className="engine-name">{e.name}</span>
                      <span className={`chip ${e.health === '健康' ? 'chip-green' : 'chip-amber'}`}>{e.health}</span>
                    </div>
                    <div className="engine-desc">{e.desc}</div>
                    <div className="engine-stats">
                      <span>运行 <b>{e.stats.runs}</b></span>
                      <span>成功 <b>{e.stats.ok}</b></span>
                      <span>最近 {e.last}</span>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <span className="card-title">证据源</span>
              <span className="card-sub">最近写入</span>
            </div>
            {obs && !err && obs.recent_failures.length > 0 ? (
              obs.recent_failures.map((f) => (
                <div className="list-row" key={f.short_code}>
                  <div className="list-ic">⚠️</div>
                  <div className="list-main">
                    <div className="list-title">{f.short_code} · {f.title}</div>
                    <div className="list-sub">{f.fail_reason || '失败'} · {(f.finished_at || '').replace('T', ' ').slice(5, 16)}</div>
                  </div>
                  <span className="chip chip-red">失败</span>
                </div>
              ))
            ) : (
              <>
                <div className="list-row">
                  <div className="list-ic">🧾</div>
                  <div className="list-main">
                    <div className="list-title">EV-041 · 诊断证据</div>
                    <div className="list-sub">来源 TR-1042 · 面经导入</div>
                  </div>
                  <span className="chip chip-purple">诊断</span>
                </div>
                <div className="list-row">
                  <div className="list-ic">📄</div>
                  <div className="list-main">
                    <div className="list-title">EV-039 · 面经证据</div>
                    <div className="list-sub">来源 IM-103 · 字节跳动</div>
                  </div>
                  <span className="chip chip-blue">面经</span>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
