import { useT } from '../i18n';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { api, workspaceLogin } from '../api/client';
import LanguageSelector from './LanguageSelector';
import { invalidatePodcastDeviceSession, podcastDeviceLoginState, recordPodcastDeviceLogin } from '../podcasts/podcastOfflineIdentity';

export default function WorkspaceLogin({ children }: { children: ReactNode }) {
  const t = useT();
  const [ready, setReady] = useState(() => !workspaceLogin ||
    (!!sessionStorage.getItem('cp-login-token') && (!isSecureContext || podcastDeviceLoginState() === 'active')));
  const [restoring, setRestoring] = useState(() => !ready && !!sessionStorage.getItem('cp-login-token') &&
    podcastDeviceLoginState() === 'uninitialized');
  const [slug, setSlug] = useState('');
  const [token, setToken] = useState('');
  const [failure, setFailure] = useState<{ detail?: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function clear() {
    sessionStorage.removeItem('cp-login-workspace');
    sessionStorage.removeItem('cp-login-token');
  }

  useEffect(() => {
    if (!restoring) return;
    const previousToken = sessionStorage.getItem('cp-login-token');
    if (!previousToken) {
      setRestoring(false);
      return;
    }
    let active = true;
    void api.get('/settings/overview').then(async () => {
      if (!active) return;
      await recordPodcastDeviceLogin(previousToken);
      if (active) setReady(true);
    }).catch(() => {}).finally(() => {
      if (active) setRestoring(false);
    });
    return () => { active = false; };
  }, [restoring]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFailure(null);
    sessionStorage.setItem('cp-login-workspace', slug.trim());
    sessionStorage.setItem('cp-login-token', token.trim());
    try {
      await api.get('/settings/overview');
      await recordPodcastDeviceLogin(token.trim()).catch(() => undefined);
      setToken('');
      setReady(true);
    } catch (error) {
      clear();
      setFailure({ detail: error instanceof Error ? error.message : undefined });
    } finally {
      setBusy(false);
    }
  }

  if (restoring) return <p role="status">{t('registered.workspaceOpening')}</p>;
  if (ready) return <>
    {workspaceLogin && <button className="btn btn-outline" onClick={() => {
      clear();
      try { invalidatePodcastDeviceSession(); } finally { setReady(false); }
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
