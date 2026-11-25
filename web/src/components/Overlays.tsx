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

// —— 工作空间切换与管理(创建/编辑/删除) ——
type Workspace = { id: number; name: string; slug: string; is_demo: boolean; target_role?: string | null; target_level?: string | null };
export function WorkspaceSwitcher() {
  const t = useT();
  const { wsOpen, setWsOpen } = useAppState();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [newRole, setNewRole] = useState('');
  const [newLevel, setNewLevel] = useState('');
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [editRole, setEditRole] = useState('');
  const [editLevel, setEditLevel] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [confirmSlug, setConfirmSlug] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [personalBusy, setPersonalBusy] = useState(false);

  const load = async () => {
    setLoading(true); setError(null);
    try {
      const result = await api.get<{ workspaces: Workspace[] }>('/settings/workspaces');
      setWorkspaces(result.workspaces ?? []);
    } catch (e: any) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { if (wsOpen) void load(); }, [wsOpen]); // eslint-disable-line react-hooks/exhaustive-deps

  const select = (workspace: Workspace) => {
    selectWorkspace(workspace.slug);
    window.location.assign('/');
  };
  const create = async () => {
    const name = newName.trim();
    if (!name) { setError(t('shell.workspaceNameRequired')); return; }
    setCreating(true); setError(null);
    try {
      const result = await api.post<{ workspace: Workspace }>('/settings/workspaces', {
        name,
        ...(newRole.trim() ? { target_role: newRole.trim() } : {}),
        ...(newLevel.trim() ? { target_level: newLevel.trim() } : {}),
      });
      setNewName(''); setNewRole(''); setNewLevel('');
      select(result.workspace);
    } catch (e: any) {
      setError(t('shell.workspaceCreateFailed', { error: String(e.message || e) }));
      setCreating(false);
    }
  };
  const startEdit = (workspace: Workspace) => {
    setEditingId(workspace.id); setDeletingId(null);
    setEditName(workspace.name); setEditRole(workspace.target_role ?? ''); setEditLevel(workspace.target_level ?? '');
  };
  const saveEdit = async (workspace: Workspace) => {
    const name = editName.trim();
    if (!name) { setError(t('shell.workspaceNameRequired')); return; }
    setSavingEdit(true); setError(null);
    try {
      await api.patch(`/settings/workspaces/${workspace.id}`, {
        name,
        ...(editRole.trim() ? { target_role: editRole.trim() } : {}),
        ...(editLevel.trim() ? { target_level: editLevel.trim() } : {}),
      });
      setEditingId(null);
      await load();
    } catch (e: any) {
      setError(t('shell.saveFailed', { error: String(e.message || e) }));
    } finally {
      setSavingEdit(false);
    }
  };
  const startDelete = (workspace: Workspace) => {
    setDeletingId(workspace.id); setEditingId(null); setConfirmSlug('');
  };
  const remove = async (workspace: Workspace) => {
    if (confirmSlug !== workspace.slug) { setError(t('shell.confirmSlugError', { slug: workspace.slug })); return; }
    setDeleting(true); setError(null);
    try {
      await api.del(`/settings/workspaces/${workspace.id}`, { confirm_slug: workspace.slug });
      setDeletingId(null); setConfirmSlug('');
      const wasCurrent = workspace.slug === getWorkspace();
      await load();
      if (wasCurrent) {
        const next = workspaces.find(w => w.id !== workspace.id) ?? workspaces[0];
        selectWorkspace(next?.slug ?? 'personal');
        window.location.assign('/');
      }
    } catch (e: any) {
      setError(t('shell.deleteFailed', { error: String(e.message || e) }));
      setDeleting(false);
    }
  };
  const personal = async () => {
    setPersonalBusy(true); setError(null);
    try { const result = await api.post<{ workspace: Workspace }>('/settings/workspaces/personal'); select(result.workspace); }
    catch (e: any) { setError(String(e.message || e)); setPersonalBusy(false); }
  };
  if (!wsOpen) return null;
  return <Modal title={t('shell.switchWorkspace')} onClose={() => setWsOpen(false)} size="lg">
    {loading && <p>{t('shell.loadingWorkspaces')}</p>}
    {error && <p role="alert">{error}</p>}
    {/* 新建 */}
    <div className="card" style={{ padding: 12, marginBottom: 12 }}>
      <p style={{ fontWeight: 600, margin: '0 0 8px' }}>{t('shell.createWorkspace')}</p>
      <label style={{ display: 'grid', gap: 4, marginBottom: 8 }}>
        <span>{t('shell.workspaceName')}</span>
        <input className="field" value={newName} onChange={e => setNewName(e.target.value)} maxLength={120} placeholder={t('shell.workspaceName')} />
      </label>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
        <label style={{ display: 'grid', gap: 4 }}>
          <span>{t('shell.targetRole')}</span>
          <input className="field" value={newRole} onChange={e => setNewRole(e.target.value)} maxLength={160} />
        </label>
        <label style={{ display: 'grid', gap: 4 }}>
          <span>{t('shell.targetLevel')}</span>
          <input className="field" value={newLevel} onChange={e => setNewLevel(e.target.value)} maxLength={16} placeholder={t('shell.levelExample')} />
        </label>
      </div>
      <button className="btn btn-primary" disabled={creating || !newName.trim()} onClick={create}>
        {creating ? t('shell.creating') : t('shell.create')}
      </button>
    </div>
    {/* 列表 */}
    {workspaces.map(w => {
      const isEditing = editingId === w.id;
      const isDeleting = deletingId === w.id;
      const active = w.slug === getWorkspace();
      return <div key={w.id} className="card" style={{ padding: 10, marginBottom: 8 }}>
        {!isEditing && !isDeleting && <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="ws-avatar">🧭</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>{w.name}</div>
              <div className="pop-item-sub">{t('shell.workspaceWithSlug', { label: t(w.is_demo ? 'shell.demoSpace' : 'shell.personalSpace'), slug: w.slug })}</div>
            </div>
            {active && <span className="chip chip-purple">{t('shell.activeWorkspace')}</span>}
            <button className="btn btn-sm" onClick={() => select(w)}>{t('shell.enterWorkspace')}</button>
            <button className="btn btn-sm" onClick={() => startEdit(w)}>{t('shell.edit')}</button>
            <button className="btn btn-sm" onClick={() => startDelete(w)}>{t('shell.deleteWorkspace')}</button>
          </div>
          {(w.target_role || w.target_level) && <div className="pop-item-sub" style={{ marginTop: 4 }}>
            {t('shell.roleAndLevel', { role: w.target_role ?? '—', level: w.target_level ?? '—' })}
          </div>}
        </>}
        {isEditing && <>
          <label style={{ display: 'grid', gap: 4, marginBottom: 6 }}>
            <span>{t('shell.workspaceName')}</span>
            <input className="field" value={editName} onChange={e => setEditName(e.target.value)} maxLength={120} />
          </label>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
            <label style={{ display: 'grid', gap: 4 }}>
              <span>{t('shell.targetRole')}</span>
              <input className="field" value={editRole} onChange={e => setEditRole(e.target.value)} maxLength={160} />
            </label>
            <label style={{ display: 'grid', gap: 4 }}>
              <span>{t('shell.targetLevel')}</span>
              <input className="field" value={editLevel} onChange={e => setEditLevel(e.target.value)} maxLength={16} placeholder={t('shell.levelExample')} />
            </label>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary" disabled={savingEdit || !editName.trim()} onClick={() => saveEdit(w)}>{savingEdit ? t('shell.creating') : t('shell.save')}</button>
            <button className="btn" onClick={() => setEditingId(null)}>{t('shell.cancel')}</button>
          </div>
        </>}
        {isDeleting && <>
          <p style={{ fontWeight: 600, margin: '0 0 4px' }}>{t('shell.deleteWorkspaceTitle')}</p>
          <p className="pop-item-sub" style={{ marginBottom: 8 }}>{t('shell.deleteWorkspaceHint')}</p>
          <label style={{ display: 'grid', gap: 4, marginBottom: 8 }}>
            <span>{t('shell.confirmSlug', { slug: w.slug })}</span>
            <input className="field" value={confirmSlug} onChange={e => setConfirmSlug(e.target.value)} placeholder={w.slug} />
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-danger" disabled={deleting} onClick={() => remove(w)}>{deleting ? t('shell.deleting') : t('shell.deleteWorkspace')}</button>
            <button className="btn" onClick={() => setDeletingId(null)}>{t('shell.cancel')}</button>
          </div>
        </>}
      </div>;
    })}
    <button className="btn btn-outline" disabled={personalBusy} onClick={personal}>{personalBusy ? t('shell.openingPersonalWorkspace') : t('shell.enterPersonalWorkspace')}</button>
  </Modal>;
}
