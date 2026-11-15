import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, PageHead } from '../components/ui';
import { PROJECTS } from '../data/demo';

export default function Assets() {
  const navigate = useNavigate();
  const { toast } = useAppState();

  return (
    <div>
      <PageHead
        title="项目与职业资产"
        sub="项目证据自动映射为职业展示 · 草稿 → 风险分级 → 发布，全流程可审计"
      >
        <button className="btn btn-outline" onClick={() => toast('🔍', '仓库导入（Demo 未接入 GitHub）')}>＋ 导入仓库</button>
        <button className="btn btn-primary" onClick={() => navigate('/assets/pipeline')}>⚙ 更新职业资产</button>
      </PageHead>
      <DemoBanner />

      <div className="grid grid-2">
        {PROJECTS.map((p) => (
          <div className="card" key={p.id}>
            <div className="card-head">
              <div>
                <div className="card-title">{p.name}</div>
                <div className="card-sub">{p.sub}</div>
              </div>
              <span className="chip chip-green">已扫描</span>
            </div>
            <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.7 }}>{p.summary}</p>
            <div className="q-meta" style={{ margin: '10px 0' }}>
              <span className="chip chip-slate">🔗 {p.github}</span>
              <span className="chip chip-slate">{p.stack.join(' · ')}</span>
            </div>
            <div className="hint" style={{ marginBottom: 10 }}>最近扫描：{p.scanned} · 能力矩阵</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
              {p.matrix.map((m) => (
                <div className="dim-row" key={m.key}>
                  <span className="dim-name" style={{ width: 160 }}>{m.label}</span>
                  <div className="dim-track">
                    <div className="dim-fill" style={{ width: `${(m.level / 5) * 100}%` }} />
                  </div>
                  <span className="dim-val">{m.level}.0</span>
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn btn-primary" onClick={() => navigate(`/assets/project/${p.id}`)}>项目审查</button>
              <button className="btn btn-outline" onClick={() => toast('🔄', `重新分析 ${p.name}（Demo）`)}>重新分析</button>
            </div>
          </div>
        ))}
      </div>

      <div className="card mt-18">
        <div className="card-head">
          <span className="card-title">职业资产更新流水线</span>
          <button className="btn btn-ghost btn-sm" onClick={() => navigate('/assets/pipeline')}>查看流水线状态 →</button>
        </div>
        <div className="pipeline">
          {['检测项目变更', '提取证据', '映射能力', '对比现有展示', '生成内容草稿', '风险分级', '创建 PR', '发布'].map((s, i) => (
            <React.Fragment key={s}>
              {i > 0 && <span className="pl-arrow">→</span>}
              <span className={`pl-step ${i === 0 ? 'now' : i < 0 ? 'done' : ''}`}>
                {i === 0 ? '⟳' : '✓'} {s}
              </span>
            </React.Fragment>
          ))}
        </div>
        <div className="hint" style={{ marginTop: 10 }}>等待项目变更触发 · 最近运行 11-15 09:58（成功）</div>
      </div>
    </div>
  );
}
