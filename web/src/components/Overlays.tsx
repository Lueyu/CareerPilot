import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from './AppState';
import { Modal } from './ui';
import { api, useApi, getWorkspace, selectWorkspace, ProfileData, LearningData } from '../api/client';
const QUICK_ACTIONS = [
  { ic: '📥', label: '导入面经', desc: '解析并整理面试题', to: '/bank?import=1' },
  { ic: '🎯', label: '开始独立诊断', desc: '提交真实作答', to: '/exam/diagnosis' },
  { ic: '📚', label: '查看学习任务', desc: '补齐具体失分项', to: '/learning' },
];

// —— ⌘K 全局搜索 ——
export function CommandPalette() {
  const { cmdOpen, setCmdOpen } = useAppState();
  const [q, setQ] = useState('');
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const profile = useApi<ProfileData>(cmdOpen ? '/profile' : null);
  const learning = useApi<LearningData>(cmdOpen ? '/learning' : null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setCmdOpen(!cmdOpen);
      }
      if (e.key === 'Escape') setCmdOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [cmdOpen, setCmdOpen]);

  useEffect(() => {
    if (cmdOpen) setTimeout(() => inputRef.current?.focus(), 30);
  }, [cmdOpen]);

  const groups = useMemo(() => {
    const all = [
      { g: '技能', items: (profile.data?.domains ?? []).map(d => ({ ic: '🧠', name: d.name, meta: d.level == null ? '未知' : `${d.level}/5`, to: `/profile/${d.key}` })) },
      { g: '任务', items: (learning.data?.today ?? []).map(t => ({ ic: '📌', name: t.title, meta: t.status, to: `/learning/task/${t.id}` })) },
    ];
    const kw = q.trim().toLowerCase();
    if (!kw) return all;
    return all
      .map((g) => ({ ...g, items: g.items.filter((it) => (it.name + it.meta).toLowerCase().includes(kw)) }))
      .filter((g) => g.items.length > 0);
  }, [q, profile.data, learning.data]);

  if (!cmdOpen) return null;
  return (
    <div className="modal-mask" onClick={() => setCmdOpen(false)}>
      <div className="modal cmdk" onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-input">
          <span>🔍</span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索技能、证据、任务…"
          />
          <span className="kbd">ESC</span>
        </div>
        <div className="cmdk-groups">{(profile.loading || learning.loading) && <p className="hint">正在读取当前空间…</p>}{(profile.error || learning.error) && <p role="alert">搜索读取失败：{profile.error || learning.error}</p>}
          {groups.length === 0 && <div className="empty" style={{ padding: 20 }}><b>无匹配结果</b></div>}
          {groups.map((g) => (
            <div key={g.g}>
              <div className="cmdk-group-title">{g.g}</div>
              {g.items.map((it) => (
                <div
                  className="cmdk-item"
                  key={it.name}
                  onClick={() => {
                    setCmdOpen(false);
                    navigate(it.to);
                  }}
                >
                  <span className="cmdk-item-ic">{it.ic}</span>
                  <span className="cmdk-item-name">{it.name}</span>
                  <span className="cmdk-item-meta">{it.meta}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="cmdk-foot">
          <span>↑↓ 选择</span>
          <span>↵ 打开</span>
          <span>ESC 关闭</span>
        </div>
      </div>
    </div>
  );
}

// —— 通知中心 ——
export function NotificationsPanel() {
  const { notifOpen, setNotifOpen } = useAppState();
  const navigate = useNavigate();
  if (!notifOpen) return null;
  const go = (to: string) => {
    setNotifOpen(false);
    navigate(to);
  };
  return (
    <div className="modal-mask" onClick={() => setNotifOpen(false)}>
      <div className="pop-panel" style={{ right: 'auto', left: '50%', transform: 'translateX(-50%)', top: '64px', position: 'absolute' }} onClick={(e) => e.stopPropagation()}>
        <div className="pop-head">
          <span className="pop-title">通知中心</span>
          <span className="kbd" style={{ cursor: 'pointer' }} onClick={() => setNotifOpen(false)}>关闭 ✕</span>
        </div>
        <div className="pop-body">
          <p className="hint">暂无可读取的通知。请在考试记录和学习计划中查看实际进展。</p>
        </div>
      </div>
    </div>
  );
}

// —— 快捷操作 ——
export function QuickActionsPanel() {
  const { quickOpen, setQuickOpen } = useAppState();
  const navigate = useNavigate();
  if (!quickOpen) return null;
  return (
    <div className="modal-mask" onClick={() => setQuickOpen(false)}>
      <div className="pop-panel" style={{ right: 'auto', left: '50%', transform: 'translateX(-50%)', top: '64px', position: 'absolute', width: 420 }} onClick={(e) => e.stopPropagation()}>
        <div className="pop-head">
          <span className="pop-title">快捷操作</span>
          <span className="kbd" style={{ cursor: 'pointer' }} onClick={() => setQuickOpen(false)}>关闭 ✕</span>
        </div>
        <div className="pop-body">
          {QUICK_ACTIONS.map((a) => (
            <div
              className="pop-item"
              key={a.label}
              onClick={() => {
                setQuickOpen(false);
                navigate(a.to);
              }}
            >
              <span className="pop-item-ic">{a.ic}</span>
              <div className="pop-item-main">
                <div className="pop-item-title">{a.label}</div>
                <div className="pop-item-sub">{a.desc}</div>
              </div>
              <span className="pop-item-arrow">→</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// —— 工作空间切换 ——
type Workspace = { id: number; name: string; slug: string; is_demo: boolean };
export function WorkspaceSwitcher() {
  const { wsOpen, setWsOpen } = useAppState();
  const { data, loading, error } = useApi<{ workspaces: Workspace[] }>(wsOpen ? '/settings/workspaces' : null);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const select = (workspace: Workspace) => {
    selectWorkspace(workspace.slug);
    window.location.assign('/');
  };
  const personal = async () => {
    setBusy(true); setCreateErr(null);
    try { const result = await api.post<{ workspace: Workspace }>('/settings/workspaces/personal'); select(result.workspace); }
    catch (e: any) { setCreateErr(String(e.message || e)); setBusy(false); }
  };
  if (!wsOpen) return null;
  return <Modal title="切换工作空间" onClose={() => setWsOpen(false)} size="sm">
    {loading && <p>正在读取真实工作空间…</p>}
    {(error || createErr) && <p role="alert">{error || createErr}</p>}
    {(data?.workspaces ?? []).map(w => <button className="pop-item" key={w.id} onClick={() => select(w)} style={{ width: '100%', textAlign: 'left' }}>
      <span className="ws-avatar">🧭</span><span className="pop-item-main"><span className="pop-item-title">{w.name}</span><span className="pop-item-sub">{w.is_demo ? '演示空间' : '真实个人空间'} · {w.slug}</span></span>
      {w.slug === getWorkspace() && <span className="chip chip-purple">当前</span>}
    </button>)}
    <button className="btn btn-outline" disabled={busy} onClick={personal}>{busy ? '准备个人空间…' : '进入或创建真实个人空间'}</button>
  </Modal>;
}
