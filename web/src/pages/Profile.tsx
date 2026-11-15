import React from 'react';
import { useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, StatusChip } from '../components/ui';
import { useApi, ProfileData, EvidenceItem } from '../api/client';
import { DOMAINS, EVIDENCE } from '../data/demo';

const COLOR: Record<string, string> = {
  BELOW_TARGET: 'var(--red)',
  NEEDS_VALIDATION: '#eab308',
  MEETS_TARGET: 'var(--green)',
  ABOVE_TARGET: 'var(--purple)',
  UNKNOWN: '#c7cbd3',
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

  const domains = data?.domains?.length ? data.domains : DOMAINS.map((d) => ({
    key: d.key, name: d.name, weight_pct: d.weight, level: d.current, status: d.status,
    confidence: d.confidence, evidence_coverage: d.evidence,
  }));
  const evidence: (EvidenceItem & { domain: string })[] = data?.evidence?.length
    ? data.evidence.map((e: any) => ({ ...e, domain: e.skill || '—' }))
    : EVIDENCE.map((e) => ({
        short_code: e.id, kind: e.type === '诊断' ? 'EXAM_ANSWER' : e.type === '面经' ? 'IMPORTED_QA' : e.type === '项目' ? 'PROJECT_COMMIT' : 'MEMORY_NOTE',
        title: e.summary, grade: e.grade || 'MEDIUM', domain: e.domain,
        occurred_at: e.date, skill: e.domain,
      }));

  const overall = data?.overall;
  const focusName = data?.focus?.name || 'Agent Evaluation';
  const evidenceTotal = overall?.evidence_total ?? 27;
  const overallLevel = overall?.level != null ? overall.level.toFixed(2) : '2.7';
  const overallConf = overall?.confidence != null ? overall.confidence.toFixed(2) : '0.71';

  return (
    <div>
      <PageHead
        title="能力画像"
        sub={`基于 ${evidenceTotal} 条证据的能力估计 · 置信度反映证据充分性`}
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
              <span className="hero-chip">置信度 {overallConf}</span>
            </div>
          </div>
          <div className="hero-score">
            <div className="big">{evidenceTotal}<span>条证据</span></div>
            <div className="note">覆盖 {domains.length} 个能力域</div>
          </div>
        </div>
        <div className="hero-body">
          <div className="seg-gap" style={{ maxWidth: 620 }}>
            证据等级分布：{['VERIFIED', 'HIGH', 'MEDIUM', 'LOW'].map((g) => `${g} ${evidence.filter((e) => e.grade === g).length}`).join(' · ')}
          </div>
        </div>
      </div>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载实时数据…</div>}
      {error && <div className="demo-banner" style={{ marginBottom: 12, background: '#fef2f2', color: '#b91c1c' }}>后端不可用（{error}），以下为静态演示数据</div>}

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        {domains.map((d: any) => (
          <div className="domain-card" key={d.key} onClick={() => navigate(`/profile/${d.key}`)}>
            <div className="domain-card-top">
              <div>
                <div className="domain-card-name">{d.name}</div>
                <div className="domain-card-weight">证据 {d.evidence_coverage} 条</div>
              </div>
              <StatusChip status={d.status} />
            </div>
            <div className="domain-card-level">
              {d.level != null ? d.level.toFixed(1) : '—'}<span>/5</span>
            </div>
            <div className="domain-track">
              <div className="domain-track-fill" style={{ width: `${((d.level ?? 0) / 5) * 100}%`, background: COLOR[d.status] }} />
            </div>
            <div className="domain-card-foot">
              <span>置信 {d.confidence != null ? d.confidence.toFixed(2) : '—'}</span>
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
            <tbody>
              {evidence.slice(0, 20).map((e: any, i: number) => (
                <tr key={e.short_code || i}>
                  <td className="num strong">{e.short_code || `EV-${i + 1}`}</td>
                  <td><span className={`chip ${KIND_CLS[e.kind] || 'chip-slate'}`}>{KIND_NAME[e.kind] || e.kind}</span></td>
                  <td>{e.domain}</td>
                  <td style={{ maxWidth: 420 }}>{e.title}{e.detail ? ` — ${e.detail}` : ''}</td>
                  <td><b>{e.grade}</b></td>
                  <td className="num">{(e.occurred_at || '').slice(0, 10) || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
