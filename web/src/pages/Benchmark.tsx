import { useT } from '../i18n';
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, StatusChip, plainConfidence, plainLevel, OBSERVATION_COVERAGE_NOTE } from '../components/ui';
import { useApi, BenchmarkData } from '../api/client';


export default function Benchmark() {
  const t = useT();
  const navigate = useNavigate();
  const { data, loading, error } = useApi<BenchmarkData>('/benchmark');

  const rows = data?.rows
    ? data.rows.map((r: any) => ({
        skill_key: r.skill?.key ?? r.skill_key,
        skill: r.skill?.name ?? r.skill,
        weight_pct: r.weight_pct,
        target_level: r.target_level,
        current_level: r.current_level,
        status: r.status as string,
        evidence_coverage: r.evidence_coverage,
        confidence: r.confidence,
        next_step: r.next_step,
      }))
    : [];

  const role = data?.standard?.role || t('progress.notSet');
  const level = data?.standard?.level || t('progress.notSet');
  const jd = data?.standard?.jd_sample_count ?? '—';
  const version = data?.standard?.version ? `benchmark ${data.standard.version}` : '—';

  const nBelow = rows.filter((r) => r.status === 'BELOW_TARGET' || r.status === 'NEEDS_VALIDATION' || r.status === 'UNKNOWN').length;
  const nMeets = rows.filter((r) => r.status === 'MEETS_TARGET').length;
  const nAbove = rows.filter((r) => r.status === 'ABOVE_TARGET').length;

  // 加权差距
  const wSum = rows.reduce((s, r) => s + r.weight_pct, 0) || 1;
  const gap = !rows.length || rows.some(r => r.current_level == null) ? null : rows.reduce((s, r) => s + r.weight_pct * Math.max(0, r.target_level - (r.current_level ?? 0)), 0) / wSum;

  return (
    <div>
      <PageHead
        title={t('progress.benchmarkHeading')}
        sub={t('progress.benchmarkDescription')}
      >
        <button className="btn btn-outline" onClick={() => navigate('/benchmark')}>{t('progress.benchmarkRefresh')}</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>{t('progress.benchmarkDiagnose')}</button>
      </PageHead>
      <DemoBanner text={t('progress.benchmarkDemo')} />

      {/* 对标总卡 */}
      <div className="card" style={{ marginBottom: 18 }}>
        <div className="bench-head">
          <div>
            <div className="hero-label" style={{ color: 'var(--text-4)' }}>{t('progress.benchmarkRole')}</div>
            <h2 style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
              {role}
              <span className="chip chip-purple" style={{ marginLeft: 10 }}>{t('progress.benchmarkLevel') + ' '}{level}</span>
            </h2>
            <div className="bench-meta">
              <span>{t('progress.benchmarkSample') + ' '}{t('progress.benchmarkJobDescriptions', { count: jd })}</span>
              <i className="dot-sep" />
              <span>{t('progress.benchmarkWeightSource') + ' '}<b>{t('progress.benchmarkInitialEstimates')}</b></span>
              <i className="dot-sep" />
              <span>{version}</span>
            </div>
          </div>
          <div className="bench-gap">
            <div className="big">{gap == null ? t('progress.unknown') : gap.toFixed(1)}<span>/5</span></div>
            <div className="note">{t('progress.benchmarkWeightedGap')}</div>
          </div>
        </div>
        <div className="grid grid-4" style={{ marginTop: 20 }}>
          <div className="mini-stat"><b>{rows.length}</b><span>{t('progress.benchmarkDomainCount')}</span></div>
          <div className="mini-stat"><b>{nBelow}</b><span>{t('progress.benchmarkBelowOrUnverified')}</span></div>
          <div className="mini-stat"><b>{nMeets}</b><span>{t('progress.atTarget')}</span></div>
          <div className="mini-stat"><b>{nAbove}</b><span>{t('progress.aboveTarget')}</span></div>
        </div>
      </div>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>{t('progress.loadingLive')}</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>{t('progress.benchmarkLoadError', { error })}</div>}

      <div className="card">
        <div className="card-head">
          <span className="card-title">{t('progress.benchmarkDetails')}</span>
          <span className="card-sub">{t('progress.benchmarkPriorityFormula')}</span>
        </div>
        <p className="hint">{OBSERVATION_COVERAGE_NOTE}</p>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t('progress.benchmarkCompetency')}</th>
                <th>{t('progress.benchmarkWeight')}</th>
                <th>{t('progress.benchmarkTarget')}</th>
                <th>{t('progress.benchmarkCurrent')}</th>
                <th>{t('progress.benchmarkStatus')}</th>
                <th>{t('progress.benchmarkCoverage')}</th>
                <th>{t('progress.benchmarkCoverageNote')}</th>
                <th>{t('progress.benchmarkNext')}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.skill_key}>
                  <td className="strong" style={{ cursor: 'pointer' }} onClick={() => navigate(`/profile/${r.skill_key}`)}>{r.skill}</td>
                  <td className="num">{r.weight_pct}%</td>
                  <td className="num">{r.target_level}</td>
                  <td>
                    <span className="num">{r.current_level != null ? r.current_level.toFixed(2) : '—'}</span>
                    <div className="plain-hint">{plainLevel(r.current_level)}</div>
                  </td>
                  <td><StatusChip status={r.status} /></td>
                  <td className="num">{t('progress.evidenceCount', { count: r.evidence_coverage })}</td>
                  <td>
                    <div className="plain-hint">{plainConfidence(r.confidence)}</div>
                  </td>
                  <td><span className="action-link" onClick={() => navigate('/learning')}>{r.next_step}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card mt-18">
        <div className="card-head">
          <span className="card-title">{t('progress.benchmarkPriority')}</span>
          <span className="card-sub">{t('progress.benchmarkPriorityDescription')}</span>
        </div>
        <p className="hint">{t('progress.benchmarkMissingEvidence')}</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {[...rows].filter(r => r.current_level != null)
            .map((r) => ({ ...r, priority: r.weight_pct * Math.max(0, r.target_level - (r.current_level ?? 0)) }))
            .sort((a, b) => b.priority - a.priority)
            .map((r) => (
              <div key={r.skill_key} className="bench-row">
                <div className="bench-row-name">{r.skill}</div>
                <div className="bench-bar">
                  <div className="bench-bar-fill" style={{ width: `${Math.min(100, (r.priority / 40) * 100)}%`, background: r.status === 'MEETS_TARGET' || r.status === 'ABOVE_TARGET' ? 'var(--green)' : 'var(--accent-grad)' }} />
                </div>
                <div className="bench-row-val">{r.priority.toFixed(1)}</div>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
