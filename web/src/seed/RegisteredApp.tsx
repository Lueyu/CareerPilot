import { lazy, useEffect, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { WorkspaceRoutes } from '../WorkspaceRoutes';
import { RegisteredWorkspaceLayout } from '../components/WorkspaceLayout';
import { AccountFrame } from './AccountFrame';
import AccountSettings from './AccountSettings';
import Dashboard from './Dashboard';
import { RegisteredOnboarding } from './RegisteredOnboarding';
import Memory from './Memory';
import RegisteredSession from './RegisteredSession';
import { type AccountInfo, useWorkspaceApi } from './api';
import type { LibraryRoles } from './libraryRole';
import './registered.css';

const Coach = lazy(() => import('./Coach'));
const Library = lazy(() => import('./Library'));
const Learning = lazy(() => import('./Learning'));
const StudyNotes = lazy(() => import('./StudyNotes'));

type Question = { id: number; title: string };
type Mode = 'study' | 'interview';
const previousLocations: Record<string, string> = { '#library': '/bank', '#coach': '/study/chat', '#memories': '/memory', '#settings': '/settings' };

function QuestionPractice({ mode, id }: { mode: Mode; id: string | null }) {
  const { request } = useWorkspaceApi();
  const navigate = useNavigate();
  const { sessionId } = useParams();
  const [params] = useSearchParams();
  const threadId = Number(mode === 'study' ? sessionId : params.get('thread')) || undefined;
  const [question, setQuestion] = useState<Question>();
  const [error, setError] = useState('');
  useEffect(() => {
    if (id === null) return;
    const controller = new AbortController();
    request<Question>('/library/questions/' + encodeURIComponent(id), { signal: controller.signal })
      .then(setQuestion)
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [request, id]);
  if (error) return <div className="card"><p role="alert">{error}</p><Link to="/bank">返回面试题库</Link></div>;
  if (id !== null && !question) return <p role="status">正在读取题目…</p>;
  return <Coach mode={mode} question={question} threadId={threadId} onLibrary={() => navigate('/bank')}
    onNotebook={() => navigate('/study/notes')} onNote={note => navigate('/study/notes/' + note)}
    onThreadChange={(next, replace = false) => {
      const query = new URLSearchParams();
      if (id) query.set('question', id);
      if (mode === 'interview') query.set('thread', String(next));
      navigate({ pathname: mode === 'study' ? '/study/chat/' + next : '/exam', search: query.toString() }, { replace });
    }} />;
}

function NotebookPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  return <StudyNotes noteId={Number(id) || undefined} onChat={() => navigate('/study/chat')} />;
}

function PracticePage({ mode }: { mode: Mode }) {
  const [params] = useSearchParams();
  const id = params.get('question');
  return <QuestionPractice key={mode + (id ?? '')} id={id} mode={mode} />;
}

function Workspace({ user, signedOut }: { user: AccountInfo; signedOut: () => void }) {
  const { request } = useWorkspaceApi();
  const location = useLocation();
  const navigate = useNavigate();
  const [ready, setReady] = useState<boolean>();
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    request<LibraryRoles>('/library/roles', { signal: controller.signal })
      .then(data => setReady(data.roles.some(role => role.role === data.selected_role && !!role.selected_topic)))
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [request, attempt]);

  if (error) return <AccountFrame><p role="alert">{error}</p><button className="btn btn-outline"
    onClick={() => setAttempt(n => n + 1)}>重新读取岗位</button></AccountFrame>;
  if (ready === undefined) return <AccountFrame><p role="status">正在读取工作空间…</p></AccountFrame>;
  if (location.pathname === '/' && previousLocations[location.hash]) return <Navigate replace to={previousLocations[location.hash]} />;
  if (!ready && location.pathname !== '/onboarding' && location.pathname !== '/settings') return <Navigate replace to="/onboarding" />;
  const onboarding = <AccountFrame><RegisteredOnboarding onComplete={() => {
    setReady(true);
    navigate('/', { replace: true });
  }} /></AccountFrame>;
  return <WorkspaceRoutes layout={<RegisteredWorkspaceLayout accountName={user.username} />} onboarding={onboarding}
    home={<Dashboard />} bank={<Library openImport={new URLSearchParams(location.search).get('import') === '1'} onCoach={() => navigate('/study/chat')}
      onPractice={(q, mode) => navigate((mode === 'interview' ? '/exam' : '/study/chat') + '?question=' + q.id)} />}
    chat={<PracticePage mode="study" />} notes={<NotebookPage />} exam={<PracticePage mode="interview" />} learning={<Learning />}
    memory={<Memory />} settings={<AccountSettings user={user} signedOut={() => { signedOut(); navigate('/', { replace: true }); }} />} />;
}

export default function RegisteredApp() {
  return <RegisteredSession>{(user, signedOut) => <Workspace user={user} signedOut={signedOut} />}</RegisteredSession>;
}
