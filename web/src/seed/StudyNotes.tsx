import { useEffect, useState } from 'react';
import { NotebookFields, NotebookList, type NotebookSummary, type NotebookText } from '../components/NotebookPresentation';
import { SeedApiError, useWorkspaceApi } from './api';
import '../pages/studyNotebook.css';
import './study.css';

type Note = NotebookSummary & NotebookText & { version_id: number; versions: { id: number }[] };
type Props = { noteId?: number; onChat?: () => void };
const notesPath = '/coach/notes';

function NoteEditor({ id }: { id: number }) {
  const { request } = useWorkspaceApi();
  const [note, setNote] = useState<Note | null>(null);
  const [draft, setDraft] = useState<NotebookText | null>(null);
  const [version, setVersion] = useState<NotebookText | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [conflict, setConflict] = useState(false);
  const [revision, setRevision] = useState(0);
  const path = notesPath + '/' + id;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    request<Note>(path, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setNote(value); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [path, request, revision]);

  async function save() {
    if (!draft || !note || busy || loading || !draft.title.trim() || !draft.body.trim()) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const saved = await request<Note>(path, {
        method: 'PATCH',
        body: JSON.stringify({ title: draft.title, body: draft.body, expected_version_id: note.version_id }),
      });
      setNote(saved);
      setDraft(null);
      setConflict(false);
      setNotice('笔记已保存。');
    } catch (error) {
      setError(error instanceof Error ? error.message : '保存失败，编辑内容已保留。');
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
    setError('');
    try {
      setVersion(await request<NotebookText>(path + '/versions/' + versionId));
    } catch (error) {
      setError(error instanceof Error ? error.message : '读取历史版本失败。');
    } finally {
      setBusy(false);
    }
  }

  return <section className="study-notebook-paper study-notebook-panel">
    <div className="study-notebook-actions">
      <h2>{note?.title ?? '笔记编辑'}</h2>
      <button type="button" className="btn btn-outline" disabled={loading || busy || (!!draft && !conflict)} onClick={() => setRevision(value => value + 1)}>刷新笔记</button>
    </div>
    {loading && <p role="status">正在读取笔记…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {conflict && note && !loading && !error && <section className="card">
      <h3>这条笔记有新版本</h3>
      <p>下方保留了你的草稿。核对最新内容后，可以合并修改并保存。</p>
      <strong>{note.title}</strong><p style={{ whiteSpace: 'pre-wrap' }}>{note.body}</p>
    </section>}
    {note && <form onSubmit={event => { event.preventDefault(); void save(); }}>
      <NotebookFields text={draft ?? note} disabled={loading || busy} onChange={setDraft} />
      <div className="study-notebook-actions">
        <button className="btn btn-primary" disabled={!draft?.title.trim() || !draft.body.trim() || loading || busy || (conflict && !!error)}>保存笔记</button>
        {draft && <button type="button" className="btn btn-outline" disabled={busy} onClick={() => { setDraft(null); setConflict(false); }}>放弃修改</button>}
      </div>
    </form>}
    {!!note?.versions.length && <details className="study-notebook-details">
      <summary>版本历史（{note.versions.length}）</summary>
      <ul className="study-notebook-list">{note.versions.map(item => <li key={item.id}>
        <button type="button" className="btn btn-outline" disabled={busy} onClick={() => void inspect(item.id)}>查看版本 {item.id}</button>
      </li>)}</ul>
      {version && <><h3>{version.title}</h3><p className="study-notebook-text">{version.body}</p></>}
    </details>}
  </section>;
}

function NotesList({ onChat }: Pick<Props, 'onChat'>) {
  const { request } = useWorkspaceApi();
  const [query, setQuery] = useState('');
  const [notes, setNotes] = useState<NotebookSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    request<{ items: NotebookSummary[] }>(notesPath + '?q=' + encodeURIComponent(query.trim()), { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setNotes(result.items); })
      .catch((error: Error) => { if (!controller.signal.aborted) setError(error.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, request, revision]);

  return <section className="study-notebook-panel study-notebook-paper" aria-label="笔记列表">
    <label className="study-notebook-field">搜索笔记
      <input className="inp" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="输入关键词" />
    </label>
    {loading && <p role="status">正在搜索笔记…</p>}
    {error && <p role="alert">{error} <button type="button" className="btn btn-outline" onClick={() => setRevision(value => value + 1)}>重试</button></p>}
    {!loading && !error && !notes.length && <div className="study-notebook-empty">
      <p>{query.trim() ? '没有匹配的笔记。' : '还没有学习笔记。可以在学习聊天中保存助手的回复。'}</p>
      {query.trim() ? <button type="button" className="btn btn-outline" onClick={() => setQuery('')}>清除搜索</button>
        : onChat && <button type="button" className="btn btn-outline" onClick={onChat}>去学习聊天 →</button>}
    </div>}
    {!loading && !error && <NotebookList notes={notes} renderLink={(note, content) => <a href={'/beta/study/notes/' + note.id}>{content}</a>} />}
  </section>;
}

export default function PublicStudyNotes({ noteId, onChat }: Props) {
  return <div className="public-notebook study-notebook">
    <header className="study-notebook-header"><div>
      {noteId && <a href="/beta/study/notes">← 学习笔记</a>}
      <h1>{noteId ? '笔记编辑' : '学习笔记'}</h1>
    </div></header>
    {noteId ? <NoteEditor key={noteId} id={noteId} /> : <NotesList onChat={onChat} />}
  </div>;
}
