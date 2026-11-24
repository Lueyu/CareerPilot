import { useEffect, useState } from 'react';
import { useT } from '../i18n';
import { NotebookFields, NotebookList, type NotebookSummary, type NotebookText } from '../components/NotebookPresentation';
import { messageFor, renderMessage, SeedApiError, useWorkspaceApi, type MessageKey, type UiMessage } from './api';
import '../pages/studyNotebook.css';
import './study.css';

type Note = NotebookSummary & NotebookText & { version_id: number; versions: { id: number }[] };
type Props = { noteId?: number; onChat?: () => void };
const notesPath = '/coach/notes';

function NoteEditor({ id }: { id: number }) {
  const t = useT();
  const { request } = useWorkspaceApi();
  const [note, setNote] = useState<Note | null>(null);
  const [draft, setDraft] = useState<NotebookText | null>(null);
  const [version, setVersion] = useState<NotebookText | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<UiMessage | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [conflict, setConflict] = useState(false);
  const [revision, setRevision] = useState(0);
  const path = notesPath + '/' + id;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    request<Note>(path, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setNote(value); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(messageFor(error, 'registered.requestIncomplete')); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [path, request, revision]);

  async function save() {
    if (!draft || !note || busy || loading || !draft.title.trim() || !draft.body.trim()) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await request<Note>(path, {
        method: 'PATCH',
        body: JSON.stringify({ title: draft.title, body: draft.body, expected_version_id: note.version_id }),
      });
      setNote(saved);
      setDraft(null);
      setConflict(false);
      setNotice('registered.noteSaved');
    } catch (error) {
      setError(messageFor(error, 'registered.noteSaveFailed'));
      if (error instanceof SeedApiError && error.status === 409) {
        setConflict(true);
        setRevision(value => value + 1);
      }
    } finally {
      setBusy(false);
    }
  }

  async function inspect(versionId: number) {
    setBusy(true);
    setError(null);
    try {
      setVersion(await request<NotebookText>(path + '/versions/' + versionId));
    } catch (error) {
      setError(messageFor(error, 'registered.versionLoadFailed'));
    } finally {
      setBusy(false);
    }
  }

  return <section className="study-notebook-paper study-notebook-panel">
    <div className="study-notebook-actions">
      <h2>{note?.title ?? t('registered.noteEditor')}</h2>
      <button type="button" className="btn btn-outline" disabled={loading || busy || (!!draft && !conflict)} onClick={() => setRevision(value => value + 1)}>{t('registered.refreshNote')}</button>
    </div>
    {loading && <p role="status">{t('registered.noteLoading')}</p>}
    {error && <p role="alert">{renderMessage(error, t)}</p>}
    {notice && <p role="status">{t(notice)}</p>}
    {conflict && note && !loading && !error && <section className="card">
      <h3>{t('registered.noteConflictTitle')}</h3>
      <p>{t('registered.noteConflictHelp')}</p>
      <strong>{note.title}</strong><p style={{ whiteSpace: 'pre-wrap' }}>{note.body}</p>
    </section>}
    {note && <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <NotebookFields text={draft ?? note} disabled={loading || busy} onChange={setDraft} />
      <div className="study-notebook-actions">
        <button className="btn btn-primary" disabled={!draft?.title.trim() || !draft.body.trim() || loading || busy || (conflict && !!error)}>{t('registered.saveNote')}</button>
        {draft && <button type="button" className="btn btn-outline" disabled={busy} onClick={() => { setDraft(null); setConflict(false); }}>{t('registered.discardChanges')}</button>}
      </div>
    </form>}
    {!!note?.versions.length && <details className="study-notebook-details">
      <summary>{t('registered.versionHistoryCount', { count: note.versions.length })}</summary>
      <ul className="study-notebook-list">{note.versions.map(item => <li key={item.id}>
        <button type="button" className="btn btn-outline" disabled={busy} onClick={() => void inspect(item.id)}>{t('registered.viewNoteVersion', { version: item.id })}</button>
      </li>)}</ul>
      {version && <><h3>{version.title}</h3><p className="study-notebook-text">{version.body}</p></>}
    </details>}
  </section>;
}

function NotesList({ onChat }: Pick<Props, 'onChat'>) {
  const t = useT();
  const { request } = useWorkspaceApi();
  const [query, setQuery] = useState('');
  const [notes, setNotes] = useState<NotebookSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<UiMessage | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    request<{ items: NotebookSummary[] }>(notesPath + '?q=' + encodeURIComponent(query.trim()), { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setNotes(result.items); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(messageFor(error, 'registered.requestIncomplete')); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, request, revision]);

  return <section className="study-notebook-panel study-notebook-paper" aria-label={t('registered.noteList')}>
    <label className="study-notebook-field">{t('registered.searchNotes')}
      <input className="inp" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={t('registered.noteSearchPlaceholder')} />
    </label>
    {loading && <p role="status">{t('registered.notesSearching')}</p>}
    {error && <p role="alert">{renderMessage(error, t)} <button type="button" className="btn btn-outline" onClick={() => setRevision(value => value + 1)}>{t('registered.retry')}</button></p>}
    {!loading && !error && !notes.length && <div className="study-notebook-empty">
      <p>{query.trim() ? t('registered.noMatchingNotes') : t('registered.noStudyNotes')}</p>
      {query.trim() ? <button type="button" className="btn btn-outline" onClick={() => setQuery('')}>{t('registered.clearSearch')}</button>
        : onChat && <button type="button" className="btn btn-outline" onClick={onChat}>{t('registered.goToStudyChat')}</button>}
    </div>}
    {!loading && !error && <NotebookList notes={notes} renderLink={(note, content) => <a href={'/beta/study/notes/' + note.id}>{content}</a>} />}
  </section>;
}

export default function PublicStudyNotes({ noteId, onChat }: Props) {
  const t = useT();
  return <div className="public-notebook study-notebook">
    <header className="study-notebook-header"><div>
      {noteId && <a href="/beta/study/notes">{t('registered.backToStudyNotes')}</a>}
      <h1>{noteId ? t('registered.noteEditor') : t('registered.studyNotes')}</h1>
    </div></header>
    {noteId ? <NoteEditor key={noteId} id={noteId} /> : <NotesList onChat={onChat} />}
  </div>;
}
