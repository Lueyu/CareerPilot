import { useT } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import { StudyCards, type StudyCard } from '../components/StudyCards';
import StudyShell, { draftKey, remember, stored } from '../components/StudyShell';
import { NotebookFields, NotebookList } from '../components/NotebookPresentation';
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
const roles: Record<string, string> = { user: "study.me", assistant: "study.assistant", system: "study.system" };
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
  const t = useT();

  return sources.length ? <ul>{sources.map((source, index) => (
    <li key={source.session_id + ':' + source.message_id + ':' + index}>
      <Link to={chatPath(source.session_id) + '?message=' + encodeURIComponent(source.message_id)}>
        {t("study.viewOriginalMessageWithRole", { role: roles[source.role] ? t(roles[source.role]) : source.role })}
      </Link>
      {' · '}<Link to={comparePath(noteId, source.session_id, source.message_id)}>{t("study.compareThisMessage")}</Link>
      <blockquote style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{source.quote}</blockquote>
    </li>
  ))}</ul> : <p className="hint">{t("study.noVersionSources")}</p>;
}

function NoteEditor({ id, onSaved, onGenerated }: {
  id: string;
  onSaved: () => void;
  onGenerated: (cards: StudyCard[]) => void;
}) {
  const t = useT();

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
      if (request === noteRequest.current) setError("study.noteLoadFailed");
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
      if (request === versionRequest.current) setError("study.versionLoadFailed");
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
        setError("study.noteSaveConflict");
      } else {
        updateDraft(null);
        onSaved();
        setNotice("study.noteSaved");
      }
    } catch (exception) {
      if (request !== noteRequest.current) return;
      if (exception instanceof ApiError && exception.status === 409) {
        await reload();
        setNotice("study.noteVersionConflictDetected");
      } else {
        setError("study.noteSaveUnconfirmed");
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
      if (request === noteRequest.current) setNotice(result.items.length ? "study.suggestedCardsGenerated" : "study.noNewCardsFromVersion");
    } catch {
      if (request === noteRequest.current) setError("study.createCardsFailed");
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
      <div><Link to="/study/notes">{t("study.backToStudyNotes")}</Link><h1>{note?.title ?? t("study.noteEditor")}</h1></div>
      <div className="study-notebook-actions" aria-label={t("study.noteView")}>
        <Link className="btn btn-outline btn-sm" aria-current={!isCompare ? 'page' : undefined} to={notePath(id)} state={{sourceSession:session,sourceMessage:message}}>{t("study.focusOnNote")}</Link>
        {toggleSession !== undefined && <Link className="btn btn-outline btn-sm" aria-current={compared ? 'page' : undefined}
          to={comparePath(id, toggleSession, invalidSource ? primary?.message_id : message)}>{t("study.compareSession")}</Link>}
      </div>
    </header>
    {isCompare && invalidSource && <p role="alert">{t("study.sessionNotNoteSource")}</p>}
    {compared && <a className="study-notebook-source-jump" href="#study-conversation">{t("study.jumpToSourceSession")}</a>}
    <div className={'study-notebook-layout' + (compared ? ' is-compare' : '')}>
    <section className="study-notebook-paper study-notebook-panel" aria-labelledby="study-note-editor">
    <div className="study-notebook-actions">
      <h2 id="study-note-editor">{t("study.noteEditor")}</h2>
      <button type="button" className="btn btn-outline btn-sm" disabled={busy || reading} onClick={() => void reload()}>{t("study.refreshNote")}</button>
    </div>
    {reading && <p role="status">{t("study.loadingNote")}</p>}
    {error && <p role="alert">{t(error)}</p>}
    {notice && <p role="status">{t(notice)}</p>}
    {note && text && <>
      <form onSubmit={event => { event.preventDefault(); void save(); }}>
        <NotebookFields text={text} disabled={busy}
          onChange={next => updateDraft({ base: note.version_id, ...draft, text: next })} />
        {dirty && <p className="hint">{recovered ? t("study.unsavedEditsRecovered") : t("study.unsavedEdits")}{draft?.source !== undefined && t("study.draftFromVersion", { version: draft.source })}</p>}
        {stale && <div>
          <p role="alert">{t("study.noteSavedVersionChanged")}</p>
          <details><summary>{t("study.viewLatestSavedContent")}</summary>
            <h3>{note.title}</h3><p className="study-notebook-text">{note.body}</p>
          </details>
          <button type="button" className="btn btn-outline" disabled={busy || reading}
            onClick={() => updateDraft({ ...draft, base: note.version_id })}>{t("study.keepEditsOnLatestVersion")}</button>
        </div>}
        <div className="study-notebook-actions">
          <button className="btn btn-primary" disabled={busy || reading || !dirty || !!stale || !text.title.trim()}>{t("study.saveNote")}</button>
          {dirty && <button type="button" className="btn btn-outline" disabled={busy || reading}
            onClick={() => {
              if (window.confirm(t("study.confirmDiscardNoteEdits"))) {
                updateDraft(null);
                setNotice('');
                setError('');
              }
            }}>{t("study.discardEdits")}</button>}
          <button type="button" className="btn btn-outline" disabled={busy || reading}
            onClick={() => void generate(note.version_id)}>{t("study.generateCardsFromSavedVersion", { version: note.version_id })}</button>
        </div>
      </form>
      <Link to="/study/notes?tab=review">{t("study.viewReviewCards")}</Link>
      <details className="study-notebook-details">
      <summary>{t("study.savedVersionSources", { count: note.sources.length })}</summary>
      <Sources sources={note.sources} noteId={id} />
      <div className="study-notebook-actions">
        {note.session_ids.map((session, index) => (
          <Link className="btn btn-outline btn-sm" key={session} to={chatPath(session)}>
            {t("study.continueSession", { session: note.session_ids.length > 1 ? t("study.numberedSession", { session: index + 1 }) : '' })}
          </Link>
        ))}
      </div>
      </details>
      <details className="study-notebook-details">
      <summary>{t("study.versionHistory", { count: note.versions.length })}</summary>
      <ul className="study-notebook-list">{note.versions.map(version => (
        <li key={version.id}>
          <button type="button" className="btn btn-outline btn-sm"
            aria-pressed={revision ? sameId(revision.id, version.id) : false}
            onClick={() => void inspect(version.id)}>
            {t("study.noteVersionStatus", { version: version.id, status: version.conflict ? t("study.conflictDraftSuffix") : sameId(version.id, note.version_id) ? t("study.currentVersionSuffix") : '' })}
          </button>
        </li>
      ))}</ul>
      {readingVersion && <p role="status">{t("study.loadingVersion")}</p>}
      {revision && <section className="study-notebook-revision" aria-label={t("study.noteVersion", { version: revision.id })}>
        <h4>{revision.title}</h4>
        <p className="study-notebook-text">{revision.body}</p>
        {revision.sources && <Sources sources={revision.sources} noteId={id} />}
        {revision.conflict ? <button type="button" className="btn btn-outline" disabled={busy || reading}
          onClick={() => {
            if (dirty && !window.confirm(t("study.confirmReplaceWithConflictDraft"))) return;
            updateDraft({ base: note.version_id, text: revision, source: revision.id });
            setNotice("study.conflictDraftInEditor");
          }}>{t("study.useConflictDraft")}</button> : !sameId(revision.id, note.version_id) &&
          <button type="button" className="btn btn-outline" disabled={busy || reading}
            onClick={() => void generate(revision.id)}>{t("study.generateCardsFromVersion", { version: revision.id })}</button>}
      </section>}
      </details>
    </>}
  </section>
  {compared && <StudyCompare key={String(session)} sessionId={session} messageId={message} noteId={id} />}
  </div>
  </>;
}

export function StudyNotebook() {
  const t = useT();

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
        <h1>{review ? t("study.reviewAndPractice") : t("study.studyNotes")}</h1>
        <p>{review ? t("study.reviewAndPracticeDescription") : t("study.studyNotesDescription")}</p>
      </div></header>}
      {!id && !review && <section className="study-notebook-review-strip" aria-labelledby="study-review-strip-title">
        <div><h2 id="study-review-strip-title">{t("study.cardReview")}</h2><p>{dueCount === null ? t("study.cardReviewDescription") : dueCount ? t("study.cardsDue", { count: dueCount }) : t("study.noCardsDue")}</p></div>
        <Link className="btn btn-primary" to="/study/notes?tab=review">{t("study.startReview")}</Link>
      </section>}
      {!id && !review && <section className="study-notebook-panel study-notebook-paper" aria-label={t("study.notesList")}>
        <label className="study-notebook-field">{t("study.searchNotes")}
          <input className="inp" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={t("study.noteSearchPlaceholder")} />
        </label>
        {loading && <p role="status">{t("study.searchingNotes")}</p>}
        {error && <p role="alert">{t("study.noteListLoadFailed")}<button type="button" className="btn btn-outline btn-sm"
          onClick={() => setRefresh(value => value + 1)}>{t("study.retry")}</button></p>}
        {!loading && !error && !notes.length && <div className="study-notebook-empty">
          <p>{query.trim() ? t("study.noMatchingNotes") : t("study.noStudyNotes")}</p>
          {query.trim() ? <button type="button" className="btn btn-outline" onClick={() => setQuery('')}>{t("study.clearSearch")}</button>
            : <Link className="btn btn-outline" to="/study/chat">{t("study.goToStudyChat")}</Link>}
        </div>}
        <NotebookList notes={notes} renderLink={(note, content) => <Link to={notePath(note.id)}>{content}</Link>} />
      </section>}
      {id && <NoteEditor key={draftKey(`note:${id}`)} id={id} onSaved={() => setRefresh(value => value + 1)}
        onGenerated={cards => setGenerated(cards)} />}
    {review && <StudyCards generated={generated} />}
  </div></StudyShell>;
}

export default StudyNotebook;
