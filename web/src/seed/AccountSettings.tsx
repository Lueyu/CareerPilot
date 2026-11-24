import { useT } from '../i18n';
import { useState } from 'react';
import { messageFor, renderMessage, useWorkspaceApi, type AccountInfo, type UiMessage } from './api';

export default function AccountSettings({ user, signedOut }: { user: AccountInfo; signedOut: () => void }) {
  const t = useT();
  const { request } = useWorkspaceApi();
  const [password, setPassword] = useState('');
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [changed, setChanged] = useState(false);
  const [error, setError] = useState<UiMessage | null>(null);
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try { await action(); } catch (error) { setError(messageFor(error, 'registered.actionFailed')); } finally { setBusy(false); }
  }
  async function download() {
    const data = await request<unknown>('/export');
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = t('registered.accountExportFilename');
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  }
  return <section className="account-settings"><h1 className="page-title">{t('registered.accountAndData')}</h1><div className="card account-section"><h2>{t('registered.currentAccount')}</h2><p>{user.username}</p>
    <div className="account-actions"><button className="btn btn-outline" onClick={() => perform(download)} disabled={busy}>{t('registered.exportAccountData')}</button>
      <button className="btn btn-outline" onClick={() => perform(async () => { await request('/auth/logout', { method: 'POST' }); signedOut(); })} disabled={busy}>{t('registered.logout')}</button></div>
    </div><div className="card account-section">
      <h2>{t('registered.changePassword')}</h2>
      <form className="account-form" onSubmit={e => { e.preventDefault(); void perform(async () => {
        await request('/auth/password', { method: 'POST', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
        setCurrentPassword(''); setNewPassword(''); setChanged(true);
      }); }}>
        <label>{t('registered.currentPassword')}<input type="password" autoComplete="current-password" value={currentPassword} required maxLength={128} onChange={e => { setCurrentPassword(e.target.value); setChanged(false); }} /></label>
        <label>{t('registered.newPasswordLabel')}<input type="password" autoComplete="new-password" value={newPassword} required minLength={12} maxLength={128} onChange={e => { setNewPassword(e.target.value); setChanged(false); }} /><small className="account-input-hint">{t('registered.passwordHint')}</small></label>
        <button className="btn btn-primary" disabled={busy || !currentPassword || newPassword.length < 12}>{t('registered.changePassword')}</button>
        {changed && <p role="status">{t('registered.passwordChanged')}</p>}
      </form>
    </div><details className="card account-section" open={deleting} onToggle={e => setDeleting(e.currentTarget.open)}>
      <summary>{t('registered.deleteAccount')}</summary><p>{t('registered.deleteAccountWarning')}</p>
      <form className="account-form" onSubmit={e => { e.preventDefault(); void perform(async () => {
        await request('/auth/account', { method: 'DELETE', body: JSON.stringify({ password }) }); signedOut();
      }); }}>
        <label>{t('registered.confirmPasswordToDeleteAccount')}<input type="password" autoComplete="current-password" value={password} required onChange={e => setPassword(e.target.value)} /></label>
        <button className="btn btn-outline account-danger" disabled={busy || !password}>{t('registered.permanentlyDeleteAccount')}</button>
      </form></details>
    {error && <p role="alert" className="error">{renderMessage(error, t)}</p>}</section>;
}
