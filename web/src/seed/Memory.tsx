import { useCallback, useEffect, useRef, useState } from 'react';
import { useT } from '../i18n';
import MemoryTimeline, { type MemoryEntry } from '../components/MemoryTimeline';
import { messageFor, renderMessage, useWorkspaceApi, type UiMessage } from './api';

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
  const t = useT();
  const { request } = useWorkspaceApi();
  const [entries, setEntries] = useState<Experience[]>([]);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<UiMessage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<UiMessage | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setLoadError(null);
    try {
      setEntries(await request<Experience[]>('/memories', { signal }));
    } catch (error) {
      if (!signal?.aborted) setLoadError(messageFor(error, 'registered.experienceLoadFailed'));
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
    setError(null);
    try {
      const path = editing === null ? '/memories' : '/memories/' + editing;
      const saved = await request<Experience>(path, {
        method: editing === null ? 'POST' : 'PUT',
        body: JSON.stringify({ text: draft.trim(), source: 'user_statement', confirmed: true }),
      });
      setEntries(old => editing === null ? [saved, ...old] : old.map(entry => entry.id === saved.id ? saved : entry));
      cancelEdit();
    } catch (error) {
      setError(messageFor(error, 'registered.experienceSaveFailed'));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    setBusy(true);
    setError(null);
    try {
      await request('/memories/' + id, { method: 'DELETE' });
      setEntries(old => old.filter(entry => entry.id !== id));
      setRemoving(null);
      if (editing === id) cancelEdit();
    } catch (error) {
      setError(messageFor(error, 'registered.experienceDeleteFailed'));
    } finally {
      setBusy(false);
    }
  }

  const disabled = busy || loading;
  return <div className="memory-page">
    <header className="page-head">
      <div>
        <h1 className="page-title">{t('registered.projectMemory')}</h1>
        <p className="page-sub">{t('registered.projectMemoryGuidance')}</p>
      </div>
      <div className="page-actions">
        <button type="button" className="btn btn-outline" disabled={disabled} onClick={() => void refresh()}>{t('registered.refreshMemory')}</button>
      </div>
    </header>
    <form className="card memory-panel" onSubmit={event => { event.preventDefault(); void save(); }}>
      <label className="field">
        <span>{editing === null ? t('registered.addExperience') : t('registered.editExperience')}</span>
        <textarea
          ref={textarea}
          className="textarea"
          value={draft}
          rows={4}
          maxLength={2400}
          required
          disabled={busy}
          onChange={event => setDraft(event.target.value)}
          placeholder={t('registered.experiencePlaceholder')}
        />
      </label>
      <div className="mem-actions">
        <button type="submit" className="btn btn-primary" disabled={disabled || !draft.trim()}>{t('registered.confirmAndSaveExperience')}</button>
        {editing !== null && <button type="button" className="btn btn-outline" disabled={busy} onClick={cancelEdit}>{t('registered.cancelEdit')}</button>}
      </div>
    </form>
    {loadError && <div role="alert" className="demo-banner demo-banner-err">
      {t('registered.memoryReadError', { error: renderMessage(loadError, t) })}<button type="button" className="btn btn-outline btn-sm" disabled={disabled} onClick={() => void refresh()}>{t('registered.retry')}</button>
    </div>}
    {error && <div role="alert" className="demo-banner demo-banner-err">{t('registered.memoryActionError', { error: renderMessage(error, t) })}</div>}
    {loading && <p role="status">{t('registered.experiencesLoading')}</p>}
    {(!loadError || entries.length > 0) && <MemoryTimeline
      items={entries.map(toMemoryEntry)}
      title={t('registered.confirmedExperiences')}
      summary={t('registered.savedExperienceCount', { count: entries.length })}
      empty={!loading && <div className="memory-panel"><h2>{t('registered.noSavedExperiences')}</h2></div>}
      details={entry => <div className="mem-actions">
        <button type="button" className="btn btn-outline btn-sm" disabled={disabled} onClick={() => {
          setDraft(entry.content);
          setEditing(entry.id);
          setRemoving(null);
          setError(null);
          textarea.current?.focus();
        }}>{t('registered.edit')}</button>
        {removing === entry.id ? <>
          <button type="button" className="btn btn-danger btn-sm" disabled={disabled} onClick={() => void remove(entry.id)}>{t('registered.confirmDeleteExperience')}</button>
          <button type="button" className="btn btn-outline btn-sm" disabled={busy} onClick={() => setRemoving(null)}>{t('registered.cancel')}</button>
        </> : <button type="button" className="btn btn-ghost btn-sm" disabled={disabled} onClick={() => {
          setRemoving(entry.id);
          setError(null);
        }}>{t('registered.delete')}</button>}
      </div>}
    />}
  </div>;
}
