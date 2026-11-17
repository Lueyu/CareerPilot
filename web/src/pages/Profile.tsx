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
  EXAM_ANSWER: '诊断',
  IMPORTED_QA: '面经',
  PROJECT_COMMIT: '项目',
  MEMORY_NOTE: '记忆',
  EXTERNAL_LINK: '实践',
};

export default function Profile() {
  const navigate = useNavigate();
  const { data, loading, error } = useApi<ProfileData>('/profile');

  const domains = data?.domains ?? [];
  const evidence: (EvidenceItem & { domain: string })[] = (data?.evidence ?? []).map(e => ({ ...e, domain: e.skill || '—' }));

  const overall = data?.overall;
  const focusName = data?.focus?.name || '';
  const evidenceTotal = overall?.evidence_total ?? '—';
  const overallLevel = overall?.level != null ? overall.level.toFixed(2) : '未知';

  return (
    <div>
      <PageHead
        title="能力画像"
        sub={`基于 ${evidenceTotal} 条证据的能力估计 · 观察覆盖仅提示已有记录数量`}
      >
        <button className="btn btn-outline" onClick={() => navigate('/profile')}>↻ 重新评估</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>补充证据</button>
      </PageHead>
      <DemoBanner />

      <div className="hero-card" style={{ marginBottom: 18 }}>
        <div className="hero-top">
          <div>
            <div className="hero-label">能力画像 · 综合估计</div>
            <div className="hero-title">
              {focusName} 整体估计 {overallLevel}/5
              <span className="hero-chip">{plainConfidence(overall?.confidence)}</span>
            </div>
            <div className="hero-plain">
              通俗地说：{plainLevel(overall?.level)}。{OBSERVATION_COVERAGE_NOTE}
            </div>
          </div>
          <div className="hero-score">
            <div className="big">{evidenceTotal}<span>条证据</span></div>
            <div className="note">覆盖 {domains.length} 个能力域</div>
          </div>
        </div>
        <div className="hero-body">
          <div className="seg-gap" style={{ maxWidth: 620 }}>
            证据记录分布：{['OBSERVED_UNCALIBRATED', 'VERIFIED', 'HIGH', 'MEDIUM', 'LOW'].map((g) => `${g === 'OBSERVED_UNCALIBRATED' ? '未校准观察' : g} ${evidence.filter((e) => e.grade === g).length}`).join(' · ')}
          </div>
          {evidence.some(e=>e.grade==='OBSERVED_UNCALIBRATED') && <p className="hint">未校准观察只记录本次表现，不等于可靠能力判断。</p>}
        </div>
      </div>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载实时数据…</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>后端不可用（{error}），无法读取当前空间数据，请稍后重试</div>}

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        {domains.map((d: any) => (
          <div className="domain-card" key={d.key} onClick={() => navigate(`/profile/${d.key}`)}>
            <div className="domain-card-top">
              <div>
                <div className="domain-card-name">{d.name}</div>
                <div className="domain-card-weight">证据 {d.evidence ?? d.evidence_coverage ?? 0} 条</div>
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
              <span>最近评估 {d.last_assessed_at ? d.last_assessed_at.slice(5, 10) : '—'}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">证据明细</span>
          <span className="card-sub">最新 {Math.min(20, evidence.length)} 条</span>
        </div>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>ID</th>
                <th>类型</th>
                <th>能力域</th>
                <th>证据摘要</th>
                <th>结果</th>
                <th>日期</th>
              </tr>
            </thead>
            <tbody>{!loading && !error && evidence.length === 0 && <tr><td colSpan={6}>还没有作答证据。完成一次诊断后可在这里查看。</td></tr>}
              {evidence.slice(0, 20).map((e: any, i: number) => (
                <tr key={e.short_code || i}>
                  <td className="num strong">{e.short_code || `EV-${i + 1}`}</td>
                  <td><span className={`chip ${KIND_CLS[e.kind] || 'chip-slate'}`}>{KIND_NAME[e.kind] || e.kind}</span></td>
                  <td>{e.domain}</td>
                  <td style={{ maxWidth: 420 }}>{e.title}{e.detail ? ` — ${e.detail}` : ''}</td>
                  <td><b>{e.grade==='OBSERVED_UNCALIBRATED'?'未校准观察':e.grade}</b></td>
                  <td className="num">{(e.occurred_at || e.created_at || '').slice(0, 10) || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
