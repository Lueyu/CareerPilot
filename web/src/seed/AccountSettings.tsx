import { useState } from 'react';
import { useWorkspaceApi, type AccountInfo } from './api';

export default function AccountSettings({ user, signedOut }: { user: AccountInfo; signedOut: () => void }) {
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
  return <section className="account-settings"><h1 className="page-title">账户与资料</h1><div className="card account-section"><h2>当前账号</h2><p>{user.username}</p>
    <div className="account-actions"><button className="btn btn-outline" onClick={() => perform(download)} disabled={busy}>导出我的资料</button>
      <button className="btn btn-outline" onClick={() => perform(async () => { await request('/auth/logout', { method: 'POST' }); signedOut(); })} disabled={busy}>退出登录</button></div>
    </div><details className="card account-section" open={deleting} onToggle={e => setDeleting(e.currentTarget.open)}>
      <summary>删除账户</summary><p>删除后，你将无法找回这个账户的题目、对话和项目记忆。可以先导出资料。</p>
      <form className="account-form" onSubmit={e => { e.preventDefault(); void perform(async () => {
        await request('/auth/account', { method: 'DELETE', body: JSON.stringify({ password }) }); signedOut();
      }); }}>
        <label>输入当前密码确认删除<input type="password" autoComplete="current-password" value={password} required onChange={e => setPassword(e.target.value)} /></label>
        <button className="btn btn-outline account-danger" disabled={busy || !password}>永久删除我的账户</button>
      </form></details>
    {error && <p role="alert" className="error">{error}</p>}</section>;
}

