import { lazy, useEffect, useLayoutEffect, useState, type ReactElement } from 'react';
import { useT } from '../i18n';
import { Link, Navigate, Route, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { NativeWorkspace } from '../App';
import { api, ApiError, bindRegisteredNativeApi } from '../api/client';
import StudyChat from '../pages/StudyChat';
import StudyNotebook from '../pages/StudyNotebook';
import ExamCenter from '../pages/ExamCenter';
import { AccountFrame } from './AccountFrame';
import AccountSettings from './AccountSettings';
import Dashboard from './Dashboard';
import { RegisteredOnboarding } from './RegisteredOnboarding';
import Memory from './Memory';
import RegisteredSession from './RegisteredSession';
import { messageFor, renderMessage, useWorkspaceApi, type AccountInfo, type UiMessage } from './api';
import type { LibraryRoles } from './libraryRole';
import './registered.css';

const Coach = lazy(() => import('./Coach'));
const Library = lazy(() => import('./Library'));
const Learning = lazy(() => import('./Learning'));
const StudyNotes = lazy(() => import('./StudyNotes'));

type Question = { id: number; title: string };
type Mode = 'study' | 'interview';
const previousLocations: Record<string, string> = {
  '#library': '/beta/bank', '#coach': '/beta/study/chat', '#memories': '/beta/memory', '#settings': '/settings/account',
};

function QuestionPractice({ mode, id }: { mode: Mode; id: string | null }) {
  const t = useT();
  const { request } = useWorkspaceApi();
  const navigate = useNavigate();
  const { sessionId } = useParams();
  const [params] = useSearchParams();
  const threadId = Number(mode === 'study' ? sessionId : params.get('thread')) || undefined;
  const [question, setQuestion] = useState<Question>();
  const [error, setError] = useState<UiMessage | null>(null);
  useEffect(() => {
    if (id === null) return;
    const controller = new AbortController();
    request<Question>('/library/questions/' + encodeURIComponent(id), { signal: controller.signal })
      .then(setQuestion)
      .catch(e => { if (!controller.signal.aborted) setError(messageFor(e, 'registered.requestIncomplete')); });
    return () => controller.abort();
  }, [request, id]);
  if (error) return <div className="card"><p role="alert">{renderMessage(error, t)}</p><Link to="/beta/bank">{t('registered.returnToQuestionBank')}</Link></div>;
  if (id !== null && !question) return <p role="status">{t('registered.questionLoading')}</p>;
  return <Coach mode={mode} question={question} threadId={threadId} onLibrary={() => navigate('/beta/bank')}
    onNotebook={() => navigate('/beta/study/notes')} onNote={note => navigate('/beta/study/notes/' + note)}
    onThreadChange={(next, replace = false) => {
      const query = new URLSearchParams();
      if (id) query.set('question', id);
      if (mode === 'interview') query.set('thread', String(next));
      navigate({ pathname: mode === 'study' ? '/beta/study/chat/' + next : '/beta/exam', search: query.toString() }, { replace });
    }} />;
}

function NotebookPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  return <StudyNotes noteId={Number(id) || undefined} onChat={() => navigate('/beta/study/chat')} />;
}

function PracticePage({ mode }: { mode: Mode }) {
  const [params] = useSearchParams();
  const id = params.get('question');
  return <QuestionPractice key={mode + (id ?? '')} id={id} mode={mode} />;
}

function CompatibleRecord({ kind, native, beta }: {
  kind: 'sessions' | 'notes'; native: ReactElement; beta: ReactElement;
}) {
  const params = useParams();
  const id = kind === 'sessions' ? params.sessionId : params.id;
  if (!id) return native;
  return <RecordLookup key={kind + id} path={'/study/' + kind + '/' + encodeURIComponent(id)}
    native={native} beta={beta} />;
}

function RecordLookup({ path, native, beta }: { path: string; native: ReactElement; beta: ReactElement }) {
  const t = useT();
  const [legacy, setLegacy] = useState<boolean>();
  useEffect(() => {
    let current = true;
    api.get(path).then(() => { if (current) setLegacy(false); }).catch(error => {
      if (current) setLegacy(error instanceof ApiError && error.status === 404);
    });
    return () => { current = false; };
  }, [path]);
  if (legacy === undefined) return <p role="status">{t('registered.recordLoading')}</p>;
  return legacy ? beta : native;
}

function RegisteredChat() {
  const { sessionId } = useParams();
  const { search } = useLocation();
  if (new URLSearchParams(search).has('question')) {
    return <Navigate replace to={'/beta/study/chat' + (sessionId ? '/' + sessionId : '') + search} />;
  }
  return <CompatibleRecord kind="sessions" native={<StudyChat questionBuilderPath="/beta/study/chat" />} beta={<PracticePage mode="study" />} />;
}

function RegisteredExam() {
  const { search } = useLocation();
  const query = new URLSearchParams(search);
  return query.has('thread') || query.has('question') ? <Navigate replace to={'/beta/exam' + search} /> : <ExamCenter />;
}

function Workspace({ user, signedOut }: { user: AccountInfo; signedOut: () => void }) {
  const t = useT();
  const { request, response, signal } = useWorkspaceApi();
  const location = useLocation();
  const navigate = useNavigate();
  const [access, setAccess] = useState<{ personal_owner: boolean; ready: boolean }>();
  const [bound, setBound] = useState(false);
  const [error, setError] = useState<UiMessage | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    async function open() {
      const { personal_owner } = await request<{ personal_owner: boolean }>('/native/access', { signal: controller.signal });
      if (typeof personal_owner !== 'boolean') {
        if (!controller.signal.aborted) setError({ key: 'registered.workspaceAccessUnknown' });
        return;
      }
      const roles = personal_owner ? undefined : await request<LibraryRoles>('/library/roles', { signal: controller.signal });
      if (!controller.signal.aborted) setAccess({
        personal_owner,
        ready: personal_owner || !!roles?.roles.some(role => role.role === roles.selected_role && !!role.selected_topic),
      });
    }
    void open().catch(e => { if (!controller.signal.aborted) setError(messageFor(e, 'registered.requestIncomplete')); });
    return () => controller.abort();
  }, [request, attempt]);

  const personalOwner = access?.personal_owner;
  useLayoutEffect(() => {
    if (personalOwner === undefined) return;
    const release = bindRegisteredNativeApi({ workspaceId: user.workspace_id, personalOwner, response, signal });
    setBound(true);
    return release;
  }, [personalOwner, response, signal, user.workspace_id]);

  if (error) return <AccountFrame><p role="alert">{renderMessage(error, t)}</p><button className="btn btn-outline"
    onClick={() => setAttempt(n => n + 1)}>{t('registered.rereadWorkspace')}</button></AccountFrame>;
  if (!access || !bound) return <AccountFrame><p role="status">{t('registered.workspaceReading')}</p></AccountFrame>;
  if (!access.ready && !['/onboarding', '/settings/account', '/settings'].includes(location.pathname)) {
    return <Navigate replace to="/onboarding" state={{ returnTo: location.pathname + location.search + location.hash }} />;
  }
  if (location.pathname === '/' && previousLocations[location.hash]) return <Navigate replace to={previousLocations[location.hash]} />;
  if (location.pathname === '/onboarding' && access.personal_owner) return <Navigate replace to="/" />;
  const onboarding = <AccountFrame><RegisteredOnboarding onComplete={() => {
    setAccess({ ...access, ready: true });
    navigate(location.state?.returnTo ?? '/', { replace: true });
  }} /></AccountFrame>;
  const account = <><AccountSettings user={user} signedOut={() => { signedOut(); navigate('/', { replace: true }); }} />
    <Link to="/settings">{t('registered.workspaceSettings')}</Link></>;
  return <NativeWorkspace registered={{
    accountName: user.username,
    onboarding,
    chat: <RegisteredChat />,
    notes: <CompatibleRecord kind="notes" native={<StudyNotebook />} beta={<NotebookPage />} />,
    exam: <RegisteredExam />,
    routes: <>
      <Route path="/settings/account" element={account} />
      <Route path="/beta" element={<Dashboard />} />
      <Route path="/beta/bank" element={<Library openImport={new URLSearchParams(location.search).get('import') === '1'}
        onCoach={() => navigate('/beta/study/chat')}
        onPractice={(q, mode) => navigate((mode === 'interview' ? '/beta/exam' : '/beta/study/chat') + '?question=' + q.id)} />} />
      <Route path="/beta/study/chat/:sessionId?" element={<PracticePage mode="study" />} />
      <Route path="/beta/study/notes/:id?" element={<NotebookPage />} />
      <Route path="/beta/exam" element={<PracticePage mode="interview" />} />
      <Route path="/beta/learning" element={<Learning />} />
      <Route path="/beta/memory" element={<Memory />} />
    </>,
  }} />;
}

export default function RegisteredApp() {
  return <RegisteredSession>{(user, signedOut) => <Workspace user={user} signedOut={signedOut} />}</RegisteredSession>;
}
