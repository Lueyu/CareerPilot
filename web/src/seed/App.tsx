import { useCallback, useEffect, useState } from 'react';
import Account from './Account';
import Coach from './Coach';
import Library from './Library';
import Memory from './Memory';
import { request, SeedApiError } from './api';

const pages = { library: '我的题库', coach: '练习室', memories: '项目记忆', settings: '账户' } as const;
type Page = keyof typeof pages;
type User = { username: string; workspace_id: number };
type Practice = { question: { id: number; title: string }; mode: 'study' | 'interview' };

function currentPage(): Page {
  const key = window.location.hash.slice(1);
  return Object.prototype.hasOwnProperty.call(pages, key) ? key as Page : 'library';
}

function Settings({ user, signedOut }: { user: User; signedOut: () => void }) {
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
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState('');
  const [page, setPage] = useState<Page>(currentPage);
  const [practice, setPractice] = useState<Practice>();
  const [coachKey, setCoachKey] = useState(0);
  const loadUser = useCallback(async () => {
    setError('');
    try { setUser(await request<User>('/auth/me')); }
    catch (e) { setUser(null); if (!(e instanceof SeedApiError && e.status === 401)) setError((e as Error).message); }
    finally { setBooting(false); }
  }, []);
  function signedOut() {
    setUser(null);
    setPractice(undefined);
    window.location.hash = 'library';
  }
  useEffect(() => {
    void loadUser();
    const change = () => setPage(currentPage());
    window.addEventListener('hashchange', change);
    window.addEventListener('careerpilot-session-expired', signedOut);
    return () => { window.removeEventListener('hashchange', change); window.removeEventListener('careerpilot-session-expired', signedOut); };
  }, [loadUser]);
  function openCoach(next?: Practice) {
    setPractice(next);
    setCoachKey(k => k + 1);
    window.location.hash = 'coach';
  }
  return <><header className="mast"><div className="mast-inner">
    <a className="brand" href="#library" aria-label="CareerPilot 首页"><span className="brand-mark" aria-hidden="true">C</span>CareerPilot</a>
    {user && <nav aria-label="主导航">{(Object.entries(pages) as [Page,string][]).map(([key,label]) =>
      <a key={key} href={'#' + key} aria-current={page === key ? 'page' : undefined}>{label}</a>)}</nav>}
  </div></header>
  <main className={user ? 'workspace' : 'welcome'}>
    {booting ? <p role="status">正在打开练习室…</p> : error ? <div className="panel stack"><p role="alert">{error}</p><button onClick={loadUser}>重新连接</button></div>
    : !user ? <><div className="intro"><h1>把下一场面试，<br/>提前练一遍。</h1><p>带上你的目标岗位和面经，整理自己的题库。和学习助手一起拆解问题，再让面试官沿着你的回答追问。</p></div><section className="login-panel"><Account onAuthenticated={loadUser} /></section></>
    : <div key={user.workspace_id}>
        {page === 'library' && <Library onPractice={(question,mode) => openCoach({question,mode})} onCoach={() => openCoach()} />}
        {page === 'coach' && <Coach key={coachKey} question={practice?.question} mode={practice?.mode || 'study'} onLibrary={() => { window.location.hash = 'library'; }} />}
        {page === 'memories' && <Memory />}
        {page === 'settings' && <Settings user={user} signedOut={signedOut} />}
      </div>}
  </main></>;
}
