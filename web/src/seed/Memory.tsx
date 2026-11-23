import { useCallback, useEffect, useRef, useState } from 'react';
import MemoryTimeline, { type MemoryEntry } from '../components/MemoryTimeline';
import { useWorkspaceApi } from './api';

type Experience = {
  id: number;
  text: string;
  revision: number;
  confirmed_at: string;
  source: 'user_statement';
};

function toMemoryEntry(experience: Experience): MemoryEntry {
  return {
    id: experience.id,
    content: experience.text,
    revision: experience.revision,
    created_at: experience.confirmed_at,
    confirmed_at: experience.confirmed_at,
    source: experience.source,
    mtype: 'InterviewExperience',
    status: 'ACTIVE',
  };
}

export default function Memory() {
  const { request } = useWorkspaceApi();
  const [entries, setEntries] = useState<Experience[]>([]);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const textarea = useRef<HTMLTextAreaElement>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setLoadError('');
    try {
      setEntries(await request<Experience[]>('/memories', { signal }));
    } catch (error) {
      if (!signal?.aborted) setLoadError(error instanceof Error ? error.message : '无法读取经历。');
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  function cancelEdit() {
    setEditing(null);
    setDraft('');
  }

  async function save() {
    if (busy || loading || !draft.trim()) return;
    setBusy(true);
    setError('');
    try {
      const path = editing === null ? '/memories' : '/memories/' + editing;
      const saved = await request<Experience>(path, {
        method: editing === null ? 'POST' : 'PUT',
        body: JSON.stringify({ text: draft.trim(), source: 'user_statement', confirmed: true }),
      });
      setEntries(old => editing === null ? [saved, ...old] : old.map(entry => entry.id === saved.id ? saved : entry));
      cancelEdit();
    } catch (error) {
      setError(error instanceof Error ? error.message : '无法保存经历。');
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    setBusy(true);
    setError('');
    try {
      await request('/memories/' + id, { method: 'DELETE' });
      setEntries(old => old.filter(entry => entry.id !== id));
      setRemoving(null);
      if (editing === id) cancelEdit();
    } catch (error) {
      setError(error instanceof Error ? error.message : '无法删除经历。');
    } finally {
      setBusy(false);
    }
  }

  const disabled = busy || loading;
  return <div className="memory-page">
    <header className="page-head">
      <div>
        <h1 className="page-title">项目记忆</h1>
        <p className="page-sub">写下你亲自做过的事。确认后，学习助手和面试官可以引用这些经历。</p>
      </div>
      <div className="page-actions">
        <button type="button" className="btn btn-outline" disabled={disabled} onClick={() => void refresh()}>刷新记忆</button>
      </div>
    </header>
    <form className="card memory-panel" onSubmit={event => { event.preventDefault(); void save(); }}>
      <label className="field">
        <span>{editing === null ? '添加一段经历' : '修改这段经历'}</span>
        <textarea
          ref={textarea}
          className="textarea"
          value={draft}
          rows={4}
          maxLength={2400}
          required
          disabled={busy}
          onChange={event => setDraft(event.target.value)}
          placeholder="例如：我负责一次校园社群拉新，设计了两种邀请文案，并比较它们的转化率。"
        />
      </label>
      <div className="mem-actions">
        <button type="submit" className="btn btn-primary" disabled={disabled || !draft.trim()}>确认并保存经历</button>
        {editing !== null && <button type="button" className="btn btn-outline" disabled={busy} onClick={cancelEdit}>取消修改</button>}
      </div>
    </form>
    {loadError && <div role="alert" className="demo-banner demo-banner-err">
      无法读取记忆：{loadError}。<button type="button" className="btn btn-outline btn-sm" disabled={disabled} onClick={() => void refresh()}>重试</button>
    </div>}
    {error && <div role="alert" className="demo-banner demo-banner-err">操作失败：{error}</div>}
    {loading && <p role="status">正在读取经历…</p>}
    {(!loadError || entries.length > 0) && <MemoryTimeline
      items={entries.map(toMemoryEntry)}
      title="已确认的经历"
      summary={entries.length + ' 条已保存'}
      empty={!loading && <div className="memory-panel"><h2>还没有保存的经历。</h2></div>}
      details={entry => <div className="mem-actions">
        <button type="button" className="btn btn-outline btn-sm" disabled={disabled} onClick={() => {
          setDraft(entry.content);
          setEditing(entry.id);
          setRemoving(null);
          setError('');
          textarea.current?.focus();
        }}>修改</button>
        {removing === entry.id ? <>
          <button type="button" className="btn btn-danger btn-sm" disabled={disabled} onClick={() => void remove(entry.id)}>确认删除这段经历</button>
          <button type="button" className="btn btn-outline btn-sm" disabled={busy} onClick={() => setRemoving(null)}>取消</button>
        </> : <button type="button" className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => {
          setRemoving(entry.id);
          setError('');
        }}>删除</button>}
      </div>}
    />}
  </div>;
}
