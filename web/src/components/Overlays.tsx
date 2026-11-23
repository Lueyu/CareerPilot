import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
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
  const [selectedTo, setSelectedTo] = useState<string | null>(null);
  const navigate = useNavigate();
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const activeOptionRef = useRef<HTMLDivElement>(null);
  const profile = useApi<ProfileData>(cmdOpen ? '/profile' : null);
  const learning = useApi<LearningData>(cmdOpen ? '/learning' : null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (!e.repeat) setCmdOpen(!cmdOpen);
      } else if (cmdOpen && e.key === 'Escape') {
        e.preventDefault();
        setCmdOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [cmdOpen, setCmdOpen]);

  useLayoutEffect(() => {
    if (cmdOpen) {
      const focused = document.activeElement;
      previousFocus.current = focused instanceof HTMLElement ? focused : null;
      inputRef.current?.focus();
    } else {
      if (previousFocus.current?.isConnected) previousFocus.current.focus();
      previousFocus.current = null;
      setSelectedTo(null);
    }
  }, [cmdOpen]);

  const groups = useMemo(() => {
    const all = [
      { g: '技能', items: (profile.data?.domains ?? []).map(d => ({ ic: '🧠', name: d.name, meta: d.level == null ? '未知' : `${d.level}/5`, to: `/profile/${d.key}` })) },
      { g: '任务', items: (learning.data?.today ?? []).map(t => ({ ic: '📌', name: t.title, meta: t.status, to: `/learning/task/${t.id}` })) },
    ];
    const kw = q.trim().toLowerCase();
    return all
      .map((g) => ({ ...g, items: kw ? g.items.filter((it) => (it.name + it.meta).toLowerCase().includes(kw)) : g.items }))
      .filter((g) => g.items.length > 0);
  }, [q, profile.data, learning.data]);

  const items = groups.flatMap(g => g.items);
  const activeIndex = Math.max(0, items.findIndex(item => item.to === selectedTo));
  const activeItem = items[activeIndex];
  const optionId = (to: string) => `${listId}-${to}`;
  const activeId = activeItem ? optionId(activeItem.to) : undefined;

  useLayoutEffect(() => {
    if (cmdOpen) activeOptionRef.current?.scrollIntoView({ block: 'nearest' });
  }, [activeId, cmdOpen]);

  const openResult = (to: string) => {
    setCmdOpen(false);
    navigate(to);
  };

  const onInputKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === 'Tab') {
      e.preventDefault();
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!items.length) return;
      const step = e.key === 'ArrowDown' ? 1 : -1;
      const nextIndex = (activeIndex + step + items.length) % items.length;
      setSelectedTo(items[nextIndex].to);
    } else if (e.key === 'Enter' && activeItem) {
      e.preventDefault();
      openResult(activeItem.to);
    }
  };

  if (!cmdOpen) return null;
  return (
    <div className="modal-mask" onClick={() => setCmdOpen(false)}>
      <div className="modal cmdk" role="dialog" aria-label="全局搜索" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-input">
          <span>🔍</span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setSelectedTo(null);
            }}
            onKeyDown={onInputKey}
            role="combobox"
            aria-label="搜索技能、证据、任务"
            aria-autocomplete="list"
            aria-controls={listId}
            aria-expanded="true"
            aria-activedescendant={activeId}
            placeholder="搜索技能、证据、任务…"
          />
          <span className="kbd">ESC</span>
        </div>
        <div className="cmdk-groups">
          {(profile.loading || learning.loading) && <p role="status" className="hint">正在读取当前空间…</p>}
          {(profile.error || learning.error) && <p role="alert">搜索读取失败：{profile.error || learning.error}</p>}
          {!profile.loading && !learning.loading && !items.length && (
            <div className="empty" role="status" style={{ padding: 20 }}><b>无匹配结果</b></div>
          )}
          <div role="listbox" id={listId} aria-label="搜索结果">
            {groups.map((g) => (
              <div role="group" aria-label={g.g} key={g.g}>
                <div className="cmdk-group-title" aria-hidden="true">{g.g}</div>
                {g.items.map((it) => {
                  const selected = it.to === activeItem?.to;
                  return (
                    <div
                      className={selected ? 'cmdk-item sel' : 'cmdk-item'}
                      key={it.to}
                      id={optionId(it.to)}
                      ref={selected ? activeOptionRef : null}
                      role="option"
                      aria-selected={selected}
                      onClick={() => openResult(it.to)}
                    >
                      <span className="cmdk-item-ic" aria-hidden="true">{it.ic}</span>
                      <span className="cmdk-item-name">{it.name}</span>
                      <span className="cmdk-item-meta">{it.meta}</span>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
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
  if (!notifOpen) return null;
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
