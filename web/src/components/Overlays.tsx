import { useT } from '../i18n';
import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from './AppState';
import { Modal } from './ui';
import { api, useApi, getWorkspace, selectWorkspace, ProfileData, LearningData } from '../api/client';
const QUICK_ACTIONS = [
  { ic: '📥', label: 'shell.importInterviewNotes', desc: 'shell.importInterviewNotesHint', to: '/bank?import=1' },
  { ic: '🎯', label: 'shell.startDiagnosis', desc: 'shell.startDiagnosisHint', to: '/exam/diagnosis' },
  { ic: '📚', label: 'shell.viewTasks', desc: 'shell.viewTasksHint', to: '/learning' },
];

const TASK_STATUS_LABELS = new Map([
  ['PENDING', 'shell.taskPending'],
  ['IN_PROGRESS', 'shell.taskInProgress'],
  ['DONE', 'shell.taskDone'],
]);

// —— ⌘K 全局搜索 ——
export function CommandPalette() {
  const t = useT();
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
      {
        id: 'skills',
        label: t('shell.skills'),
        items: (profile.data?.domains ?? []).map(domain => {
          const meta = domain.level == null ? t('shell.unknown') : `${domain.level}/5`;
          return {
            ic: '🧠', name: domain.name, meta,
            rawMeta: meta,
            to: `/profile/${domain.key}`,
          };
        }),
      },
      {
        id: 'tasks',
        label: t('shell.tasks'),
        items: (learning.data?.today ?? []).map(task => {
          const statusKey = TASK_STATUS_LABELS.get(task.status);
          const meta = statusKey ? t(statusKey) : task.status;
          return { ic: '📌', name: task.title, meta, rawMeta: task.status, to: `/learning/task/${task.id}` };
        }),
      },
    ];
    const kw = q.trim().toLowerCase();
    return all
      .map(group => ({
        ...group,
        items: kw ? group.items.filter(item =>
          [item.rawMeta, item.meta].some(meta => (item.name + meta).toLowerCase().includes(kw)),
        ) : group.items,
      }))
      .filter((g) => g.items.length > 0);
  }, [q, profile.data, learning.data, t]);

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
      <div className="modal cmdk" role="dialog" aria-label={t('shell.search')} aria-modal="true" onClick={(e) => e.stopPropagation()}>
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
            aria-label={t('shell.searchPrompt')}
            aria-autocomplete="list"
            aria-controls={listId}
            aria-expanded="true"
            aria-activedescendant={activeId}
            placeholder={t('shell.searchPlaceholder')}
          />
          <span className="kbd">ESC</span>
        </div>
        <div className="cmdk-groups">
          {(profile.loading || learning.loading) && <p role="status" className="hint">{t('shell.searchLoading')}</p>}
          {(profile.error || learning.error) && <p role="alert">{t('shell.searchError', { error: profile.error || learning.error || '' })}</p>}
          {!profile.loading && !learning.loading && !items.length && (
            <div className="empty" role="status" style={{ padding: 20 }}><b>{t('shell.searchNoResults')}</b></div>
          )}
          <div role="listbox" id={listId} aria-label={t('shell.searchResults')}>
            {groups.map((g) => (
              <div role="group" aria-label={g.label} key={g.id}>
                <div className="cmdk-group-title" aria-hidden="true">{g.label}</div>
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
          <span>{t('shell.searchSelect')}</span>
          <span>{t('shell.searchOpen')}</span>
          <span>{t('shell.searchClose')}</span>
        </div>
      </div>
    </div>
  );
}

// —— 通知中心 ——
export function NotificationsPanel() {
  const t = useT();
  const { notifOpen, setNotifOpen } = useAppState();
  if (!notifOpen) return null;
  return (
    <div className="modal-mask" onClick={() => setNotifOpen(false)}>
      <div className="pop-panel" style={{ right: 'auto', left: '50%', transform: 'translateX(-50%)', top: '64px', position: 'absolute' }} onClick={(e) => e.stopPropagation()}>
        <div className="pop-head">
          <span className="pop-title">{t('shell.notifications')}</span>
          <span className="kbd" style={{ cursor: 'pointer' }} onClick={() => setNotifOpen(false)}>{t('shell.closeWithIcon')}</span>
        </div>
        <div className="pop-body">
          <p className="hint">{t('shell.noNotifications')}</p>
        </div>
      </div>
    </div>
  );
}

// —— 快捷操作 ——
export function QuickActionsPanel() {
  const t = useT();
  const { quickOpen, setQuickOpen } = useAppState();
  const navigate = useNavigate();
  const close = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    if (!quickOpen) return;
    const opener = document.activeElement;
    close.current?.focus();
    return () => { if (opener instanceof HTMLElement) opener.focus(); };
  }, [quickOpen]);
  if (!quickOpen) return null;
  return (
    <div className="modal-mask" onClick={() => setQuickOpen(false)}>
      <div className="pop-panel quick-actions-panel" role="dialog" aria-label={t('shell.quickActions')}
        onClick={e => e.stopPropagation()} onKeyDown={e => {
          if (e.key === 'Escape') setQuickOpen(false);
        }}>
        <div className="pop-head">
          <span className="pop-title">{t('shell.quickActions')}</span>
          <button className="kbd" type="button" ref={close} onClick={() => setQuickOpen(false)}>{t('shell.closeWithIcon')}</button>
        </div>
        <div className="pop-body">
          {QUICK_ACTIONS.map((a) => (
            <button
              className="pop-item"
              type="button"
              key={a.to}
              onClick={() => {
                setQuickOpen(false);
                navigate(a.to);
              }}
            >
              <span className="pop-item-ic" aria-hidden="true">{a.ic}</span>
              <span className="pop-item-main">
                <span className="pop-item-title">{t(a.label)}</span>
                <span className="pop-item-sub">{t(a.desc)}</span>
              </span>
              <span className="pop-item-arrow" aria-hidden="true">→</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// —— 工作空间切换 ——
type Workspace = { id: number; name: string; slug: string; is_demo: boolean };
export function WorkspaceSwitcher() {
  const t = useT();
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
  return <Modal title={t('shell.switchWorkspace')} onClose={() => setWsOpen(false)} size="sm">
    {loading && <p>{t('shell.loadingWorkspaces')}</p>}
    {(error || createErr) && <p role="alert">{error || createErr}</p>}
    {(data?.workspaces ?? []).map(w => <button className="pop-item" key={w.id} onClick={() => select(w)} style={{ width: '100%', textAlign: 'left' }}>
      <span className="ws-avatar">🧭</span><span className="pop-item-main"><span className="pop-item-title">{w.name}</span><span className="pop-item-sub">{t('shell.workspaceWithSlug', { label: t(w.is_demo ? 'shell.demoSpace' : 'shell.personalSpace'), slug: w.slug })}</span></span>
      {w.slug === getWorkspace() && <span className="chip chip-purple">{t('shell.activeWorkspace')}</span>}
    </button>)}
    <button className="btn btn-outline" disabled={busy} onClick={personal}>{busy ? t('shell.openingPersonalWorkspace') : t('shell.enterPersonalWorkspace')}</button>
  </Modal>;
}
