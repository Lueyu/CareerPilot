import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { DemoBanner, PageHead, StatusChip, plainConfidence, plainLevel } from '../components/ui';
import { DOMAINS, EVIDENCE } from '../data/demo';

const DIMENSIONS = ['理论理解', '工程实践', '问题诊断', '表达沟通', 'AI 协作'];

export default function ProfileDetail() {
  const { domain } = useParams();
  const navigate = useNavigate();
  const d = DOMAINS.find((x) => x.key === domain) ?? DOMAINS[0];
  const evs = EVIDENCE.filter((e) => e.domain === d.name);
  const list = evs.length > 0 ? evs : EVIDENCE.slice(0, 3);
  const dimScores = DIMENSIONS.map((name, i) => ({
    name,
    score: Math.min(5, Math.max(1, d.current + ((i % 3) - 1) * 0.6 + 0.2)),
  }));

  return (
    <div>
      <PageHead title={d.name} sub={`能力详情 · 权重 ${d.weight}% · ${d.evidence} 条证据`}>
        <button className="btn btn-outline" onClick={() => navigate('/profile')}>← 返回能力画像</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>补充证据</button>
      </PageHead>
      <DemoBanner />

      <div className="grid grid-2" style={{ marginBottom: 18 }}>
        <div className="card">
          <div className="card-head">
            <span className="card-title">估计与置信</span>
            <StatusChip status={d.status} />
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <div style={{ fontSize: 44, fontWeight: 750, letterSpacing: '-0.02em' }}>{d.current.toFixed(1)}<span style={{ fontSize: 16, color: 'var(--text-4)', fontWeight: 500 }}>/5</span></div>
            <div className="chip chip-slate">目标 {d.target}/5</div>
          </div>
          <div className="plain-hint" style={{ fontSize: 12.5, marginTop: 4 }}>{plainLevel(d.current)}</div>
          <div className="hint" style={{ marginTop: 6 }}>置信度 {d.confidence.toFixed(2)}（{plainConfidence(d.confidence)}）· 最后评估 2026-11-15</div>
          <div className="hint" style={{ marginTop: 4, color: 'var(--text-4)' }}>评估详情：scorer rule-v1 · 证据 {d.evidence} 条</div>
          <div className="domain-track" style={{ height: 9, marginTop: 14 }}>
            <div className="domain-track-fill" style={{ width: `${(d.current / 5) * 100}%`, background: 'var(--accent-grad)' }} />
          </div>
          <div className="domain-card-foot" style={{ marginTop: 8 }}>
            <span>目标 {d.target} / 最高 5</span>
            <span>{d.next}</span>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">维度分解</span>
            <span className="card-sub">来自诊断与项目证据</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {dimScores.map((dim) => (
              <div key={dim.name} className="dim-row">
                <span className="dim-name">{dim.name}</span>
                <div className="dim-track">
                  <div className="dim-fill" style={{ width: `${(dim.score / 5) * 100}%` }} />
                </div>
                <span className="dim-val">{dim.score.toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">证据列表</span>
          <span className="card-sub">{list.length} 条</span>
        </div>
        {list.map((e) => (
          <div className="list-row" key={e.id}>
            <div className="list-ic">{e.type === '诊断' ? '🎯' : e.type === '面经' ? '📄' : e.type === '项目' ? '💼' : e.type === '实践' ? '🛠️' : '🔁'}</div>
            <div className="list-main">
              <div className="list-title">{e.summary}</div>
              <div className="list-sub">{e.id} · {e.date} {e.grade ? `· ${e.grade}` : ''}</div>
            </div>
            <span className={`chip ${e.type === '诊断' ? 'chip-purple' : e.type === '面经' ? 'chip-blue' : e.type === '项目' ? 'chip-green' : 'chip-amber'}`}>{e.type}</span>
          </div>
        ))}
        <div style={{ marginTop: 14 }}>
          <button className="btn btn-outline btn-sm" onClick={() => navigate('/learning')}>从证据生成学习任务 →</button>
        </div>
      </div>
    </div>
  );
}
