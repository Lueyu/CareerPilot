import { getLocale } from '../i18n';

const base = import.meta.env?.VITE_API_BASE || 'http://127.0.0.1:8000/api/v1';
const initialWorkspace = import.meta.env?.VITE_WORKSPACE || 'personal';
export const workspaceLogin = import.meta.env?.VITE_AUTH_MODE === 'workspace';
export const requiresLocalLogin = true;

function storedWorkspace(): string {
  return globalThis.sessionStorage?.getItem('cp-login-workspace') || globalThis.localStorage?.getItem('cp-workspace') || initialWorkspace;
}

// Workspace selection takes effect on navigation. Until then this document
// must keep sending requests for the workspace it rendered.
let browserWorkspace = typeof window !== 'undefined' ? storedWorkspace() : undefined;

export function localWorkspace(): string {
  return browserWorkspace ?? storedWorkspace();
}

export function beginLocalSession(slug: string, token: string) {
  sessionStorage.setItem('cp-login-workspace', slug);
  sessionStorage.setItem('cp-login-token', token);
  browserWorkspace = slug;
}

export function clearLocalSession() {
  sessionStorage.removeItem('cp-login-workspace');
  sessionStorage.removeItem('cp-login-token');
  browserWorkspace = undefined;
}

export function selectLocalWorkspace(slug: string) {
  localStorage.setItem('cp-workspace', slug);
  globalThis.sessionStorage?.setItem('cp-login-workspace', slug);
}

export function localFetch(path: string, options: RequestInit) {
  return fetch(base + path, {
    ...options,
    headers: {
      'X-Api-Token': globalThis.sessionStorage?.getItem('cp-login-token') || '',
      'X-Workspace': localWorkspace(),
      'Accept-Language': getLocale(),
      ...options.headers,
    },
  });
}
