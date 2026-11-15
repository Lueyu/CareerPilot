import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from './AppState';
import { Modal } from './ui';
import { NOTIFICATIONS, QUICK_ACTIONS, WORKSPACES } from '../data/demo';

// —— ⌘K 全局搜索 ——
export function CommandPalette() {
  const { cmdOpen, setCmdOpen } = useAppState();
  const [q, setQ] = useState('');
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

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
      { g: '技能', items: [
        { ic: '🧠', name: 'Agent Evaluation', meta: '2.7/5 · 低于目标', to: '/profile/eval' },
        { ic: '🔧', name: '工具调用 / MCP', meta: '3.5/5 · 低于目标', to: '/profile/tools' },
        { ic: '🧩', name: 'RAG / 上下文工程', meta: '3.6/5 · 达到目标', to: '/profile/rag' },
      ]},
      { g: '证据', items: [
        { ic: '🧾', name: 'EV-041 工具重试策略 3/5', meta: '诊断 · 11-15', to: '/profile/eval' },
        { ic: '🧾', name: 'EV-036 对抗评估循环', meta: '项目 · VERIFIED', to: '/assets/project/squabble' },
      ]},
      { g: '任务', items: [
        { ic: '📌', name: '让工具安全地重试', meta: '实践 · 40 分钟', to: '/learning/task/t2' },
        { ic: '📌', name: 'RAG 检索评估指标速测', meta: '复测 · 10 分钟', to: '/learning/task/t3' },
      ]},
      { g: '面经', items: [
        { ic: '📄', name: '字节跳动 · Agent 研发工程师', meta: '待审核 · IM-103', to: '/bank' },
      ]},
    ];
    const kw = q.trim().toLowerCase();
    if (!kw) return all;
    return all
      .map((g) => ({ ...g, items: g.items.filter((it) => (it.name + it.meta).toLowerCase().includes(kw)) }))
      .filter((g) => g.items.length > 0);
  }, [q]);

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
        <div className="cmdk-groups">
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
          {NOTIFICATIONS.map((n) => (
            <div className="pop-item" key={n.title} onClick={() => go('/observatory')}>
              <span className="pop-item-ic">{n.ic}</span>
              <div className="pop-item-main">
                <div className="pop-item-title">{n.title}</div>
                <div className="pop-item-sub">{n.sub} · {n.time}</div>
              </div>
            </div>
          ))}
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
export function WorkspaceSwitcher() {
  const { wsOpen, setWsOpen, toast } = useAppState();
  if (!wsOpen) return null;
  return (
    <div className="modal-mask" onClick={() => setWsOpen(false)}>
      <div className="modal modal-sm" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="modal-title">切换工作空间</div>
          <button className="modal-x" onClick={() => setWsOpen(false)}>✕</button>
        </div>
        <div className="modal-body" style={{ paddingTop: 10 }}>
          {WORKSPACES.map((w) => (
            <div
              className="pop-item"
              key={w.name}
              onClick={() => toast('✅', `已切换到「${w.name}」`)}
              style={{ border: w.active ? '1px solid var(--primary-200)' : '1px solid transparent', background: w.active ? 'var(--primary-50)' : 'transparent', marginBottom: 6 }}
            >
              <span className="ws-avatar" style={{ background: 'var(--primary-50)' }}>🧭</span>
              <div className="pop-item-main">
                <div className="pop-item-title">{w.name}</div>
                <div className="pop-item-sub">{w.kind}</div>
              </div>
              {w.active && <span className="chip chip-purple">当前</span>}
            </div>
          ))}
          <div className="pop-item" onClick={() => toast('➕', '新建工作空间（Demo 未接入后端）')} style={{ color: 'var(--primary)' }}>
            <span className="pop-item-ic">＋</span>
            <div className="pop-item-main">
              <div className="pop-item-title">新建工作空间</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
