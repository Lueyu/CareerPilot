import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import { StudyCards, type StudyCard } from '../components/StudyCards';
import StudyShell, { draftKey, remember, stored } from '../components/StudyShell';
import StudyCompare, { comparePath } from './StudyCompare';
import './studyNotebook.css';

type Id = string | number;
type Draft = { title: string; body: string };
type Source = { session_id: Id; message_id: Id; role: string; quote: string };
type Revision = Draft & { id: Id; conflict: boolean; sources?: Source[]; session_ids?: Id[] };
type NoteSummary = { id: Id; title: string; updated_at?: string };
type Note = NoteSummary & Draft & {
  version_id: Id;
  sources: Source[];
  versions: Pick<Revision, 'id' | 'conflict' | 'session_ids'>[];
  session_ids: Id[];
};
type WorkingDraft = { base: Id; text: Draft; source?: Id };
type Items<T> = { items: T[] };

const sameId = (a: Id, b: Id) => String(a) === String(b);
const sameText = (a: Draft, b: Draft) => a.title === b.title && a.body === b.body;
const notePath = (id: Id) => '/study/notes/' + encodeURIComponent(id);
const chatPath = (id: Id) => '/study/chat/' + encodeURIComponent(id);
const roles: Record<string, string> = { user: '我', assistant: '助手', system: '系统' };
const isId = (value: unknown): value is Id => typeof value === 'number' || typeof value === 'string';

function recoverDraft(key: string): WorkingDraft | null {
  try {
    const draft = JSON.parse(stored(key));
    return isId(draft?.base) && typeof draft.text?.title === 'string' && typeof draft.text.body === 'string'
      && (draft.source === undefined || isId(draft.source)) ? draft : null;
  } catch {
    return null;
  }
}

function Sources({ sources, noteId }: { sources: Source[]; noteId: Id }) {
  return sources.length ? <ul>{sources.map((source, index) => (
    <li key={source.session_id + ':' + source.message_id + ':' + index}>
      <Link to={chatPath(source.session_id) + '?message=' + encodeURIComponent(source.message_id)}>
        查看原消息 · {roles[source.role] ?? source.role}
      </Link>
      {' · '}<Link to={comparePath(noteId, source.session_id, source.message_id)}>对照此消息</Link>
      <blockquote style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{source.quote}</blockquote>
    </li>
  ))}</ul> : <p className="hint">此版本暂无来源引用。</p>;
}

function NoteEditor({ id, onSaved, onGenerated }: {
  id: string;
  onSaved: () => void;
  onGenerated: (cards: StudyCard[]) => void;
}) {
  const [search] = useSearchParams();
  const location = useLocation();
  const focus = location.state;
  const isCompare = search.get('view') === 'compare';
  const storageKey = draftKey(`note:${id}`);
  const [note, setNote] = useState<Note | null>(null);
  const [draft, setDraft] = useState<WorkingDraft | null>(() => recoverDraft(storageKey));
  const [recovered, setRecovered] = useState(!!draft);
  const [revision, setRevision] = useState<Revision | null>(null);
  const [reading, setReading] = useState(true);
  const [readingVersion, setReadingVersion] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const noteRequest = useRef(0);
  const versionRequest = useRef(0);

  async function reload() {
    const request = ++noteRequest.current;
    setReading(true);
    setError('');
    try {
      const latest = await api.get<Note>(notePath(id));
      if (request === noteRequest.current) setNote(latest);
    } catch {
      if (request === noteRequest.current) setError('读取笔记失败，请重试。');
    } finally {
      if (request === noteRequest.current) setReading(false);
    }
  }

  useEffect(() => {
    void reload();
    return () => {
      noteRequest.current++;
      versionRequest.current++;
    };
  }, [id]);

  function updateDraft(next: WorkingDraft | null) {
    remember(storageKey, next ? JSON.stringify(next) : '');
    setDraft(next);
    setRecovered(false);
  }

  async function inspect(version: Id) {
    const request = ++versionRequest.current;
    setRevision(null);
    setReadingVersion(true);
    setError('');
    try {
      const result = await api.get<Revision>(notePath(id) + '/versions/' + encodeURIComponent(version));
      if (request === versionRequest.current) {
        setRevision({ ...result, id: version, conflict: note?.versions.some(v => sameId(v.id, version) && v.conflict) || result.conflict });
      }
    } catch {
      if (request === versionRequest.current) setError('读取历史版本失败，请重试。');
    } finally {
      if (request === versionRequest.current) setReadingVersion(false);
    }
  }

  async function save() {
    if (!note || !draft || busy || reading || !sameId(draft.base, note.version_id)) return;
    const request = noteRequest.current;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api.patch<{ conflict?: boolean } | undefined>(notePath(id), {
        expected_version_id: draft.base,
        title: draft.text.title,
        body: draft.text.body,
        ...(draft.source !== undefined ? { source_version_id: draft.source } : {}),
      });
      const latest = await api.get<Note>(notePath(id));
      if (request !== noteRequest.current) return;
      setNote(latest);
      if (result?.conflict || !sameText(latest, draft.text)) {
        setError('保存产生了冲突。可从版本历史查看冲突草稿，或保留当前编辑后重新保存。');
      } else {
        updateDraft(null);
        onSaved();
        setNotice('笔记已保存。');
      }
    } catch (exception) {
      if (request !== noteRequest.current) return;
      if (exception instanceof ApiError && exception.status === 409) {
        await reload();
        setNotice('保存时发现版本冲突，编辑内容已保留。请核对最新版本与冲突草稿。');
      } else {
        setError('无法确认保存结果，编辑内容已保留。可刷新笔记核对后重试。');
      }
    } finally {
      setBusy(false);
    }
  }

  async function generate(version: Id) {
    const request = noteRequest.current;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api.post<Items<StudyCard>>(notePath(id) + '/cards', { version_id: version });
      onGenerated(result.items);
      if (request === noteRequest.current) setNotice(result.items.length ? '候选卡片已生成。' : '此版本没有生成新的卡片。');
    } catch {
      if (request === noteRequest.current) setError('生成卡片失败，请重试。');
    } finally {
      setBusy(false);
    }
  }

  const text = draft?.text ?? note;
  const stale = note && draft && !sameId(draft.base, note.version_id);
  const dirty = note && draft && (stale || draft.source !== undefined || !sameText(note, draft.text));

  const primary = note?.sources[0];
  const requestedSession = search.get('session') ?? (isId(focus?.sourceSession) ? String(focus.sourceSession) : null);
  const allowedSessions = note ? [...note.session_ids, ...note.sources.map(source => source.session_id),
    ...note.versions.flatMap(version => version.session_ids ?? [])] : [];
  const defaultSession = primary?.session_id ?? note?.session_ids[0];
  const session = requestedSession === null ? defaultSession : allowedSessions.find(candidate => sameId(candidate, requestedSession));
  const invalidSource = !!note && requestedSession !== null && session === undefined;
  const toggleSession = session ?? defaultSession;
  const message = search.get('message') ?? (isId(focus?.sourceMessage) ? focus.sourceMessage : undefined) ?? note?.sources.find(source => session !== undefined && sameId(source.session_id, session))?.message_id;
  const compared = isCompare && session !== undefined;

  return <>
    <header className="study-notebook-header">
      <div><Link to="/study/notes">← 学习笔记</Link><h1>{note?.title ?? '笔记编辑'}</h1></div>
      <div className="study-notebook-actions" aria-label="笔记视图">
        <Link className="btn btn-outline btn-sm" aria-current={!isCompare ? 'page' : undefined} to={notePath(id)} state={{sourceSession:session,sourceMessage:message}}>专注笔记</Link>
        {toggleSession !== undefined && <Link className="btn btn-outline btn-sm" aria-current={compared ? 'page' : undefined}
          to={comparePath(id, toggleSession, invalidSource ? primary?.message_id : message)}>对照会话</Link>}
      </div>
    </header>
    {isCompare && invalidSource && <p role="alert">指定会话不是这份笔记的来源。</p>}
    {compared && <a className="study-notebook-source-jump" href="#study-conversation">跳到来源会话 ↓</a>}
    <div className={'study-notebook-layout' + (compared ? ' is-compare' : '')}>
    <section className="study-notebook-paper study-notebook-panel" aria-labelledby="study-note-editor">
    <div className="study-notebook-actions">
      <h2 id="study-note-editor">笔记编辑</h2>
      <button type="button" className="btn btn-outline btn-sm" disabled={busy || reading} onClick={() => void reload()}>刷新笔记</button>
    </div>
    {reading && <p role="status">正在读取笔记…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {note && text && <>
      <form onSubmit={event => { event.preventDefault(); void save(); }}>
        <label className="study-notebook-field">标题
          <input className="inp" required value={text.title} disabled={busy}
            onChange={event => updateDraft({ base: note.version_id, ...draft, text: { title: event.target.value, body: text.body } })} />
        </label>
        <label className="study-notebook-field">正文
          <textarea aria-label="正文" className="inp" rows={12} value={text.body} disabled={busy}
            onChange={event => updateDraft({ base: note.version_id, ...draft, text: { title: text.title, body: event.target.value } })} />
        </label>
        {dirty && <p className="hint">{recovered ? '已恢复上次未保存的编辑。' : '有未保存的编辑。'}{draft?.source !== undefined && '草稿来自版本 ' + draft.source + '。'}</p>}
        {stale && <div>
          <p role="alert">已保存版本发生变化。请核对后决定是否继续保留当前编辑。</p>
          <details><summary>查看最新保存内容</summary>
            <h3>{note.title}</h3><p className="study-notebook-text">{note.body}</p>
          </details>
          <button type="button" className="btn btn-outline" disabled={busy || reading}
            onClick={() => updateDraft({ ...draft, base: note.version_id })}>保留我的编辑，改用最新版本作为保存基础</button>
        </div>}
        <div className="study-notebook-actions">
          <button className="btn btn-primary" disabled={busy || reading || !dirty || !!stale || !text.title.trim()}>保存笔记</button>
          {dirty && <button type="button" className="btn btn-outline" disabled={busy || reading}
            onClick={() => {
              if (window.confirm('确定放弃未保存的编辑？')) {
                updateDraft(null);
                setNotice('');
                setError('');
              }
            }}>放弃编辑</button>}
          <button type="button" className="btn btn-outline" disabled={busy || reading}
            onClick={() => void generate(note.version_id)}>从已保存版本 {note.version_id} 生成卡片</button>
        </div>
      </form>
      <Link to="/study/notes?tab=review">查看复习卡片 →</Link>
      <details className="study-notebook-details">
      <summary>已保存版本的来源（{note.sources.length}）</summary>
      <Sources sources={note.sources} noteId={id} />
      <div className="study-notebook-actions">
        {note.session_ids.map((session, index) => (
          <Link className="btn btn-outline btn-sm" key={session} to={chatPath(session)}>
            继续学习{note.session_ids.length > 1 ? ' · 会话 ' + (index + 1) : ''}
          </Link>
        ))}
      </div>
      </details>
      <details className="study-notebook-details">
      <summary>版本历史（{note.versions.length}）</summary>
      <ul className="study-notebook-list">{note.versions.map(version => (
        <li key={version.id}>
          <button type="button" className="btn btn-outline btn-sm"
            aria-pressed={revision ? sameId(revision.id, version.id) : false}
            onClick={() => void inspect(version.id)}>
            版本 {version.id}{version.conflict ? ' · 冲突草稿' : sameId(version.id, note.version_id) ? ' · 当前' : ''}
          </button>
        </li>
      ))}</ul>
      {readingVersion && <p role="status">正在读取版本…</p>}
      {revision && <section className="study-notebook-revision" aria-label={'版本 ' + revision.id}>
        <h4>{revision.title}</h4>
        <p className="study-notebook-text">{revision.body}</p>
        {revision.sources && <Sources sources={revision.sources} noteId={id} />}
        {revision.conflict ? <button type="button" className="btn btn-outline" disabled={busy || reading}
          onClick={() => {
            if (dirty && !window.confirm('用此冲突草稿替换当前未保存的编辑？')) return;
            updateDraft({ base: note.version_id, text: revision, source: revision.id });
            setNotice('冲突草稿已载入编辑区。请核对后点击保存笔记。');
          }}>采用此冲突草稿</button> : !sameId(revision.id, note.version_id) &&
          <button type="button" className="btn btn-outline" disabled={busy || reading}
            onClick={() => void generate(revision.id)}>从版本 {revision.id} 生成卡片</button>}
      </section>}
      </details>
    </>}
  </section>
  {compared && <StudyCompare key={String(session)} sessionId={session} messageId={message} noteId={id} />}
  </div>
  </>;
}

export function StudyNotebook() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const review = !id && search.get('tab') === 'review';
  const [query, setQuery] = useState('');
  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [generated, setGenerated] = useState<StudyCard[]>([]);
  const [dueCount, setDueCount] = useState<number | null>(null);

  useEffect(() => {
    if (id || review) return;
    let active = true;
    setDueCount(null);
    api.get<Items<StudyCard>>('/study/cards?status=DUE')
      .then(result => { if (active) setDueCount(result.items.length); })
      .catch(() => { if (active) setDueCount(null); });
    return () => { active = false; };
  }, [id, review]);

  useEffect(() => {
    let current = true;
    if (id || review) return;
    setLoading(true);
    setError(false);
    api.get<Items<NoteSummary>>('/study/notes?q=' + encodeURIComponent(query.trim()))
      .then(result => { if (current) setNotes(result.items); })
      .catch(() => { if (current) setError(true); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [id, review, query, refresh]);

  return <StudyShell section={review ? 'review' : 'notes'}><div className="study-notebook">
      {!id && <header className="study-notebook-header"><div>
        <h1>{review ? '复习与练习' : '学习笔记'}</h1>
        <p>{review ? '复习到期卡片、确认新卡片，并从卡片发起练习。' : '整理学习内容，随时回到原始对话。'}</p>
      </div></header>}
      {!id && !review && <section className="study-notebook-review-strip" aria-labelledby="study-review-strip-title">
        <div><h2 id="study-review-strip-title">卡片复习</h2><p>{dueCount === null ? '回顾知识点，或确认待复习的卡片。' : dueCount ? `${dueCount} 张卡片到期，来回顾一下。` : '目前没有到期卡片。'}</p></div>
        <Link className="btn btn-primary" to="/study/notes?tab=review">进入复习 →</Link>
      </section>}
      {!id && !review && <section className="study-notebook-panel study-notebook-paper" aria-label="笔记列表">
        <label className="study-notebook-field">搜索笔记
          <input className="inp" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="输入关键词" />
        </label>
        {loading && <p role="status">正在搜索笔记…</p>}
        {error && <p role="alert">读取笔记列表失败。 <button type="button" className="btn btn-outline btn-sm"
          onClick={() => setRefresh(value => value + 1)}>重试</button></p>}
        {!loading && !error && !notes.length && <div className="study-notebook-empty">
          <p>{query.trim() ? '没有匹配的笔记。' : '还没有学习笔记。可以在学习聊天中把对话整理成笔记。'}</p>
          {query.trim() ? <button type="button" className="btn btn-outline" onClick={() => setQuery('')}>清除搜索</button>
            : <Link className="btn btn-outline" to="/study/chat">去学习聊天 →</Link>}
        </div>}
        <ul className="study-notebook-list study-notebook-library">{notes.map(note => (
          <li key={note.id}><Link to={notePath(note.id)}>
            <strong>{note.title}</strong><span className="study-notebook-library-date">{note.updated_at && <time dateTime={note.updated_at}>
              {new Date(note.updated_at).toLocaleDateString('zh-CN')}</time>} <span aria-hidden="true">→</span></span>
          </Link></li>
        ))}</ul>
      </section>}
      {id && <NoteEditor key={draftKey(`note:${id}`)} id={id} onSaved={() => setRefresh(value => value + 1)}
        onGenerated={cards => setGenerated(cards)} />}
    {review && <StudyCards generated={generated} />}
  </div></StudyShell>;
}

export default StudyNotebook;
