import { t, useT } from '../i18n';
import { formatProgressDate } from '../lib/coreLoop';
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, StatusChip, plainConfidence, plainLevel, OBSERVATION_COVERAGE_NOTE } from '../components/ui';
import { useApi, ProfileData, EvidenceItem } from '../api/client';


const COLOR: Record<string, string> = {
  BELOW_TARGET: 'var(--red)',
  NEEDS_VALIDATION: '#eab308',
  MEETS_TARGET: 'var(--green)',
  ABOVE_TARGET: 'var(--purple)',
  UNKNOWN: 'var(--dot)',
};

const KIND_CLS: Record<string, string> = {
  EXAM_ANSWER: 'chip-purple',
  IMPORTED_QA: 'chip-blue',
  PROJECT_COMMIT: 'chip-green',
  MEMORY_NOTE: 'chip-slate',
  EXTERNAL_LINK: 'chip-amber',
};
const KIND_NAME: Record<string, string> = {
  get EXAM_ANSWER() { return t('progress.profileEvidenceDiagnosis'); },
  get IMPORTED_QA() { return t('progress.profileEvidenceInterview'); },
  get PROJECT_COMMIT() { return t('progress.profileEvidenceProject'); },
  get MEMORY_NOTE() { return t('progress.profileEvidenceMemory'); },
  get EXTERNAL_LINK() { return t('progress.profileEvidencePractice'); },
};

export default function Profile() {
  const t = useT();
  const navigate = useNavigate();
  const { data, loading, error } = useApi<ProfileData>('/profile');

  const domains = data?.domains ?? [];
  const evidence: (EvidenceItem & { domain: string })[] = (data?.evidence ?? []).map(e => ({ ...e, domain: e.skill || '—' }));

  const overall = data?.overall;
  const focusName = data?.focus?.name || '';
  const evidenceTotal = overall?.evidence_total ?? '—';
  const overallLevel = overall?.level != null ? overall.level.toFixed(2) : t('progress.unknown');

  return (
    <div>
      <PageHead
        title={t('progress.profileHeading')}
        sub={t('progress.profileSubtitle', { count: evidenceTotal })}
      >
        <button className="btn btn-outline" onClick={() => navigate('/profile')}>{t('progress.profileReevaluate')}</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>{t('progress.addEvidence')}</button>
      </PageHead>
      <DemoBanner />

      <div className="hero-card" style={{ marginBottom: 18 }}>
        <div className="hero-top">
          <div>
            <div className="hero-label">{t('progress.profileOverview')}</div>
            <div className="hero-title">
              {focusName}{' ' + t('progress.profileEstimate') + ' '}{overallLevel}/5
              <span className="hero-chip">{plainConfidence(overall?.confidence)}</span>
            </div>
            <div className="hero-plain">
              {t('progress.profilePlainLevel', { level: plainLevel(overall?.level) })}{OBSERVATION_COVERAGE_NOTE}
            </div>
          </div>
          <div className="hero-score">
            <div className="big">{t('progress.profileEvidenceTotal', { count: evidenceTotal })}</div>
            <div className="note">{t('progress.profileDomainsCovered', { count: domains.length })}</div>
          </div>
        </div>
        <div className="hero-body">
          <div className="seg-gap" style={{ maxWidth: 620 }}>
            {t('progress.profileDistribution')}{['OBSERVED_UNCALIBRATED', 'VERIFIED', 'HIGH', 'MEDIUM', 'LOW'].map((g) => `${g === 'OBSERVED_UNCALIBRATED' ? t('progress.uncalibratedObservation') : g} ${evidence.filter((e) => e.grade === g).length}`).join(' · ')}
          </div>
          {evidence.some(e=>e.grade==='OBSERVED_UNCALIBRATED') && <p className="hint">{t('progress.uncalibratedScope')}</p>}
        </div>
      </div>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>{t('progress.loadingLive')}</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>{t('progress.workspaceLoadError', { error })}</div>}

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        {domains.map((d: any) => (
          <div className="domain-card" key={d.key} onClick={() => navigate(`/profile/${d.key}`)}>
            <div className="domain-card-top">
              <div>
                <div className="domain-card-name">{d.name}</div>
                <div className="domain-card-weight">{t('progress.profileDomainEvidence', { count: d.evidence ?? d.evidence_coverage ?? 0 })}</div>
              </div>
              <StatusChip status={d.status} />
            </div>
            <div className="domain-card-level">
              {d.level != null ? d.level.toFixed(1) : '—'}<span>/5</span>
            </div>
            <div className="domain-track">
              <div className="domain-track-fill" style={{ width: `${((d.level ?? 0) / 5) * 100}%`, background: COLOR[d.status] }} />
            </div>
            <div className="domain-card-plain">{plainLevel(d.level)}</div>
            <div className="domain-card-foot">
              <span>{plainConfidence(d.confidence)}</span>
              <span>{t('progress.profileLastEvaluated') + ' '}{d.last_assessed_at ? d.last_assessed_at.slice(5, 10) : '—'}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">{t('progress.profileDetails')}</span>
          <span className="card-sub">{t('progress.profileLatestEvidence', { count: Math.min(20, evidence.length) })}</span>
        </div>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>{t('progress.profileTableId')}</th>
                <th>{t('progress.profileTableType')}</th>
                <th>{t('progress.profileTableDomain')}</th>
                <th>{t('progress.profileTableSummary')}</th>
                <th>{t('progress.profileTableResult')}</th>
                <th>{t('progress.profileTableDate')}</th>
              </tr>
            </thead>
            <tbody>{!loading && !error && evidence.length === 0 && <tr><td colSpan={6}>{t('progress.profileNoEvidence')}</td></tr>}
              {evidence.slice(0, 20).map((e: any, i: number) => (
                <tr key={e.short_code || i}>
                  <td className="num strong">{e.short_code || `EV-${i + 1}`}</td>
                  <td><span className={`chip ${KIND_CLS[e.kind] || 'chip-slate'}`}>{KIND_NAME[e.kind] || e.kind}</span></td>
                  <td>{e.domain}</td>
                  <td style={{ maxWidth: 420 }}>{e.title}{e.detail ? ` — ${e.detail}` : ''}</td>
                  <td><b>{e.grade==='OBSERVED_UNCALIBRATED'?t('progress.uncalibratedObservation'):e.grade}</b></td>
                  <td className="num">{formatProgressDate(e.occurred_at || e.created_at) || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
