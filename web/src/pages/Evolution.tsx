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
  PROPOSED: { cls: 'chip-slate', label: '提议中' },
  TESTING: { cls: 'chip-amber', label: '试用中' },
  EVALUATED: { cls: 'chip-amber', label: '已评估' },
  APPROVED: { cls: 'chip-purple', label: '已批准' },
  ACTIVE: { cls: 'chip-green', label: '生效中' },
  REJECTED: { cls: 'chip-slate', label: '已拒绝' },
  ROLLED_BACK: { cls: 'chip-slate', label: '已回滚' },
};
const VERDICT_META: Record<string, { cls: string; label: string }> = {
  POSITIVE: { cls: 'chip-green', label: '评估正向' },
  NEGATIVE: { cls: 'chip-red', label: '评估负向' },
  INSUFFICIENT_EVIDENCE: { cls: 'chip-amber', label: '样本不足' },
};

const fmtDelta = (m: Metric) => {
  if (m.delta == null) return '—';
  const v = m.delta;
  return (v > 0 ? '+' : '') + (Number.isInteger(v) ? v : Number(v.toFixed(3)));
};
const fmtVal = (v: unknown) => {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? '是' : '否';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(3)));
  return String(v);
};
const fmtPct = (v: number | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`);

export default function Evolution() {
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
      setLoadErr(e?.message || '加载失败');
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const propose = async () => {
    setProposeBusy(true);
    try {
      const d = await api.post<{ proposed: boolean; reason?: string; candidate?: EvolutionCandidate | null }>('/evolution/candidates');
      if (d.proposed && d.candidate) {
        toast('🧪', `已基于真实运行数据生成候选 ${d.candidate.short_code}，请先评估再批准`);
      } else {
        toast('ℹ️', d.reason || '当前数据不足以生成候选，先在考试与学习中积累样本');
      }
      await load();
    } catch (e: any) {
      toast('⚠️', e?.message || '生成失败');
    } finally {
      setProposeBusy(false);
    }
  };

  const act = async (c: EvolutionCandidate, action: 'evaluate' | 'approve' | 'reject' | 'rollback') => {
    setBusyId(c.id);
    try {
      await api.post(`/evolution/candidates/${c.id}/${action}`);
      const msg: Record<string, string> = {
        evaluate: `已评估 ${c.short_code}，对比指标见下方`,
        approve: `已批准 ${c.short_code}，策略已生效`,
        reject: `已拒绝 ${c.short_code}，继续使用基线策略`,
        rollback: `已回滚 ${c.short_code}，回到基线策略`,
      };
      toast(action === 'approve' ? '✅' : action === 'reject' ? '⛔' : action === 'rollback' ? '↩️' : '🧪', msg[action]);
      setConfirmMode(null);
      setConfirmCand(null);
      await load();
    } catch (e: any) {
      toast('⚠️', e?.message || '操作失败');
    } finally {
      setBusyId(null);
    }
  };

  const activeCand = (items || []).find((c) => c.status === 'ACTIVE');
  const hasAny = (items || []).length > 0;

  return (
    <div>
      <PageHead
        title="进化实验室"
        sub="用实验验证每个策略改动 · 只有证据支持的改动才会被保留"
      >
        <button className="btn btn-primary" disabled={proposeBusy} onClick={propose}>
          {proposeBusy ? '分析中…' : '+ 生成策略候选'}
        </button>
      </PageHead>
      <DemoBanner />

      {/* 机制说明 */}
      <div className="card obs-summary" style={{ marginBottom: 18 }}>
        <div className="card-head">
          <span className="card-title">进化实验室是做什么的？</span>
        </div>
        <div className="obs-summary-line">
          <span className="obs-summary-ic">🧪</span>
          <span>策略自动激活已禁用。候选和事件仍可查看与评估，本地核心流程不会通过候选批准改变策略。</span>
        </div>
        <div className="obs-summary-line">
          <span className="obs-summary-ic">ℹ️</span>
          <span>
            {hasAny ? '以下候选全部由真实运行数据生成；样本量不足时会明确标注「样本不足」，不会伪造结论。' : <>还没有真实实验——点右上角「生成策略候选」，系统会基于你的考试得分率、复测完成率等真实信号判断是否值得发起；样本不足（已完成考试 &lt; 3 次）时会诚实拒绝。</>}
          </span>
        </div>
        {activeCand && (
          <div className="obs-summary-line">
            <span className="obs-summary-ic">⚙️</span>
            <span>当前生效策略：<b>{activeCand.short_code} {activeCand.title}</b>（选题时会自动应用，选题理由里能看到它的编号）。</span>
          </div>
        )}
      </div>

      {loadErr && (
        <div className="card" style={{ padding: 16, marginBottom: 16, borderColor: 'var(--red)' }}>
          <span style={{ color: 'var(--red)' }}>后端不可用：{loadErr}</span>
          <button className="btn btn-outline btn-sm" style={{ marginLeft: 12 }} onClick={load}>重试</button>
        </div>
      )}

      {items && !hasAny && !loadErr && (
        <div className="card" style={{ padding: 28, textAlign: 'center' }}>
          <div style={{ fontSize: 30, marginBottom: 8 }}>🧪</div>
          <b>还没有策略候选</b>
          <p className="hint" style={{ marginTop: 6 }}>
            完成几次独立诊断、安排复测并执行学习任务后，系统才有足够样本判断策略改动是否有价值。
          </p>
        </div>
      )}

      {items && hasAny && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">策略候选</span>
            <span className="card-sub">全部来自真实运行数据 · 批准前必须先评估 · 样本量越大越可信</span>
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
                        策略参数：
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
                          const good = typeof m.delta === 'number' && m.delta > 0 && k !== '单位成本';
                          const bad = (typeof m.delta === 'number' && m.delta > 0 && k === '单位成本') || (typeof m.delta === 'number' && m.delta < 0 && k !== '单位成本');
                          const pct = m.candidate == null ? null : Math.min(100, Math.abs(m.candidate) * 30);
                          return (
                            <div className="evo-metric" key={k}>
                              <div className="evo-m-label">{k}</div>
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
                                基线 {fmtVal(m.baseline)} → 候选 {fmtVal(m.candidate)}{m.note ? ` · ${m.note}` : ''}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}

                    {/* 样本信号 */}
                    {Object.keys(sig).length > 0 && (
                      <div className="hint" style={{ fontSize: 12.5, marginTop: 8 }}>
                        样本信号：完成考试 {sig.completed_exams ?? 0} 次 · 平均得分率 {fmtPct(sig.avg_score_ratio)} · 复测完成率 {fmtPct(sig.retest_rate)} · 任务完成率 {fmtPct(sig.task_rate)}
                      </div>
                    )}
                    {c.run && (
                      <div className="hint" style={{ fontSize: 12.5, marginTop: 4 }}>
                        样本量 {c.run.sample_size} · 回归：{c.run.regression ? Object.entries(c.run.regression).map(([k, v]) => `${k} ${v > 0 ? '+' : ''}${v}`).join('、') : '无'}
                        {c.approved_at ? ` · 批准于 ${new Date(c.approved_at).toLocaleString('zh-CN', { hour12: false })}` : ''}
                      </div>
                    )}
                  </div>
                </div>

                <div className="btn-row" style={{ marginTop: 14 }}>
                  {c.status === 'PROPOSED' && (
                    <button className="btn btn-primary" disabled={busy} onClick={() => act(c, 'evaluate')}>
                      {busy ? '评估中…' : '🧪 评估'}
                    </button>
                  )}
                  {c.status === 'EVALUATED' && (
                    <>
                      <button className="btn btn-primary" disabled title="策略激活已关闭">策略激活已禁用</button>
                      <button className="btn btn-outline" disabled={busy} onClick={() => { setConfirmCand(c); setConfirmMode('reject'); }}>拒绝</button>
                    </>
                  )}
                  {c.status === 'ACTIVE' && (
                    <button className="btn btn-outline" disabled={busy} onClick={() => { setConfirmCand(c); setConfirmMode('rollback'); }}>↩️ 回滚到基线</button>
                  )}
                  {terminal && (
                    <span className="hint" style={{ fontSize: 12.5 }}>该候选已终态，历史记录保留。</span>
                  )}
                </div>

                {c.run?.verdict === 'INSUFFICIENT_EVIDENCE' && c.status !== 'ACTIVE' && (
                  <div className="hint" style={{ fontSize: 12.5, marginTop: 8, color: 'var(--text-3)' }}>
                    💡 样本量 {c.run.sample_size} 不足以给出可靠结论——继续积累考试与复测数据后可重新评估。
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {confirmMode && confirmCand && (
        <Modal
          title={confirmMode === 'reject' ? `拒绝 ${confirmCand.short_code}？` : `回滚 ${confirmCand.short_code}？`}
          size="sm"
          onClose={() => { setConfirmMode(null); setConfirmCand(null); }}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => { setConfirmMode(null); setConfirmCand(null); }}>取消</button>
              <button
                className="btn btn-danger"
                disabled={busyId === confirmCand.id}
                onClick={() => act(confirmCand, confirmMode)}
              >
                {confirmMode === 'reject' ? '确认拒绝' : '确认回滚'}
              </button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            {confirmMode === 'reject'
              ? <>拒绝后，系统将继续使用<b>基线策略</b>，该候选将不再参与后续运行。候选及其评估记录会保留在历史中。</>
              : <>回滚后，选题立即恢复为<b>基线策略</b>；该候选标记为已回滚并保留完整运行记录，用于后续分析。</>}
          </p>
        </Modal>
      )}
    </div>
  );
}
