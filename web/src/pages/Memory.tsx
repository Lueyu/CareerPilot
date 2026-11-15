import React, { useEffect, useState } from 'react';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api, MemoryData } from '../api/client';
import { CANDIDATE_MEMORIES, MEMORIES, MemoryItem } from '../data/demo';

const TYPE_CLS: Record<string, string> = {
  CareerGoal: 'chip-purple',
  Preference: 'chip-blue',
  KnowledgeGapSignal: 'chip-amber',
  ProjectDecision: 'chip-green',
  Feedback: 'chip-slate',
};
const TYPE_IC: Record<string, string> = {
  CareerGoal: '🎯',
  Preference: '❤️',
  KnowledgeGapSignal: '🔎',
  ProjectDecision: '🧭',
  Feedback: '💬',
};

interface ApiMem {
  id: number;
  mtype: string;
  content: string;
  source: string;
  source_ref?: string | null;
  extract_confidence?: number | null;
  status: string;
  created_at: string;
}

export default function Memory() {
  const { toast } = useAppState();
  const [items, setItems] = useState<ApiMem[]>([]);
  const [apiErr, setApiErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pasteText, setPasteText] = useState('');
  const [extracting, setExtracting] = useState(false);
  const [cands, setCands] = useState<{ id: number; mtype: string; content: string; confidence: number }[]>([]);
  const [demoCands, setDemoCands] = useState(CANDIDATE_MEMORIES);
  const [correcting, setCorrecting] = useState<ApiMem | null>(null);
  const [correctText, setCorrectText] = useState('');

  const reload = () => {
    api.get<MemoryData>('/memory')
      .then((d) => {
        setItems(d.items);
        setCands(d.items
          .filter((m) => m.status === 'CANDIDATE')
          .map((m) => ({ id: m.id, mtype: m.mtype, content: m.content, confidence: m.extract_confidence ?? 0.5 })));
        setApiErr(null);
      })
      .catch((e) => { setApiErr(String(e.message || e)); })
      .finally(() => setLoading(false));
  };
  useEffect(reload, []);
  const timeline = items.filter((m) => m.status !== 'CANDIDATE');

  const remove = async (m: ApiMem) => {
    setItems((l) => l.filter((x) => x.id !== m.id));
    toast('🗑️', `已删除记忆 #${m.id}`);
    try { await api.del(`/memory/${m.id}`); } catch { /* 本地已移除 */ }
    reload();
  };
  const confirm = async (m: ApiMem) => {
    try {
      await api.post(`/memory/${m.id}/confirm`);
      toast('✓', `已确认记忆 #${m.id}`);
    } catch { toast('✓', `已确认记忆 #${m.id}(本地)`); }
    reload();
  };
  const submitCorrect = async () => {
    if (!correcting || !correctText.trim()) return;
    try {
      await api.post(`/memory/${correcting.id}/correct`, { content: correctText.trim() });
      toast('✏️', '已纠正为新的记忆（旧记忆保留为 SUPERSEDED）');
    } catch { toast('✏️', '纠正已记录（本地）'); }
    setCorrecting(null);
    setCorrectText('');
    reload();
  };
  const extract = async () => {
    if (!pasteText.trim()) { toast('⚠️', '先粘贴一段对话文本'); return; }
    setExtracting(true);
    try {
      const r = await api.post<{ candidates: { id: number; mtype: string; content: string; extract_confidence: number; status?: string }[] }>(
        '/memory/import-dialog', { text: pasteText }
      );
      reload();
      toast('🔎', `已提取 ${r.candidates.length} 条候选记忆`);
    } catch (e: any) {
      toast('⚠️', `提取失败: ${e.message}`);
    } finally {
      setExtracting(false);
    }
  };
  const confirmCand = async (i: number) => {
    const c = cands[i];
    setCands((l) => l.filter((_, k) => k !== i));
    try {
      await api.post(`/memory/${c.id}/confirm`);
      toast('✅', '已写入记忆中心');
    } catch { toast('⚠️', '确认失败, 请稍后重试'); }
    reload();
  };
  const dismissCand = async (i: number) => {
    const c = cands[i];
    setCands((l) => l.filter((_, k) => k !== i));
    try {
      await api.del(`/memory/${c.id}`);
      toast('👋', '已忽略该候选记忆');
    } catch { /* 本地已移除 */ }
  };

  const dateShort = (s?: string) => (s || '').replace('T', ' ').slice(5, 16);

  return (
    <div>
      <PageHead
        title="记忆中心"
        sub="从对话、面经、诊断中沉淀的长期记忆 · 可确认、可纠正、可删除"
      >
        <button className="btn btn-primary" onClick={() => toast('📥', '在右侧粘贴对话文本即可提取候选记忆')}>导入对话</button>
      </PageHead>
      <DemoBanner />

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载…</div>}
      {apiErr && (
        <div className="demo-banner" style={{ marginBottom: 12, background: '#fef2f2', color: '#b91c1c' }}>
          后端不可用（{apiErr}），以下为静态演示记忆
        </div>
      )}

      <div className="grid grid-1-2">
        {/* 左侧：记忆时间线 */}
        <div className="card">
          <div className="card-head">
            <span className="card-title">记忆时间线</span>
            <span className="card-sub">{timeline.length} 条 · 仅 ACTIVE 参与推理</span>
          </div>
          {apiErr
            ? MEMORIES.map((m) => (
                <div className="mem-item" key={m.id}>
                  <div className="mem-ic">{TYPE_IC[m.type]}</div>
                  <div className="mem-body">
                    <div className="q-meta">
                      <span className={`chip ${TYPE_CLS[m.type]}`}>{m.type}</span>
                      <span className={`chip ${m.status === 'ACTIVE' ? 'chip-green' : 'chip-slate'}`}>{m.status}</span>
                      <span style={{ fontSize: 11.5 }}>{m.date} · {m.source}</span>
                    </div>
                    <div className="mem-text" style={{ marginTop: 5 }}>{m.text}</div>
                    {m.status === 'ACTIVE' && (
                      <div className="mem-actions">
                        <button className="btn btn-outline btn-sm" onClick={() => toast('✓', `已确认记忆 ${m.id}`)}>✓ 确认</button>
                        <button className="btn btn-outline btn-sm" onClick={() => toast('✏️', '纠正记忆（后端不可用）')}>✏️ 纠正</button>
                        <button className="btn btn-danger btn-sm" onClick={() => toast('🗑️', `已删除记忆 ${m.id}`)}>删除</button>
                      </div>
                    )}
                  </div>
                </div>
              ))
            : timeline.map((m) => (
                <div className="mem-item" key={m.id}>
                  <div className="mem-ic">{TYPE_IC[m.mtype] || '📌'}</div>
                  <div className="mem-body">
                    <div className="q-meta">
                      <span className={`chip ${TYPE_CLS[m.mtype] || 'chip-slate'}`}>{m.mtype}</span>
                      <span className={`chip ${m.status === 'ACTIVE' ? 'chip-green' : m.status === 'CANDIDATE' ? 'chip-amber' : 'chip-slate'}`}>{m.status}</span>
                      <span style={{ fontSize: 11.5 }}>{dateShort(m.created_at)} · {m.source === 'DIALOG_IMPORT' ? '对话导入' : m.source === 'SYSTEM' ? '系统' : '用户'}</span>
                    </div>
                    <div className="mem-text" style={{ marginTop: 5 }}>{m.content}</div>
                    {m.status !== 'DISMISSED' && (
                      <div className="mem-actions">
                        {m.status === 'CANDIDATE' ? (
                          <button className="btn btn-primary btn-sm" onClick={() => confirm(m)}>✓ 确认</button>
                        ) : m.status === 'ACTIVE' ? (
                          <button className="btn btn-outline btn-sm" onClick={() => confirm(m)}>✓ 确认</button>
                        ) : null}
                        <button className="btn btn-outline btn-sm" onClick={() => { setCorrecting(m); setCorrectText(m.content); }}>✏️ 纠正</button>
                        <button className="btn btn-danger btn-sm" onClick={() => remove(m)}>删除</button>
                      </div>
                    )}
                  </div>
                </div>
              ))}
          {!apiErr && timeline.length === 0 && !loading && (
            <div className="empty" style={{ padding: 20 }}><b>暂无记忆</b><p>从右侧导入对话开始</p></div>
          )}
        </div>

        {/* 右侧：导入对话 + 候选记忆 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div className="card">
            <div className="card-head">
              <span className="card-title">导入对话</span>
              <span className="card-sub">粘贴对话记录，提取候选记忆</span>
            </div>
            <textarea
              className="textarea"
              style={{ minHeight: 90 }}
              placeholder={'粘贴对话记录，例如：\n我的目标是三个月内拿到 AI Agent 工程师 offer…\n我偏好用费曼学习法复盘…'}
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
            />
            <button className="btn btn-outline" style={{ marginTop: 10 }} onClick={extract} disabled={extracting}>
              {extracting ? '正在提取…' : '提取候选记忆'}
            </button>
            <div className="hint" style={{ marginTop: 8 }}>
              提取走后端规则引擎（可配 LLM），候选记忆确认后写入时间线。
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <span className="card-title">候选记忆</span>
              <span className="card-sub">{cands.length} 条待确认</span>
            </div>
            {cands.map((c, i) => (
              <div className="mem-item" key={c.id}>
                <div className="mem-body">
                  <div className="q-meta">
                    <span className={`chip ${TYPE_CLS[c.mtype] || 'chip-slate'}`}>{c.mtype}</span>
                    <span style={{ fontSize: 11.5 }}>置信度 {Math.round((c.confidence ?? 0) * 100)}%</span>
                  </div>
                  <div className="mem-text" style={{ marginTop: 5 }}>{c.content}</div>
                  <div className="mem-actions">
                    <button className="btn btn-primary btn-sm" onClick={() => confirmCand(i)}>确认</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => dismissCand(i)}>忽略</button>
                  </div>
                </div>
              </div>
            ))}
            {apiErr && demoCands.map((c, i) => (
              <div className="mem-item" key={i}>
                <div className="mem-body">
                  <div className="mem-text">{c.text}</div>
                  <div className="mem-src">{c.source}</div>
                  <div className="mem-actions">
                    <button className="btn btn-primary btn-sm" onClick={() => setDemoCands((l) => l.filter((_, k) => k !== i))}>确认</button>
                    <button className="btn btn-ghost btn-sm" onClick={() => setDemoCands((l) => l.filter((_, k) => k !== i))}>忽略</button>
                  </div>
                </div>
              </div>
            ))}
            {cands.length === 0 && demoCands.length === 0 && (
              <div className="empty" style={{ padding: 20 }}><b>没有待确认的候选</b></div>
            )}
          </div>
        </div>
      </div>

      {correcting && (
        <Modal title={`纠正记忆 #${correcting.id}`} onClose={() => setCorrecting(null)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setCorrecting(null)}>取消</button>
              <button className="btn btn-primary" onClick={submitCorrect}>保存纠正</button>
            </>
          }>
          <p className="hint" style={{ marginBottom: 10 }}>纠正会生成一条新记忆，旧记忆标记为 SUPERSEDED（保留审计痕迹）。</p>
          <div className="field">
            <label>原记忆</label>
            <div className="hint" style={{ padding: '8px 10px', background: 'var(--bg)', borderRadius: 8 }}>{correcting.content}</div>
          </div>
          <div className="field" style={{ marginTop: 12 }}>
            <label>纠正为</label>
            <textarea className="textarea" style={{ minHeight: 80 }} value={correctText} onChange={(e) => setCorrectText(e.target.value)} />
          </div>
        </Modal>
      )}
    </div>
  );
}
