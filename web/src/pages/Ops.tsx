import { useEffect, useState } from 'react';
import { useT } from '../i18n';
import { api } from '../api/client';
import { PageHead, StatCard } from '../components/ui';

type OpsSummary = {
  generated_at?: string;
  business: {
    users: { total: number; active_1d: number; active_7d: number; active_30d: number };
    events_total: number;
    top_events: { name: string; count: number }[];
    top_pages: { name: string; count: number }[];
    devices: { name: string; count: number }[];
    regions: { name: string; count: number }[];
    locales: { name: string; count: number }[];
  };
  agent: {
    calls: number;
    ok_rate: number | null;
    errors: Record<string, number>;
    latency_ms: { p50: number; p95: number; p99: number; avg: number };
    ttft_ms: { p50: number | null; p95: number | null };
    tokens: { prompt: number; completion: number; total: number };
    cost_estimate_cny: number | null;
    by_model: { model: string; calls: number; errors: number; avg_ms: number; tokens: number; cost_cny: number | null }[];
    finish_reasons: { name: string; count: number }[];
    stream_share: number | null;
  };
  quality: {
    sessions: number;
    messages: number;
    assistant: { completed: number; failed: number; interrupted: number; generating: number };
    failure_rate: number | null;
    retry_rate: number | null;
  };
  engine: { traces: number; success_rate: number | null; spans: number; span_error_rate: number | null };
  trend: { date: string; calls: number; cost_cny: number | null; users: number }[];
};

type RecentItem = {
  id: number;
  kind: string;
  model: string | null;
  duration_ms: number;
  ttft_ms: number | null;
  total_tokens: number | null;
  cost_cny: number | null;
  ok: boolean;
  error_kind: string | null;
  finish_reason: string | null;
  stream: boolean;
  created_at: string | null;
};

function TrendChart({ trend, t }: { trend: OpsSummary['trend']; t: (k: string) => string }) {
  const max = Math.max(1, ...trend.map((d) => d.calls));
  return (
    <div className="trend-chart" style={{ display: 'flex', gap: 4, alignItems: 'flex-end', height: 140 }}>
      {trend.length === 0 && <p className="empty">{t('progress.opsNoData')}</p>}
      {trend.map((d) => (
        <div key={d.date} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
          <div style={{ flex: 1, width: '100%', display: 'flex', alignItems: 'flex-end' }}>
            <div className="bar" title={`${d.calls} ${t('progress.opsCalls')}`}
              style={{ width: '100%', height: `${Math.max(3, Math.round((d.calls / max) * 100))}%`,
                background: 'var(--primary)', borderRadius: '4px 4px 0 0' }} />
          </div>
          <span className="bar-label" style={{ fontSize: 10, color: 'var(--text-dim)' }}>{d.date.slice(5)}</span>
        </div>
      ))}
    </div>
  );
}

export default function Ops() {
  const t = useT();
  const [summary, setSummary] = useState<OpsSummary | null>(null);
  const [recent, setRecent] = useState<RecentItem[]>([]);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, r] = await Promise.all([
          api.get<OpsSummary>('/ops/metrics/summary'),
          api.get<{ items: RecentItem[] }>('/ops/metrics/recent?limit=20'),
        ]);
        if (cancelled) return;
        setSummary(s);
        setRecent(r.items || []);
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const s = summary;
  const formatCost = (cost: number | null) => cost === null ? t('progress.opsCostUnknown') : `¥${cost}`;
  return (
    <div className="page">
      <PageHead title={t('progress.opsHeading')} sub={t('progress.opsDescription')} />
      {loading && <p className="empty">{t('progress.opsLoading')}</p>}
      {err && <p className="empty" style={{ color: 'var(--danger)' }}>{t('progress.opsLoadError')}: {err}</p>}
      {!loading && !err && s && (
        <>
          <div className="card"><div className="card-body">
            <h3 className="card-title">{t('progress.opsBusiness')}</h3>
            <div className="grid grid-4">
              <StatCard label={t('progress.opsUsers')} value={String(s.business.users.total)} />
              <StatCard label={t('progress.opsActive1d')} value={String(s.business.users.active_1d)} />
              <StatCard label={t('progress.opsActive7d')} value={String(s.business.users.active_7d)} />
              <StatCard label={t('progress.opsActive30d')} value={String(s.business.users.active_30d)} />
            </div>
            <div className="grid grid-3">
              <div><h4 className="sub">{t('progress.opsTopEvents')}</h4>
                <ul className="ops-list">{s.business.top_events.map((e) => <li key={e.name}>{e.name} <b>{e.count}</b></li>)}</ul></div>
              <div><h4 className="sub">{t('progress.opsTopPages')}</h4>
                <ul className="ops-list">{s.business.top_pages.map((e) => <li key={e.name}>{e.name} <b>{e.count}</b></li>)}</ul></div>
              <div><h4 className="sub">{t('progress.opsRegions')}</h4>
                <ul className="ops-list">{s.business.regions.map((e) => <li key={e.name}>{e.name} <b>{e.count}</b></li>)}</ul></div>
            </div>
          </div></div>

          <div className="card"><div className="card-body">
            <h3 className="card-title">{t('progress.opsEngineering')}</h3>
            <div className="grid grid-4">
              <StatCard label={t('progress.opsCalls')} value={String(s.agent.calls)} />
              <StatCard label={t('progress.opsOkRate')} value={s.agent.ok_rate === null ? '—' : `${s.agent.ok_rate}%`} />
              <StatCard label={t('progress.opsP95Latency')} value={`${s.agent.latency_ms.p95} ms`} />
              <StatCard label={t('progress.opsCost')} value={formatCost(s.agent.cost_estimate_cny)} />
            </div>
            <div className="grid grid-3">
              <div><h4 className="sub">{t('progress.opsLatency')}</h4>
                <ul className="ops-list">
                  <li>{t('progress.opsP50')} <b>{s.agent.latency_ms.p50} ms</b></li>
                  <li>{t('progress.opsP95')} <b>{s.agent.latency_ms.p95} ms</b></li>
                  <li>{t('progress.opsP99')} <b>{s.agent.latency_ms.p99} ms</b></li>
                  <li>{t('progress.opsAvg')} <b>{s.agent.latency_ms.avg} ms</b></li>
                </ul></div>
              <div><h4 className="sub">{t('progress.opsErrors')}</h4>
                {Object.keys(s.agent.errors).length === 0 && <p className="empty">{t('progress.opsNoData')}</p>}
                <ul className="ops-list">{Object.entries(s.agent.errors).map(([k, v]) => <li key={k}>{k} <b>{v}</b></li>)}</ul></div>
              <div><h4 className="sub">{t('progress.opsTokens')}</h4>
                <ul className="ops-list">
                  <li>{t('progress.opsPromptTokens')} <b>{s.agent.tokens.prompt}</b></li>
                  <li>{t('progress.opsCompletionTokens')} <b>{s.agent.tokens.completion}</b></li>
                  <li>{t('progress.opsTotalTokens')} <b>{s.agent.tokens.total}</b></li>
                </ul></div>
            </div>
            {s.agent.by_model.length > 0 && (
              <div className="table-wrap"><table className="tbl">
                <thead><tr><th>{t('progress.opsModel')}</th><th>{t('progress.opsCalls')}</th><th>{t('progress.opsErrors')}</th><th>{t('progress.opsAvg')}</th><th>{t('progress.opsTotalTokens')}</th><th>{t('progress.opsCost')}</th></tr></thead>
                <tbody>{s.agent.by_model.map((m) => <tr key={m.model}><td>{m.model}</td><td>{m.calls}</td><td>{m.errors}</td><td>{m.avg_ms} ms</td><td>{m.tokens}</td><td>{formatCost(m.cost_cny)}</td></tr>)}</tbody>
              </table></div>
            )}
          </div></div>

          <div className="card"><div className="card-body">
            <h3 className="card-title">{t('progress.opsQuality')}</h3>
            <div className="grid grid-4">
              <StatCard label={t('progress.opsSessions')} value={String(s.quality.sessions)} />
              <StatCard label={t('progress.opsMessages')} value={String(s.quality.messages)} />
              <StatCard label={t('progress.opsFailureRate')} value={s.quality.failure_rate === null ? '—' : `${s.quality.failure_rate}%`} />
              <StatCard label={t('progress.opsRetryRate')} value={s.quality.retry_rate === null ? '—' : `${s.quality.retry_rate}%`} />
            </div>
            <div className="grid grid-3">
              <div><h4 className="sub">{t('progress.opsAssistantStatus')}</h4>
                <ul className="ops-list">
                  <li>{t('progress.opsCompleted')} <b>{s.quality.assistant.completed}</b></li>
                  <li>{t('progress.opsInterrupted')} <b>{s.quality.assistant.interrupted}</b></li>
                  <li>{t('progress.opsFailed')} <b>{s.quality.assistant.failed}</b></li>
                </ul></div>
              <div><h4 className="sub">{t('progress.opsEngine')}</h4>
                <ul className="ops-list">
                  <li>{t('progress.opsTraces')} <b>{s.engine.traces}</b></li>
                  <li>{t('progress.opsEngineSuccess')} <b>{s.engine.success_rate === null ? '—' : `${s.engine.success_rate}%`}</b></li>
                  <li>{t('progress.opsSpans')} <b>{s.engine.spans}</b></li>
                </ul></div>
              <div><h4 className="sub">{t('progress.opsFinishReasons')}</h4>
                {s.agent.finish_reasons.length === 0 && <p className="empty">{t('progress.opsNoData')}</p>}
                <ul className="ops-list">{s.agent.finish_reasons.map((e) => <li key={e.name}>{e.name} <b>{e.count}</b></li>)}</ul></div>
            </div>
          </div></div>

          <div className="card"><div className="card-body">
            <h3 className="card-title">{t('progress.opsTrend')}</h3>
            <TrendChart trend={s.trend} t={t} />
          </div></div>

          <div className="card"><div className="card-body">
            <h3 className="card-title">{t('progress.opsRecent')}</h3>
            <div className="table-wrap"><table className="tbl">
              <thead><tr><th>#</th><th>{t('progress.opsModel')}</th><th>{t('progress.opsKind')}</th><th>ms</th><th>{t('progress.opsTotalTokens')}</th><th>{t('progress.opsStatus')}</th><th>{t('progress.opsFinish')}</th><th>¥</th></tr></thead>
              <tbody>{recent.map((m) => <tr key={m.id}>
                <td>{m.id}</td><td>{m.model || '—'}</td><td>{m.kind}</td><td>{m.duration_ms}</td>
                <td>{m.total_tokens ?? '—'}</td>
                <td>{m.ok ? 'ok' : (m.error_kind || 'error')}</td>
                <td>{m.finish_reason || '—'}</td><td>{formatCost(m.cost_cny)}</td>
              </tr>)}</tbody>
            </table></div>
          </div></div>
        </>
      )}
    </div>
  );
}
