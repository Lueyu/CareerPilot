import { getLocale, useT } from '../i18n';
import { formatProgressDate } from '../lib/coreLoop';
import React from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { DemoBanner, PageHead, StatusChip, plainConfidence, plainLevel, OBSERVATION_COVERAGE_NOTE } from '../components/ui';
import { useApi, ProfileDetailData } from '../api/client';

function evidenceReportLink(sourceRef: string | undefined) {
  const match = /^exam:([1-9]\d*)#q([1-9]\d*)$/.exec(sourceRef ?? '');
  if (!match) return null;
  const [, examId, order] = match;
  if (!Number.isSafeInteger(Number(examId)) || !Number.isSafeInteger(Number(order))) return null;
  return `/exam/diagnosis/report?id=${examId}#exam-question-${order}`;
}

export default function ProfileDetail() {
  const t = useT();
  const { domain } = useParams();
  const navigate = useNavigate();
  const { data, loading, error } = useApi<ProfileDetailData>(domain ? `/profile/${encodeURIComponent(domain)}` : null);
  const state = data?.state;
  return <div>
    <PageHead title={data?.skill.name || t('progress.profileDetailHeading')} sub={t('progress.profileDetailCoverage', { coverage: state?.evidence_coverage ?? '—', observations: data?.evidence.length ?? '—' })}>
      <button className="btn btn-outline" onClick={() => navigate('/profile')}>{t('progress.profileDetailBack')}</button>
      <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>{t('progress.addEvidence')}</button>
    </PageHead>
    <DemoBanner />
    {loading && <p className="hint">{t('progress.profileDetailLoading')}</p>}
    {error && <div role="alert" className="demo-banner demo-banner-err">{t('progress.profileDetailLoadError')}{error}</div>}
    {!loading && !error && !data && <p className="hint">{t('progress.profileDetailNoDomain')}</p>}
    {data && state && <>
      <div className="grid grid-2" style={{ marginBottom: 18 }}>
        <div className="card">
          <div className="card-head"><span className="card-title">{t('progress.profileDetailEstimate')}</span><StatusChip status={state.status} /></div>
          <div style={{ fontSize: 44, fontWeight: 750 }}>{state.level == null ? t('progress.unknown') : `${state.level.toFixed(1)}/5`}</div>
          <p className="plain-hint">{plainLevel(state.level)}</p>
          <p className="hint">{t('progress.profileDetailConfidence', { confidence: plainConfidence(state.confidence) })}{OBSERVATION_COVERAGE_NOTE}</p>
          <p className="hint">{t('progress.profileDetailLastAssessment', { date: formatProgressDate(state.last_assessed_at) || '—' })}</p>
        </div>
        <div className="card">
          <div className="card-head"><span className="card-title">{t('progress.profileDetailBreakdown')}</span></div>
          {Object.entries(state.dimension_scores || {}).length === 0 && <p className="hint">{t('progress.profileDetailNoEstimate')}</p>}
          {Object.entries(state.dimension_scores || {}).map(([name, score]) => <div key={name} className="dim-row">
            <span className="dim-name">{name}</span><div className="dim-track"><div className="dim-fill" style={{ width: `${score / 5 * 100}%` }} /></div><span className="dim-val">{score.toFixed(1)}</span>
          </div>)}
        </div>
      </div>
      <div className="card">
        <div className="card-head"><span className="card-title">{t('progress.profileDetailObservations')}</span><span className="card-sub">{t('progress.evidenceCount', { count: data.evidence.length })}</span></div>
        {data.evidence.some(e=>e.grade==='OBSERVED_UNCALIBRATED') && <p className="hint">{t('progress.uncalibratedScope')}</p>}
        {data.evidence.length === 0 && <p className="hint">{t('progress.profileDetailNoObservations')}</p>}
        {data.evidence.map((e, i) => {
          const reportLink = evidenceReportLink(e.source_ref);
          return <div className="list-row" key={e.id || i}>
            <div className="list-ic">🧾</div><div className="list-main"><div className="list-title">{e.title}</div><div className="list-sub">{e.detail}</div><div className="list-sub">{e.short_code} · {formatProgressDate(e.occurred_at || e.created_at)} · {e.grade==='OBSERVED_UNCALIBRATED'?t('progress.uncalibratedObservation'):e.grade}</div>
              {e.observation && <><p>{t('progress.profileDetailPerformance')}{e.observation.score ?? '—'}/{e.observation.max_points ?? '—'}</p><p>{t('progress.profileDetailAnswerExcerpt')}{e.observation.answer_excerpt || t('progress.unanswered')}</p><p>{t('progress.profileDetailMissingCriteria')}{(e.observation.criteria || []).filter(c => c.status === 'missing').map(c => c.title).join(getLocale() === 'zh-CN' ? '、' : ', ') || t('progress.notRecorded')}</p></>}
              {reportLink && <Link to={reportLink}>{t('progress.profileDetailReport')}</Link>}
            </div>
          </div>;
        })}
        <button className="btn btn-outline btn-sm" onClick={() => navigate('/learning')}>{t('progress.profileDetailTask')}</button>
      </div>
    </>}
  </div>;
}
