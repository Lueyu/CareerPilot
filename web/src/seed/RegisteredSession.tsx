import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useT } from '../i18n';
import Account from './Account';
import {
  announceSessionChange, createWorkspaceApi, messageFor, renderMessage, requestAuth, SeedApiError, sessionChangeKey,
  WorkspaceApiContext, type AccountInfo, type UiMessage,
} from './api';
import { AccountFrame } from './AccountFrame';

type WorkspaceView = { kind: 'workspace'; user: AccountInfo; api: ReturnType<typeof createWorkspaceApi>; key: number };
type SessionView = WorkspaceView | { kind: 'opening' } | { kind: 'anonymous' } | { kind: 'error'; message: UiMessage };

export default function RegisteredSession({ children }: { children: (user: AccountInfo, signedOut: () => void) => ReactNode }) {
  const t = useT();
  const [session, setSession] = useState<SessionView>({ kind: 'opening' });
  const workspace = useRef<WorkspaceView | null>(null);
  const workspaceKey = useRef(0);
  const sessionCheck = useRef<AbortController | null>(null);
  const accountFlow = useRef(false);
  const clearWorkspace = useCallback(() => {
    if (!workspace.current) return;
    workspace.current.api.close();
    workspace.current = null;
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
        setSession({ kind: 'error', message: messageFor(error, 'registered.accountConnectionUnavailable') });
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
    void loadUser(true);
  }
  useEffect(() => {
    void loadUser();
    const check = () => void loadUser();
    const visible = () => { if (document.visibilityState === 'visible') check(); };
    const changedInOtherTab = (event: StorageEvent) => { if (event.key === sessionChangeKey) check(); };
    window.addEventListener('focus', check);
    window.addEventListener('pageshow', check);
    window.addEventListener('storage', changedInOtherTab);
    document.addEventListener('visibilitychange', visible);
    return () => {
      sessionCheck.current?.abort();
      workspace.current?.api.close();
      workspace.current = null;
      window.removeEventListener('focus', check);
      window.removeEventListener('pageshow', check);
      window.removeEventListener('storage', changedInOtherTab);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [loadUser]);
  if (session.kind === 'workspace') {
    return <WorkspaceApiContext.Provider key={session.key} value={session.api}>
      {children(session.user, signedOut)}
    </WorkspaceApiContext.Provider>;
  }
  return <AccountFrame>
    {session.kind === 'opening' ? <p role="status">{t('registered.workspaceOpening')}</p>
      : session.kind === 'error' ? <div className="card account-form"><p role="alert">{renderMessage(session.message, t)}</p>
        <button className="btn btn-outline" onClick={() => void loadUser(true)}>{t('registered.reconnect')}</button></div>
      : <Account onStart={startAccountFlow} onFinish={finishAccountFlow} />}
  </AccountFrame>;
}
