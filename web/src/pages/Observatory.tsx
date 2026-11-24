import { getLocale, t, useT } from '../i18n';
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHead, StatCard } from '../components/ui';
import { api, ObservatoryData, TracesData } from '../api/client';

const HEALTH_CLS: Record<string, string> = { HEALTHY: 'chip-green', IDLE: 'chip-slate', DEGRADED: 'chip-amber' };
const HEALTH_NAME: Record<string, string> = {
  get HEALTHY() { return t('progress.observatoryHealthy'); },
  get IDLE() { return t('progress.observatoryIdle'); },
  get DEGRADED() { return t('progress.observatoryDegraded'); },
};
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

interface AnalyticsData {
  users: { total: number; active_1d: number; active_7d: number; active_30d: number };
  events_total: number;
  top_events: { name: string; count: number }[];
  top_pages: { page: string; count: number }[];
  devices: { device: string; count: number }[];
  regions: { region: string; count: number }[];
  locales: { locale: string; count: number }[];
  trend: { date: string; events: number; users: number }[];
}

export default function Observatory() {
  const t = useT();
  const navigate = useNavigate();
  const [obs, setObs] = useState<ObservatoryData | null>(null);
  const [traces, setTraces] = useState<TraceApi[]>([]);
  const [decisions, setDecisions] = useState<DecisionApi[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [openTrace, setOpenTrace] = useState<number | null>(null);
  const [openDecision, setOpenDecision] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const [advanced, setAdvanced] = useState(false);
  const [analytics, setAnalytics] = useState<AnalyticsData | null>(null);
  const [analyticsErr, setAnalyticsErr] = useState<string | null>(null);

  useEffect(() => {
    if (!analytics) {
      api.get<AnalyticsData>('/telemetry/summary')
        .then((a) => { setAnalytics(a); setAnalyticsErr(null); })
        .catch((e) => setAnalyticsErr(String(e.message || e)));
    }
  }, [analytics]);

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

  const runTime = (value?: string | null) => {
    if (!value) return '';
    const date = new Date(value);
    if (getLocale() === 'zh-CN' || Number.isNaN(date.getTime())) return value.replace('T', ' ').slice(5, 16);
    return date.toLocaleString(getLocale(), { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  };
  const fmtDur = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);
  const fmtCost = (c?: number | null) => (c != null ? `¥${c < 0.01 && c > 0 ? c.toFixed(3) : c.toFixed(2)}` : t('progress.unknown'));

  // v3.1 M6: 通俗摘要(默认视图的人话版)
  const summary: { ic: string; text: string }[] = [];
  if (obs && !err) {
    summary.push({ ic: '🟢', text: t('progress.observatoryRunSummary', { runs: obs.stats.total_runs.toLocaleString(getLocale()), success: obs.stats.success_rate.toFixed(1), duration: fmtDur(obs.stats.avg_latency_ms) }) });
    summary.push({ ic: '💰', text: obs.stats.cost_cny==null ? t('progress.observatoryCostUnknown') : t('progress.observatoryCostSummary', { total: obs.stats.cost_cny.toFixed(2), average: (obs.stats.cost_cny / Math.max(1, obs.stats.total_runs)).toFixed(3) }) });
    if (obs.stats.failures > 0) {
      const f = obs.recent_failures[0];
      summary.push({ ic: '⚠️', text: f ? t('progress.observatoryFailureSummary', { count: obs.stats.failures, title: f.title, reason: f.fail_reason || t('progress.observatoryReasonUnknown') }) : t('progress.observatoryFailureCount', { count: obs.stats.failures }) });
    } else {
      summary.push({ ic: '✅', text: t('progress.observatoryNoFailureSummary') });
    }
    const degraded = obs.engines.filter((e) => e.health === 'DEGRADED');
    summary.push({ ic: '⚙️', text: degraded.length
      ? t('progress.observatoryDegradedSummary', { count: degraded.length, engines: degraded.map(engine => engine.name).join(getLocale() === 'zh-CN' ? '、' : ', ') })
      : obs.engines.length ? t('progress.observatoryNoDegraded') : t('progress.observatoryNoEngineData') });
  } else {
    summary.push({ic:'ℹ️',text:loading?t('progress.observatoryLoadingRecords'):err?t('progress.observatoryRecordsUnavailable'):t('progress.observatoryNoRecordsPeriod')});
  }

  return (
    <div>
      <PageHead
        title={t('progress.observatoryHeading')}
        sub={t('progress.observatoryDescription')}
      >
        <button className="btn btn-outline" onClick={() => setTick((t) => t + 1)}>{t('progress.observatoryRefresh')}</button>
        <button className="btn btn-primary" onClick={() => navigate('/evolution')}>{t('progress.observatoryViewExperiments')}</button>
      </PageHead>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>{t('progress.observatoryLoadingRuns')}</div>}
      {err && (
        <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>
          {t('progress.observatoryLoadError', { error: err })}
        </div>
      )}

      {/* v3.1 M6: 通俗摘要(默认) + 高级模式开关 */}
      <div className="card obs-summary" style={{ marginBottom: 18 }}>
        <div className="card-head">
          <span className="card-title">{t('progress.observatorySummary')}</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12, color: 'var(--text-3)' }}>{t('progress.observatoryAdvanced')}</span>
            <button
              type="button"
              className={`switch ${advanced ? 'on' : ''}`}
              aria-label={t('progress.observatoryToggleAdvanced')}
              onClick={() => setAdvanced((v) => !v)}
            />
          </span>
        </div>
        {summary.map((s, i) => (
          <div className="obs-summary-line" key={i}>
            <span className="obs-summary-ic">{s.ic}</span>
            <span>{s.text}</span>
          </div>
        ))}
        <div className="hint" style={{ marginTop: 8 }}>{t('progress.observatoryAdvancedDescription')}</div>
      </div>

      {/* 产品分析(匿名遥测): 用户数/功能使用/地域分布 */}
      <div className="card" style={{ marginBottom: 18 }}>
        <div className="card-head">
          <span className="card-title">{t('progress.analyticsHeading')}</span>
          <span className="card-sub">{t('progress.analyticsDescription')}</span>
        </div>
        {analytics && analytics.users ? (
          <>
            <div className="grid grid-4" style={{ marginBottom: 16 }}>
              <StatCard label={t('progress.analyticsUsers')} value={t('progress.analyticsUsersCount', { count: analytics.users.total.toLocaleString(getLocale()) })} ic="👥" foot={t('progress.analyticsTotal')} />
              <StatCard label={t('progress.analyticsActive1d')} value={analytics.users.active_1d.toLocaleString(getLocale())} ic="🔥" foot={t('progress.analyticsTotal')} />
              <StatCard label={t('progress.analyticsActive7d')} value={analytics.users.active_7d.toLocaleString(getLocale())} ic="📈" foot={t('progress.analyticsTotal')} />
              <StatCard label={t('progress.analyticsActive30d')} value={analytics.users.active_30d.toLocaleString(getLocale())} ic="🌙" foot={t('progress.analyticsEventsTotal') + ': ' + analytics.events_total.toLocaleString(getLocale())} />
            </div>
            <div className="grid grid-2">
              <div>
                <div className="card-title" style={{ fontSize: 14, marginBottom: 8 }}>{t('progress.analyticsTopEvents')}</div>
                {analytics.top_events.length ? analytics.top_events.slice(0, 8).map((e) => (
                  <div className="list-row" key={e.name} style={{ borderBottom: '1px solid var(--border-2)' }}>
                    <div className="list-main"><div className="list-title" style={{ fontSize: 13 }}>{e.name}</div></div>
                    <span className="chip chip-purple">{t('progress.analyticsCount', { count: e.count.toLocaleString(getLocale()) })}</span>
                  </div>
                )) : <p className="hint" style={{ padding: 12 }}>{t('progress.analyticsNoData')}</p>}
              </div>
              <div>
                <div className="card-title" style={{ fontSize: 14, marginBottom: 8 }}>{t('progress.analyticsTopPages')}</div>
                {analytics.top_pages.length ? analytics.top_pages.slice(0, 8).map((e) => (
                  <div className="list-row" key={e.page} style={{ borderBottom: '1px solid var(--border-2)' }}>
                    <div className="list-main"><div className="list-title" style={{ fontSize: 13 }}>{e.page}</div></div>
                    <span className="chip chip-green">{t('progress.analyticsCount', { count: e.count.toLocaleString(getLocale()) })}</span>
                  </div>
                )) : <p className="hint" style={{ padding: 12 }}>{t('progress.analyticsNoData')}</p>}
              </div>
            </div>
            <div className="grid grid-3" style={{ marginTop: 16 }}>
              <div>
                <div className="card-title" style={{ fontSize: 14, marginBottom: 8 }}>{t('progress.analyticsDevices')}</div>
                {analytics.devices.length ? analytics.devices.slice(0, 6).map((r) => (
                  <div className="list-row" key={r.device} style={{ borderBottom: '1px solid var(--border-2)' }}>
                    <div className="list-main"><div className="list-title" style={{ fontSize: 13 }}>{r.device}</div></div>
                    <span className="chip chip-slate">{t('progress.analyticsCount', { count: r.count.toLocaleString(getLocale()) })}</span>
                  </div>
                )) : <p className="hint" style={{ padding: 12 }}>{t('progress.analyticsNoData')}</p>}
              </div>
              <div>
                <div className="card-title" style={{ fontSize: 14, marginBottom: 8 }}>{t('progress.analyticsRegions')}</div>
                {analytics.regions.length ? analytics.regions.slice(0, 6).map((r) => (
                  <div className="list-row" key={r.region} style={{ borderBottom: '1px solid var(--border-2)' }}>
                    <div className="list-main"><div className="list-title" style={{ fontSize: 13 }}>{r.region}</div></div>
                    <span className="chip chip-slate">{t('progress.analyticsCount', { count: r.count.toLocaleString(getLocale()) })}</span>
                  </div>
                )) : <p className="hint" style={{ padding: 12 }}>{t('progress.analyticsNoData')}</p>}
              </div>
              <div>
                <div className="card-title" style={{ fontSize: 14, marginBottom: 8 }}>{t('progress.analyticsLocales')}</div>
                {analytics.locales.length ? analytics.locales.slice(0, 6).map((r) => (
                  <div className="list-row" key={r.locale} style={{ borderBottom: '1px solid var(--border-2)' }}>
                    <div className="list-main"><div className="list-title" style={{ fontSize: 13 }}>{r.locale}</div></div>
                    <span className="chip chip-slate">{t('progress.analyticsCount', { count: r.count.toLocaleString(getLocale()) })}</span>
                  </div>
                )) : <p className="hint" style={{ padding: 12 }}>{t('progress.analyticsNoData')}</p>}
              </div>
            </div>
            {analytics.trend.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <div className="card-title" style={{ fontSize: 14, marginBottom: 8 }}>{t('progress.analyticsTrend')}</div>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 90, overflowX: 'auto' }}>
                  {analytics.trend.map((d) => {
                    const max = Math.max(1, ...analytics.trend.map((x) => Math.max(x.events, x.users)));
                    return (
                      <div key={d.date} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 34 }}>
                        <div style={{ fontSize: 10, color: 'var(--text-3)' }}>{d.users}</div>
                        <div style={{ width: 12, height: `${Math.max(2, (d.users / max) * 56)}px`, background: 'var(--primary)', borderRadius: 3, opacity: 0.85 }} title={d.date} />
                        <div style={{ fontSize: 9, color: 'var(--text-4)', marginTop: 2 }}>{d.date.slice(5)}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        ) : analyticsErr ? (
          <p className="hint" style={{ padding: 18 }}>{t('progress.analyticsLoadError', { error: analyticsErr })}</p>
        ) : (
          <p className="hint" style={{ padding: 18 }}>{t('progress.loading')}</p>
        )}
      </div>

      {/* 统计卡(高级数据) */}
      {advanced && (obs && !err ? (
        <div className="grid-4" style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0,1fr))', gap: 14, marginBottom: 18 }}>
          <StatCard label={t('progress.observatoryRuns')} value={obs.stats.total_runs.toLocaleString(getLocale())} ic="⚡" foot={t('progress.observatoryAllEngines')} />
          <StatCard label={t('progress.observatorySuccessRate')} value={`${obs.stats.success_rate.toFixed(1)}%`} ic="✅" foot={err ? '' : t('progress.observatoryLast30Days')} />
          <StatCard label={t('progress.observatoryLatency')} value={fmtDur(obs.stats.avg_latency_ms)} ic="⏱" foot={t('progress.observatoryEndToEnd')} />
          <StatCard label={t('progress.observatoryCost')} value={fmtCost(obs.stats.cost_cny)} ic="💰" foot={t('progress.observatoryModelAndTool')} />
          <StatCard label={t('progress.observatoryFailures')} value={String(obs.stats.failures)} ic="⚠️" foot={t('progress.observatorySeeCauses')} />
        </div>
      ) : null
      )}

      <div className="grid-2-1">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* 运行追踪 */}
          <div className="card">
            <div className="card-head">
              <span className="card-title">{t('progress.observatoryTraces')}{advanced ? '' : t('progress.observatoryCompact')}</span>
              <span className="card-sub">{advanced ? t('progress.observatoryTraceExpand') : t('progress.observatoryTraceAdvanced')}</span>
            </div>
            {!err && traces.length > 0 ? (
              traces.map((trace) => (
                <div key={trace.id} className="trace-item">
                  <div className="list-row" style={{ borderBottom: 'none', cursor: 'pointer' }} onClick={() => toggleTrace(trace)}>
                    <div className="list-ic">{trace.status === 'SUCCESS' ? '✅' : trace.status === 'RUNNING' ? '⏳' : '⚠️'}</div>
                    <div className="list-main">
                      <div className="list-title">{trace.short_code} · {trace.title}</div>
                      <div className="list-sub">
                        {trace.kind} · {runTime(trace.started_at)}
                        {advanced && (trace.span_count
                          ? t('progress.observatoryTraceStatsWithSpans', { duration: fmtDur(trace.duration_ms), cost: fmtCost(trace.cost_cny), count: trace.span_count })
                          : t('progress.observatoryTraceStats', { duration: fmtDur(trace.duration_ms), cost: fmtCost(trace.cost_cny) }))}
                      </div>
                    </div>
                    <span className={`chip ${trace.status === 'SUCCESS' ? 'chip-green' : trace.status === 'RUNNING' ? 'chip-purple' : 'chip-red'}`}>
                      {trace.status === 'SUCCESS' ? t('progress.observatorySuccess') : trace.status === 'RUNNING' ? t('progress.observatoryRunning') : t('progress.observatoryFailed')}
                    </span>
                    <span style={{ color: 'var(--text-4)', fontSize: 12 }}>{openTrace === trace.id ? '▴' : '▾'}</span>
                  </div>
                  {openTrace === trace.id && (
                    <div className="trace-spans">
                      {(trace.spans || []).map((s) => (
                        <div className="span-row" key={s.id}>
                          <span className={`status-dot ${s.status === 'OK' ? 'dot-green' : 'dot-red'}`} />
                          <span className="span-name">{s.name}</span>
                          <span className="span-bar">
                            <span className="span-bar-fill" style={{ width: `${Math.min(100, (s.duration_ms / Math.max(1, trace.duration_ms)) * 100)}%`, background: s.status === 'OK' ? 'var(--primary)' : 'var(--red)' }} />
                          </span>
                          <span className="span-dur">{fmtDur(s.duration_ms)}</span>
                          {advanced && <span className="span-cost">{fmtCost(s.cost_cny)}</span>}
                        </div>
                      ))}
                      {trace.fail_reason && <div className="trace-fail">{t('progress.observatoryFailureReason')}{trace.fail_reason}</div>}
                    </div>
                  )}
                </div>
              ))
            ) : (
              <p className="hint" style={{padding:18}}>{loading ? t('progress.loading') : err ? t('progress.observatoryNotAvailable') : t('progress.observatoryNoRuns')}</p>
            )}
          </div>

          {/* 决策记录 */}
          <div className="card">
            <div className="card-head">
              <span className="card-title">{t('progress.observatoryDecisions')}</span>
              <span className="card-sub">{t('progress.observatoryDecisionWhy')}</span>
            </div>
            {!err && decisions.length > 0 ? (
              decisions.map((d) => (
                <div key={d.id} className="list-row" style={{ alignItems: 'flex-start', cursor: 'pointer' }} onClick={() => setOpenDecision(openDecision === d.id ? null : d.id)}>
                  <div className="list-ic">🧭</div>
                  <div className="list-main">
                    <div className="list-title">{d.short_code} · {d.question}</div>
                    <div className="list-sub">
                      {t('progress.observatoryDecisionEvidence', { date: runTime(d.created_at), evidence: Array.isArray(d.evidence_refs) ? d.evidence_refs.join(', ') : d.evidence_refs || '—' })}
                    </div>
                    {openDecision === d.id && (
                      <div className="decision-body">
                        <div className="decision-chosen">{t('progress.observatoryChosen')}{d.chosen}</div>
                        {(Array.isArray(d.alternatives) ? d.alternatives : []).map((a: any, i: number) => (
                          <div className="decision-alt" key={i}>{t('progress.observatoryAlternatives')}{typeof a === 'string' ? a : a.text}{typeof a === 'object' && a.why ? <span className="hint"> — {a.why}</span> : null}</div>
                        ))}
                        {d.rationale && <div className="hint" style={{ marginTop: 6 }}>{t('progress.observatoryRationale')}{d.rationale}</div>}
                      </div>
                    )}
                  </div>
                  <span className="chip chip-green">{t('progress.observatoryExecuted')}</span>
                </div>
              ))
            ) : (
              <p className="hint" style={{padding:18}}>{loading ? t('progress.loading') : err ? t('progress.observatoryNotAvailable') : t('progress.observatoryNoDecisions')}</p>
            )}
          </div>
        </div>

        {/* 引擎检查 + 证据源 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div className="card">
            <div className="card-head">
              <span className="card-title">{t('progress.observatoryEngineCheck')}</span>
              <span className="card-sub">{obs && !err ? t('progress.observatoryEngineInference') : t('progress.observatoryNoRunData')}</span>
            </div>
            {obs && !err ? (
              obs.engines.map((e) => {
                const lr = e.last_run;
                const lrText = !lr ? '' : typeof lr === 'string' ? lr : `${lr.short_code} ${lr.title}`;
                const lrTime = !lr ? '' : runTime(typeof lr === 'string' ? lr : lr.finished_at);
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
                        {e.runs != null && <span>{t('progress.observatoryRunsLabel') + ' '}<b>{e.runs}</b></span>}
                        {lrText && <span>{t('progress.observatoryLastRun') + ' '}{lrTime} · {lrText}</span>}
                      </div>
                    </div>
                  </div>
                );
              })
            ) : (
              <p className="hint" style={{padding:18}}>{loading ? t('progress.loading') : err ? t('progress.observatoryNotAvailable') : t('progress.observatoryNoEngines')}</p>
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <span className="card-title">{t('progress.observatoryRecentFailures')}</span>
              <span className="card-sub">{t('progress.observatoryRunRecord')}</span>
            </div>
            {obs && !err && obs.recent_failures.length > 0 ? (
              obs.recent_failures.map((f) => (
                <div className="list-row" key={f.short_code}>
                  <div className="list-ic">⚠️</div>
                  <div className="list-main">
                    <div className="list-title">{f.short_code} · {f.title}</div>
                    <div className="list-sub">{f.fail_reason || t('progress.observatoryFailed')} · {runTime(f.finished_at)}</div>
                  </div>
                  <span className="chip chip-red">{t('progress.observatoryFailed')}</span>
                </div>
              ))
            ) : (
              <p className="hint" style={{padding:18}}>{loading?t('progress.loading'):err?t('progress.observatoryNotAvailable'):t('progress.observatoryNoFailures')}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
