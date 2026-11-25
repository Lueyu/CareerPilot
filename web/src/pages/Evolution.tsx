import { getLocale, t as translate, useT } from '../i18n';
import React, { useCallback, useEffect, useState } from 'react';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api } from '../api/client';

type Metric = { baseline: number | null; candidate: number | null; delta: number | null; note?: string };
type EvolutionCandidate = {
  id: number;
  short_code: string;
  title: string;
  kind: string;
  status: string;
  body: { baseline?: Record<string, unknown>; candidate?: Record<string, unknown> } | null;
  rationale: string;
  source_data: { signals?: Record<string, number> } | null;
  created_at: string | null;
  approved_at: string | null;
  run: { metrics: Record<string, Metric>; sample_size: number; regression: Record<string, number> | null; verdict: string } | null;
};

const STATUS_META: Record<string, { cls: string; label: string }> = {
  PROPOSED: { cls: 'chip-slate', get label() { return translate('progress.evolutionStatusProposed'); } },
  TESTING: { cls: 'chip-amber', get label() { return translate('progress.evolutionStatusTrial'); } },
  EVALUATED: { cls: 'chip-amber', get label() { return translate('progress.evolutionStatusEvaluated'); } },
  APPROVED: { cls: 'chip-purple', get label() { return translate('progress.evolutionStatusApproved'); } },
  ACTIVE: { cls: 'chip-green', get label() { return translate('progress.evolutionStatusActive'); } },
  REJECTED: { cls: 'chip-slate', get label() { return translate('progress.evolutionStatusRejected'); } },
  ROLLED_BACK: { cls: 'chip-slate', get label() { return translate('progress.evolutionStatusRolledBack'); } },
};
const VERDICT_META: Record<string, { cls: string; label: string }> = {
  POSITIVE: { cls: 'chip-green', get label() { return translate('progress.evolutionPositive'); } },
  NEGATIVE: { cls: 'chip-red', get label() { return translate('progress.evolutionNegative'); } },
  INSUFFICIENT_EVIDENCE: { cls: 'chip-amber', get label() { return translate('progress.evolutionInsufficient'); } },
};

const costMetric = '单位成本';
const metricLabels: Record<string, string> = {
  [costMetric]: 'progress.evolutionUnitCostLabel',
  '弱域命中率': 'progress.evolutionWeakDomainHits',
  '覆盖域数': 'progress.evolutionDomainsCovered',
  '历史平均得分率': 'progress.evolutionHistoricalScore',
};

function metricLabel(source: string) {
  return metricLabels[source] ? translate(metricLabels[source]) : source;
}

const fmtDelta = (m: Metric) => {
  if (m.delta == null) return '—';
  const v = m.delta;
  return (v > 0 ? '+' : '') + (Number.isInteger(v) ? v : Number(v.toFixed(3)));
};
const fmtVal = (v: unknown) => {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? translate('progress.yes') : translate('progress.no');
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(3)));
  return String(v);
};
const fmtPct = (v: number | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`);

export default function Evolution() {
  const t = useT();
  const { toast } = useAppState();
  const [items, setItems] = useState<EvolutionCandidate[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [proposeBusy, setProposeBusy] = useState(false);
  const [confirmMode, setConfirmMode] = useState<'reject' | 'rollback' | null>(null);
  const [confirmCand, setConfirmCand] = useState<EvolutionCandidate | null>(null);

  const load = useCallback(async () => {
    setLoadErr(null);
    try {
      const d = await api.get<{ items: EvolutionCandidate[] }>('/evolution/candidates');
      setItems(d.items || []);
    } catch (e: any) {
      setItems([]);
      setLoadErr(e?.message || translate('progress.evolutionLoadError'));
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const propose = async () => {
    setProposeBusy(true);
    try {
      const d = await api.post<{ proposed: boolean; reason?: string; candidate?: EvolutionCandidate | null }>('/evolution/candidates');
      if (d.proposed && d.candidate) {
        toast('🧪', t('progress.evolutionCreated', { code: d.candidate.short_code }));
      } else {
        toast('ℹ️', d.reason || t('progress.evolutionCannotGenerate'));
      }
      await load();
    } catch (e: any) {
      toast('⚠️', e?.message || t('progress.evolutionGenerateError'));
    } finally {
      setProposeBusy(false);
    }
  };

  const act = async (c: EvolutionCandidate, action: 'evaluate' | 'approve' | 'reject' | 'rollback') => {
    setBusyId(c.id);
    try {
      await api.post(`/evolution/candidates/${c.id}/${action}`);
      const msg: Record<string, string> = {
        evaluate: t('progress.evolutionEvaluated', { code: c.short_code }),
        approve: t('progress.evolutionApproved', { code: c.short_code }),
        reject: t('progress.evolutionRejected', { code: c.short_code }),
        rollback: t('progress.evolutionRolledBack', { code: c.short_code }),
      };
      toast(action === 'approve' ? '✅' : action === 'reject' ? '⛔' : action === 'rollback' ? '↩️' : '🧪', msg[action]);
      setConfirmMode(null);
      setConfirmCand(null);
      await load();
    } catch (e: any) {
      toast('⚠️', e?.message || t('progress.evolutionActionError'));
    } finally {
      setBusyId(null);
    }
  };

  const activeCand = (items || []).find((c) => c.status === 'ACTIVE');
  const hasAny = (items || []).length > 0;

  return (
    <div>
      <PageHead
        title={t('progress.evolutionHeading')}
        sub={t('progress.evolutionDescription')}
      >
        <button className="btn btn-primary" disabled={proposeBusy} onClick={propose}>
          {proposeBusy ? t('progress.evolutionAnalyzing') : t('progress.evolutionGenerate')}
        </button>
      </PageHead>
      <DemoBanner />

      {/* 机制说明 */}
      <div className="card obs-summary" style={{ marginBottom: 18 }}>
        <div className="card-head">
          <span className="card-title">{t('progress.evolutionPurpose')}</span>
        </div>
        <div className="obs-summary-line">
          <span className="obs-summary-ic">🧪</span>
          <span>{t('progress.evolutionActivationDisabled')}</span>
        </div>
        <div className="obs-summary-line">
          <span className="obs-summary-ic">ℹ️</span>
          <span>
            {hasAny ? t('progress.evolutionRealData') : <>{t('progress.evolutionFirstExperiment')}</>}
          </span>
        </div>
        {activeCand && (
          <div className="obs-summary-line">
            <span className="obs-summary-ic">⚙️</span>
            <span>{t('progress.evolutionActiveStrategy', { code: activeCand.short_code, title: activeCand.title })}</span>
          </div>
        )}
      </div>

      {loadErr && (
        <div className="card" style={{ padding: 16, marginBottom: 16, borderColor: 'var(--red)' }}>
          <span style={{ color: 'var(--red)' }}>{t('progress.evolutionBackendUnavailable')}{loadErr}</span>
          <button className="btn btn-outline btn-sm" style={{ marginLeft: 12 }} onClick={load}>{t('progress.retry')}</button>
        </div>
      )}

      {items && !hasAny && !loadErr && (
        <div className="card" style={{ padding: 28, textAlign: 'center' }}>
          <div style={{ fontSize: 30, marginBottom: 8 }}>🧪</div>
          <b>{t('progress.evolutionNoCandidates')}</b>
          <p className="hint" style={{ marginTop: 6 }}>
            {t('progress.evolutionNoCandidatesExplanation')}
          </p>
        </div>
      )}

      {items && hasAny && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">{t('progress.evolutionCandidates')}</span>
            <span className="card-sub">{t('progress.evolutionCandidatesExplanation')}</span>
          </div>
          {items.map((c) => {
            const st = STATUS_META[c.status] || { cls: 'chip-slate', label: c.status };
            const vd = c.run ? VERDICT_META[c.run.verdict] || null : null;
            const sig = c.source_data?.signals || {};
            const busy = busyId === c.id;
            const terminal = c.status === 'REJECTED' || c.status === 'ROLLED_BACK';
            return (
              <div className="evo-card" key={c.id} style={{ padding: '18px 0', borderBottom: '1px solid var(--border-2)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span className="chip chip-purple">{c.short_code}</span>
                      <b style={{ fontSize: 14.5 }}>{c.title}</b>
                      <span className={`chip ${st.cls}`}>{st.label}</span>
                      {vd && <span className={`chip ${vd.cls}`}>{vd.label}</span>}
                    </div>
                    <p className="hint" style={{ margin: '6px 0 12px' }}>{c.rationale}</p>

                    {/* 基线 vs 候选 参数对比 */}
                    {c.body?.candidate && (
                      <div className="hint" style={{ fontSize: 12, marginBottom: 10 }}>
                        {t('progress.evolutionStrategyParams')}
                        {Object.entries(c.body.candidate).map(([k, v]) => {
                          const b = c.body?.baseline?.[k];
                          return (
                            <span key={k} style={{ marginRight: 10 }}>
                              <code style={{ fontSize: 11.5 }}>{k}</code> {fmtVal(b)} → <b>{fmtVal(v)}</b>
                            </span>
                          );
                        })}
                      </div>
                    )}

                    {/* 评估指标 */}
                    {c.run && (
                      <div className="evo-metrics">
                        {Object.entries(c.run.metrics).map(([k, m]) => {
                          const d = fmtDelta(m);
                          const isCost = k === costMetric;
                          const good = typeof m.delta === 'number' && m.delta > 0 && !isCost;
                          const bad = (typeof m.delta === 'number' && m.delta > 0 && isCost) || (typeof m.delta === 'number' && m.delta < 0 && !isCost);
                          const pct = m.candidate == null ? null : Math.min(100, Math.abs(m.candidate) * 30);
                          return (
                            <div className="evo-metric" key={k}>
                              <div className="evo-m-label">{metricLabel(k)}</div>
                              <div className="evo-m-value">
                                <span className={`chip ${good ? 'chip-green' : bad ? 'chip-red' : 'chip-slate'}`}>{d}</span>
                              </div>
                              <div className="evo-bar">
                                <div className="evo-bar-fill" style={{
                                  width: `${pct ?? 0}%`,
                                  background: good ? 'var(--green)' : bad ? 'var(--red)' : 'var(--border)',
                                }} />
                              </div>
                              <div className="hint" style={{ fontSize: 11 }}>
                                {t('progress.evolutionBaseline') + ' '}{fmtVal(m.baseline)}{' ' + t('progress.evolutionCandidate') + ' '}{fmtVal(m.candidate)}{m.note ? ` · ${m.note}` : ''}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    {/* 样本信号 */}
                    {Object.keys(sig).length > 0 && (
                      <div className="hint" style={{ fontSize: 12.5, marginTop: 8 }}>
                        {t('progress.evolutionSampleSignals', { exams: sig.completed_exams ?? 0, score: fmtPct(sig.avg_score_ratio), retests: fmtPct(sig.retest_rate), tasks: fmtPct(sig.task_rate) })}
                      </div>
                    )}
                    {c.run && (
                      <div className="hint" style={{ fontSize: 12.5, marginTop: 4 }}>
                        {t('progress.evolutionSampleSize', { count: c.run.sample_size, regressions: c.run.regression ? Object.entries(c.run.regression).map(([key, value]) => metricLabel(key) + ' ' + (value > 0 ? '+' : '') + value).join(getLocale() === 'zh-CN' ? '、' : ', ') : t('progress.none') })}
                        {c.approved_at ? t('progress.evolutionApprovedDate', { date: new Date(c.approved_at).toLocaleString(getLocale(), { hour12: false }) }) : ''}
                      </div>
                    )}
                  </div>
                </div>

                <div className="btn-row" style={{ marginTop: 14 }}>
                  {c.status === 'PROPOSED' && (
                    <button className="btn btn-primary" disabled={busy} onClick={() => act(c, 'evaluate')}>
                      {busy ? t('progress.evolutionEvaluating') : t('progress.evolutionEvaluate')}
                    </button>
                  )}
                  {c.status === 'EVALUATED' && (
                    <>
                      <button className="btn btn-primary" disabled title={t('progress.evolutionActivationDisabledTitle')}>{t('progress.evolutionActivationDisabledButton')}</button>
                      <button className="btn btn-outline" disabled={busy} onClick={() => { setConfirmCand(c); setConfirmMode('reject'); }}>{t('progress.evolutionReject')}</button>
                    </>
                  )}
                  {c.status === 'ACTIVE' && (
                    <button className="btn btn-outline" disabled={busy} onClick={() => { setConfirmCand(c); setConfirmMode('rollback'); }}>{t('progress.evolutionRollback')}</button>
                  )}
                  {terminal && (
                    <span className="hint" style={{ fontSize: 12.5 }}>{t('progress.evolutionFinalState')}</span>
                  )}
                </div>

                {c.run?.verdict === 'INSUFFICIENT_EVIDENCE' && c.status !== 'ACTIVE' && (
                  <div className="hint" style={{ fontSize: 12.5, marginTop: 8, color: 'var(--text-3)' }}>
                    {t('progress.evolutionSampleTooSmall', { count: c.run.sample_size })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {confirmMode && confirmCand && (
        <Modal
          title={confirmMode === 'reject' ? t('progress.evolutionRejectQuestion', { code: confirmCand.short_code }) : t('progress.evolutionRollbackQuestion', { code: confirmCand.short_code })}
          size="sm"
          onClose={() => { setConfirmMode(null); setConfirmCand(null); }}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => { setConfirmMode(null); setConfirmCand(null); }}>{t('progress.cancel')}</button>
              <button
                className="btn btn-danger"
                disabled={busyId === confirmCand.id}
                onClick={() => act(confirmCand, confirmMode)}
              >
                {confirmMode === 'reject' ? t('progress.evolutionConfirmReject') : t('progress.evolutionConfirmRollback')}
              </button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            {confirmMode === 'reject'
              ? t('progress.evolutionRejectExplanation')
              : t('progress.evolutionRollbackExplanation')}
          </p>
        </Modal>
      )}
    </div>
  );
}
