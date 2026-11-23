import { useCallback, useEffect, useRef, useState } from 'react';
import Account from './Account';
import Coach from './Coach';
import Library from './Library';
import Memory from './Memory';
import {
  announceSessionChange, createWorkspaceApi, requestAuth, SeedApiError, sessionChangeKey,
  useWorkspaceApi, WorkspaceApiContext, type AccountInfo,
} from './api';

const pages = { library: '我的题库', coach: '练习室', memories: '项目记忆', settings: '账户' } as const;
type Page = keyof typeof pages;
type Practice = { question: { id: number; title: string }; mode: 'study' | 'interview' };
type WorkspaceView = { kind: 'workspace'; user: AccountInfo; api: ReturnType<typeof createWorkspaceApi>; key: number };
type SessionView = WorkspaceView | { kind: 'opening' } | { kind: 'anonymous' } | { kind: 'error'; message: string };

function currentPage(): Page {
  const key = window.location.hash.slice(1);
  return Object.prototype.hasOwnProperty.call(pages, key) ? key as Page : 'library';
}

function Settings({ user, signedOut }: { user: AccountInfo; signedOut: () => void }) {
  const { request } = useWorkspaceApi();
  const [password, setPassword] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try { await action(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function download() {
    const data = await request<unknown>('/export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'CareerPilot-个人资料.json';
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
  return <section className="panel stack"><h1>账户与资料</h1><p>当前用户 <strong>{user.username}</strong></p>
    <div className="actions"><button onClick={() => perform(download)} disabled={busy}>导出我的资料</button>
      <button onClick={() => perform(async () => { await request('/auth/logout', { method: 'POST' }); signedOut(); })} disabled={busy}>退出登录</button></div>
    <hr/><details open={deleting} onToggle={e => setDeleting(e.currentTarget.open)}>
      <summary>删除账户</summary><p>删除后，你将无法找回这个账户的题目、对话和项目记忆。可以先导出资料。</p>
      <form className="stack" onSubmit={e => { e.preventDefault(); void perform(async () => {
        await request('/auth/account', { method: 'DELETE', body: JSON.stringify({ password }) }); signedOut();
      }); }}>
        <label>输入当前密码确认删除<input type="password" autoComplete="current-password" value={password} required onChange={e => setPassword(e.target.value)} /></label>
        <button className="danger" disabled={busy || !password}>永久删除我的账户</button>
      </form></details>
    {error && <p role="alert" className="error">{error}</p>}</section>;
}

export default function App() {
  const [session, setSession] = useState<SessionView>({ kind: 'opening' });
  const workspace = useRef<WorkspaceView | null>(null);
  const workspaceKey = useRef(0);
  const sessionCheck = useRef<AbortController | null>(null);
  const accountFlow = useRef(false);
  const [page, setPage] = useState<Page>(currentPage);
  const [practice, setPractice] = useState<Practice>();
  const [coachKey, setCoachKey] = useState(0);
  const clearWorkspace = useCallback(() => {
    if (!workspace.current) return;
    workspace.current.api.close();
    workspace.current = null;
    setPractice(undefined);
  }, []);

  const loadUser = useCallback(async (reset = false): Promise<void> => {
    if (accountFlow.current) return;
    sessionCheck.current?.abort();
    const check = new AbortController();
    sessionCheck.current = check;
    if (reset) {
      clearWorkspace();
      setSession({ kind: 'opening' });
    }
    try {
      const user = await requestAuth<AccountInfo>('/auth/me', { signal: check.signal });
      if (check.signal.aborted || workspace.current?.user.workspace_id === user.workspace_id) return;
      clearWorkspace();
      const api = createWorkspaceApi(user.workspace_id, () => void loadUser(true));
      const next: WorkspaceView = { kind: 'workspace', user, api, key: ++workspaceKey.current };
      workspace.current = next;
      setSession(next);
    } catch (error) {
      if (check.signal.aborted) return;
      if (error instanceof SeedApiError && error.status === 401) {
        clearWorkspace();
        setSession({ kind: 'anonymous' });
      } else if (!workspace.current) {
        setSession({ kind: 'error', message: error instanceof Error ? error.message : '暂时无法连接账号服务。' });
      }
    } finally {
      if (sessionCheck.current === check) sessionCheck.current = null;
    }
  }, [clearWorkspace]);

  function startAccountFlow() {
    accountFlow.current = true;
    sessionCheck.current?.abort();
  }
  function finishAccountFlow() {
    accountFlow.current = false;
    void loadUser();
  }
  function signedOut() {
    announceSessionChange();
    window.location.hash = 'library';
    void loadUser(true);
  }
  useEffect(() => {
    void loadUser();
    const change = () => setPage(currentPage());
    const check = () => void loadUser();
    const visible = () => { if (document.visibilityState === 'visible') check(); };
    const changedInOtherTab = (event: StorageEvent) => { if (event.key === sessionChangeKey) check(); };
    window.addEventListener('hashchange', change);
    window.addEventListener('focus', check);
    window.addEventListener('pageshow', check);
    window.addEventListener('storage', changedInOtherTab);
    document.addEventListener('visibilitychange', visible);
    return () => {
      sessionCheck.current?.abort();
      workspace.current?.api.close();
      workspace.current = null;
      window.removeEventListener('hashchange', change);
      window.removeEventListener('focus', check);
      window.removeEventListener('pageshow', check);
      window.removeEventListener('storage', changedInOtherTab);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [loadUser]);
  function openCoach(next?: Practice) {
    setPractice(next);
    setCoachKey(k => k + 1);
    window.location.hash = 'coach';
  }
  const user = session.kind === 'workspace' ? session.user : null;
  return <><header className="mast"><div className="mast-inner">
    <a className="brand" href="#library" aria-label="CareerPilot 首页"><span className="brand-mark" aria-hidden="true">C</span>CareerPilot</a>
    {user && <nav aria-label="主导航">{(Object.entries(pages) as [Page,string][]).map(([key,label]) =>
      <a key={key} href={'#' + key} aria-current={page === key ? 'page' : undefined}>{label}</a>)}</nav>}
  </div></header>
  <main className={user ? 'workspace' : 'welcome'}>
    {session.kind === 'opening' ? <p role="status">正在打开练习室…</p> : session.kind === 'error' ? <div className="panel stack"><p role="alert">{session.message}</p><button onClick={() => void loadUser(true)}>重新连接</button></div>
    : session.kind === 'anonymous' ? <><div className="intro"><h1>把下一场面试，<br/>提前练一遍。</h1><p>带上你的目标岗位和面经，整理自己的题库。和学习助手一起拆解问题，再让面试官沿着你的回答追问。</p></div><section className="login-panel"><Account onStart={startAccountFlow} onFinish={finishAccountFlow} /></section></>
    : <WorkspaceApiContext.Provider key={session.key} value={session.api}><div>
        {page === 'library' && <Library onPractice={(question,mode) => openCoach({question,mode})} onCoach={() => openCoach()} />}
        {page === 'coach' && <Coach key={coachKey} question={practice?.question} mode={practice?.mode || 'study'} onLibrary={() => { window.location.hash = 'library'; }} />}
        {page === 'memories' && <Memory />}
        {page === 'settings' && <Settings user={session.user} signedOut={signedOut} />}
      </div></WorkspaceApiContext.Provider>}
  </main></>;
}
