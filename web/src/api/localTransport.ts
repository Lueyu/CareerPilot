const env = (import.meta as any).env;
const localApi = env?.DEV && typeof window !== 'undefined' && env['VITE_DEV_API_' + window.location.port];
const base = localApi || env?.VITE_API_BASE || 'http://127.0.0.1:8000/api/v1';
const token = env?.VITE_API_TOKEN || 'cp-dev-token';
export const workspaceLogin = env?.VITE_AUTH_MODE === 'workspace';

function storedWorkspace(): string {
  return globalThis.localStorage?.getItem('cp-workspace') || env?.VITE_WORKSPACE || 'personal';
}

// Workspace selection takes effect on navigation. Until then this document
// must keep sending requests for the workspace it rendered.
const browserWorkspace = typeof window !== 'undefined' && !workspaceLogin ? storedWorkspace() : undefined;

export function localWorkspace(): string {
  if (workspaceLogin) return sessionStorage.getItem('cp-login-workspace') || '';
  return browserWorkspace ?? storedWorkspace();
}

export function selectLocalWorkspace(slug: string) {
  localStorage.setItem('cp-workspace', slug);
}

export function localFetch(path: string, options: RequestInit) {
  return fetch(base + path, {
    ...options,
    headers: {
      'X-Api-Token': workspaceLogin ? sessionStorage.getItem('cp-login-token') || '' : token,
      'X-Workspace': localWorkspace(),
      ...options.headers,
    },
  });
}
