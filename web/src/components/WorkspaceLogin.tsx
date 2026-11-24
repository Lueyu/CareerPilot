import { useT } from '../i18n';
import { useState, type FormEvent, type ReactNode } from 'react';
import { api, workspaceLogin } from '../api/client';
import LanguageSelector from './LanguageSelector';

export default function WorkspaceLogin({ children }: { children: ReactNode }) {
  const t = useT();
  const [ready, setReady] = useState(() => !workspaceLogin || !!sessionStorage.getItem('cp-login-token'));
  const [slug, setSlug] = useState('');
  const [token, setToken] = useState('');
  const [failure, setFailure] = useState<{ detail?: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function clear() {
    sessionStorage.removeItem('cp-login-workspace');
    sessionStorage.removeItem('cp-login-token');
  }

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    sessionStorage.setItem('cp-login-workspace', slug.trim());
    sessionStorage.setItem('cp-login-token', token.trim());
    try {
      await api.get('/settings/overview');
      setToken('');
      setReady(true);
    } catch (error) {
      clear();
      setFailure({ detail: error instanceof Error ? error.message : undefined });
    } finally {
      setBusy(false);
    }
  }

  if (ready) return <>
    {workspaceLogin && <button className="btn btn-outline" onClick={() => {
      clear();
      setReady(false);
    }}>{t('shell.signOutWorkspace')}</button>}
    {children}
  </>;

  return (
    <form className="card" style={{ maxWidth: 420, margin: '12vh auto', display: 'grid', gap: 12 }} onSubmit={signIn}>
      <div className="workspace-login-heading"><h2>{t('shell.workspaceSignInTitle')}</h2><LanguageSelector /></div>
      <label>{t('shell.workspace')} <input required value={slug} onChange={e => setSlug(e.target.value)} /></label>
      <label>{t('shell.personalAccessToken')} <input type="password" required value={token} onChange={e => setToken(e.target.value)} /></label>
      {failure && <p role="alert">{failure.detail || t('shell.workspaceSignInFailed')}</p>}
      <button disabled={busy} className="btn btn-primary">{t('shell.signInWorkspace')}</button>
    </form>
  );
}
