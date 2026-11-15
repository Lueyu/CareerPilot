import React, { useState } from 'react';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { EVOS } from '../data/demo';

const META: Record<string, { label: string; base?: (e: (typeof EVOS)[number]) => string }> = {
  mastery: { label: '掌握度' },
  completion: { label: '任务完成率' },
  retest: { label: '延迟复测完成率' },
  cost: { label: '单位成本' },
};

export default function Evolution() {
  const { toast } = useAppState();
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectId, setRejectId] = useState<string | null>(null);

  const approve = (id: string) => toast('✅', `已批准 ${id}，策略已生效（Demo）`);
  const reject = (id: string) => { setRejectId(id); setRejectOpen(true); };
  const confirmReject = () => {
    if (rejectId) toast('⛔', `已拒绝 ${rejectId}，策略保持基线（Demo）`);
    setRejectOpen(false);
    setRejectId(null);
  };
  const rollback = (id: string) => toast('↩️', `已回滚 ${id}，回到基线策略（Demo）`);

  return (
    <div>
      <PageHead
        title="进化实验室"
        sub="用实验验证每个策略改动 · 只有证据支持的改动才会被保留"
      >
        <button className="btn btn-primary" onClick={() => toast('🧪', '新建实验（Demo）')}>+ 新建实验</button>
      </PageHead>
      <DemoBanner />

      {/* v3.1 M7: 诚实化说明——演示实验 ≠ 真实实验 */}
      <div className="card obs-summary" style={{ marginBottom: 18 }}>
        <div className="card-head">
          <span className="card-title">进化实验室是做什么的？</span>
        </div>
        <div className="obs-summary-line">
          <span className="obs-summary-ic">🧪</span>
          <span>系统每次想改一个策略（比如「考试怎么选题」），都要先小流量试运行，和现有策略对比数据：指标更好、且没有别的指标变差，才会全量启用；否则自动回滚。</span>
        </div>
        <div className="obs-summary-line">
          <span className="obs-summary-ic">ℹ️</span>
          <span>你的工作空间<b>还没有真实实验</b>——实验会在系统积累足够的运行数据后自动发起。下面是两个<b>演示实验</b>，仅用于展示界面与决策流程，不构成真实评估结果。</span>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">策略候选</span>
          <span className="card-sub">演示数据 · 对比基线 vs 候选 · 样本量越大越可信</span>
        </div>
        {EVOS.map((e) => (
          <div className="evo-card" key={e.id} style={{ padding: '18px 0', borderBottom: '1px solid var(--border-2)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <span className="chip chip-purple">{e.id}</span>
                  <b style={{ fontSize: 14.5 }}>{e.name}</b>
                  <span className={`chip ${e.status === '评估中' ? 'chip-amber' : 'chip-slate'}`}>{e.status}</span>
                  <span className="chip demo-tag">演示实验</span>
                </div>
                <p className="hint" style={{ margin: '6px 0 12px' }}>{e.desc}</p>
                <div className="evo-metrics">
                  {Object.entries(e.deltas).map(([k, delta]) => {
                    const v = e.candidate?.[k as keyof typeof e.candidate] ?? null;
                    const base = e.baseline?.[k as keyof typeof e.baseline] ?? null;
                    const pct = v == null ? null : Number(v);
                    const good = delta.startsWith('+') && k !== 'cost';
                    const bad = delta.startsWith('+') && k === 'cost';
                    return (
                      <div className="evo-metric" key={k}>
                        <div className="evo-m-label">{META[k]?.label ?? k}</div>
                        <div className="evo-m-value">
                          <span className={`chip ${good ? 'chip-green' : bad ? 'chip-red' : 'chip-slate'}`}>{delta}</span>
                        </div>
                        <div className="evo-bar">
                          <div className="evo-bar-fill" style={{
                            width: `${Math.min(100, (pct ?? 50) * 1.2)}%`,
                            background: good ? 'var(--green)' : bad ? 'var(--red)' : 'var(--border)',
                          }} />
                        </div>
                        <div className="hint" style={{ fontSize: 11 }}>基线 {base ?? '—'} → 候选 {v ?? '—'}</div>
                      </div>
                    );
                  })}
                  {Object.keys(e.deltas).length === 0 && (
                    <div className="hint" style={{ padding: '6px 0' }}>该实验已回滚，未产生可对比指标</div>
                  )}
                </div>
                <div className="hint" style={{ fontSize: 12.5, marginTop: 8 }}>
                  样本量 {e.sample} · 运行 {e.duration} · 回归：{e.verdict.regression}
                </div>
              </div>
            </div>
            <div className="btn-row" style={{ marginTop: 14 }}>
              {e.status === '评估中' && (
                <>
                  <button className="btn btn-primary" onClick={() => approve(e.id)}>✓ 批准</button>
                  <button className="btn btn-outline" onClick={() => reject(e.id)}>拒绝</button>
                </>
              )}
              {e.status === '已回滚' && (
                <button className="btn btn-outline" onClick={() => toast('ℹ️', `${e.id} 已回滚至基线（Demo）`)}>已回滚至基线</button>
              )}
            </div>
            {e.status === '评估中' && e.verdict.recommendation && (
              <div className="hint" style={{ fontSize: 12.5, marginTop: 8, color: 'var(--text-3)' }}>💡 {e.verdict.recommendation}</div>
            )}
          </div>
        ))}
      </div>

      {rejectOpen && (
        <Modal title={`拒绝 ${rejectId}？`} size="sm" onClose={() => setRejectOpen(false)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setRejectOpen(false)}>取消</button>
              <button className="btn btn-danger" onClick={confirmReject}>确认拒绝</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            拒绝后，系统将继续使用<b>基线策略</b>，该候选将不再参与后续运行。你可以稍后在实验历史中重新评估。
          </p>
        </Modal>
      )}
    </div>
  );
}
