import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { DemoBanner, PageHead } from '../components/ui';
import { PROJECTS, PROJECT_FINDINGS } from '../data/demo';

const LEVEL_CLS: Record<string, string> = {
  VERIFIED: 'ev-verified',
  HIGH: 'ev-claim',
  UNVERIFIED_CLAIM: 'ev-claim',
};

export default function ProjectReview() {
  const { id } = useParams();
  const navigate = useNavigate();
  const p = PROJECTS.find((x) => x.id === id) ?? PROJECTS[0];
  const verified = PROJECT_FINDINGS.filter((f) => f.level === 'VERIFIED').length;

  return (
    <div>
      <PageHead title={`${p.name} · 项目审查`} sub={`${p.sub} · 最近扫描 ${p.scanned}`}>
        <button className="btn btn-outline" onClick={() => navigate('/assets')}>← 返回项目资产</button>
        <button className="btn btn-primary" onClick={() => navigate('/assets/pipeline')}>更新职业展示 →</button>
      </PageHead>
      <DemoBanner text="DEMO · 下列扫描、文件路径、SHA 与 PR 均为示例，未对真实仓库完成审计。" />

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        <div className="mini-stat"><b>{PROJECT_FINDINGS.length}</b><span>发现项</span></div>
        <div className="mini-stat"><b>{verified}</b><span>已核验</span></div>
        <div className="mini-stat"><b>{PROJECT_FINDINGS.filter((f) => f.level === 'HIGH').length}</b><span>高置信</span></div>
        <div className="mini-stat"><b>{PROJECT_FINDINGS.filter((f) => f.level === 'UNVERIFIED_CLAIM').length}</b><span>未验证声明</span></div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">证据发现</span>
          <span className="card-sub">每条发现都引用文件与证据 ID</span>
        </div>
        {PROJECT_FINDINGS.map((f) => (
          <div className="list-row" key={f.title} style={{ alignItems: 'flex-start' }}>
            <div className="list-ic">{f.level === 'VERIFIED' ? '✅' : f.level === 'HIGH' ? '🔶' : '⚠️'}</div>
            <div className="list-main">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <span className={`ev-chip ${LEVEL_CLS[f.level]}`}>{f.level}</span>
                <span className="chip chip-slate">{f.domain}</span>
                <b style={{ fontSize: 13.5 }}>{f.title}</b>
              </div>
              <div className="list-sub" style={{ marginTop: 4 }}>{f.detail}</div>
              <div className="q-meta">
                <span className="chip chip-slate">📄 {f.file}</span>
                <span className="chip chip-slate">{f.evidence}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
