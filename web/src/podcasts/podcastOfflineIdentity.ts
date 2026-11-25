const legacySessionKey = 'careerpilot-podcast-device-session';
const legacyAccountKey = 'careerpilot-podcast-device-account';
const registeredSessionKey = 'careerpilot-public-session-change';
const ticketKey = 'careerpilot-podcast-offline-ticket';
const identityEvent = 'careerpilot-podcast-session-change';

export type PodcastIdentity = {
  workspace: string;
  mode: 'local' | 'workspace-login' | 'registered';
  epoch: string;
  account: string;
};

function storageItem(kind: 'localStorage' | 'sessionStorage', key: string): string | null {
  try {
    return globalThis[kind]?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function podcastIdentity(workspace: string, workspaceLogin: boolean): PodcastIdentity {
  const mode = workspace.startsWith('registered:') ? 'registered' : workspaceLogin ? 'workspace-login' : 'local';
  return {
    workspace, mode, epoch: sessionEpoch(mode),
    account: mode === 'workspace-login' ? storageItem('sessionStorage', legacyAccountKey) ?? '' : '',
  };
}

function sessionEpoch(mode: PodcastIdentity['mode']) {
  if (mode === 'local') return '';
  return storageItem('localStorage', mode === 'registered' ? registeredSessionKey : legacySessionKey) ?? '';
}

export function podcastStorageScope(identity: PodcastIdentity) {
  return JSON.stringify([identity.mode, identity.workspace, identity.account]);
}

export function identityIsCurrent(identity: PodcastIdentity) {
  if (!identity.workspace || identity.workspace === 'registered:unbound') return false;
  if (sessionEpoch(identity.mode) !== identity.epoch) return false;
  if (identity.mode === 'local') {
    const selected = storageItem('localStorage', 'cp-workspace');
    if (selected && selected !== identity.workspace) return false;
  }
  if (identity.mode === 'workspace-login') {
    if (globalThis.isSecureContext && (!identity.account || storageItem('localStorage', legacyAccountKey) !== identity.account)) return false;
    const account = storageItem('sessionStorage', legacyAccountKey);
    if (account && account !== identity.account) return false;
    const selected = storageItem('sessionStorage', 'cp-login-workspace');
    if (selected && selected !== identity.workspace) return false;
  }
  return true;
}

export function podcastDeviceLoginState() {
  const account = storageItem('sessionStorage', legacyAccountKey);
  if (!account) return 'uninitialized';
  return account === storageItem('localStorage', legacyAccountKey) ? 'active' : 'closed';
}

export function invalidatePodcastDeviceSession() {
  try {
    sessionStorage.removeItem(legacyAccountKey);
    localStorage.removeItem(legacyAccountKey);
    localStorage.removeItem(legacySessionKey);
  } finally {
    window.dispatchEvent(new Event(identityEvent));
  }
}

export async function recordPodcastDeviceLogin(token: string) {
  if (!crypto.subtle) return;
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token));
  const account = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
  const epoch = crypto.randomUUID();
  sessionStorage.setItem(legacyAccountKey, account);
  localStorage.setItem(legacyAccountKey, account);
  localStorage.setItem(legacySessionKey, epoch);
  window.dispatchEvent(new Event(identityEvent));
}

export function watchPodcastIdentity(onChange: () => void) {
  const keys = [legacySessionKey, legacyAccountKey, registeredSessionKey, 'cp-workspace', 'cp-login-workspace', ticketKey];
  const storage = (event: StorageEvent) => {
    if (event.key === null || keys.includes(event.key)) onChange();
  };
  window.addEventListener('storage', storage);
  window.addEventListener(identityEvent, onChange);
  window.addEventListener('pageshow', onChange);
  return () => {
    window.removeEventListener('storage', storage);
    window.removeEventListener(identityEvent, onChange);
    window.removeEventListener('pageshow', onChange);
  };
}

export function rememberOfflineIdentity(identity: PodcastIdentity) {
  if (!identityIsCurrent(identity)) throw new Error('工作区已切换，请重新打开节目库');
  localStorage.setItem(ticketKey, JSON.stringify(identity));
}

export function readOfflineIdentity(): PodcastIdentity | null {
  try {
    const identity: unknown = JSON.parse(storageItem('localStorage', ticketKey) ?? 'null');
    if (!identity || typeof identity !== 'object' || !('workspace' in identity) ||
        !('mode' in identity) || !('epoch' in identity) || !('account' in identity) ||
        typeof identity.workspace !== 'string' || typeof identity.account !== 'string' ||
        typeof identity.epoch !== 'string' ||
        (identity.mode !== 'local' && identity.mode !== 'workspace-login' && identity.mode !== 'registered')) return null;
    const valid: PodcastIdentity = { workspace: identity.workspace, mode: identity.mode, epoch: identity.epoch, account: identity.account };
    return identityIsCurrent(valid) ? valid : null;
  } catch {
    return null;
  }
}

export async function verifyOfflineIdentity(identity: PodcastIdentity, signal: AbortSignal) {
  if (identity.mode === 'registered') {
    const response = await fetch('/api/v1/auth/me', { cache: 'no-store', signal }).catch(error => {
      signal.throwIfAborted();
      if (error instanceof TypeError) return null;
      throw error;
    });
    if (response) {
      if (!response.ok) return false;
      const account: { workspace_id?: number } = await response.json();
      if (identity.workspace !== 'registered:' + account.workspace_id) return false;
    }
  }
  signal.throwIfAborted();
  return identityIsCurrent(identity);
}
