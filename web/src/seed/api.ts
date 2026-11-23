import { createContext, useContext } from 'react';

export type AccountInfo = { username: string; workspace_id: number };
export const sessionChangeKey = 'careerpilot-public-session-change';

export class SeedApiError extends Error {
  constructor(public status: number, detail: string, public code?: string) {
    super(detail);
  }
}

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
  const response = await fetch('/api/v1' + path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = typeof data.detail === 'string' ? data.detail : data.detail?.message;
    throw new SeedApiError(response.status, detail || (response.status === 422 ? '请检查输入内容后重试。' : '请求未完成，请稍后重试。'), data.detail?.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

type AuthPath = '/auth/me' | '/auth/login' | '/auth/register' | '/auth/recover';

export function requestAuth<T>(path: AuthPath, options?: RequestInit) {
  return requestJson<T>(path, options);
}

export function announceSessionChange() {
  try {
    window.localStorage.setItem(sessionChangeKey, crypto.randomUUID());
  } catch {
    // A tab also checks the session when it regains focus.
  }
}

export function createWorkspaceApi(workspaceId: number, onSessionInvalid: () => void) {
  const lifetime = new AbortController();
  const expected = String(workspaceId);

  async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const signal = options.signal ? AbortSignal.any([lifetime.signal, options.signal]) : lifetime.signal;
    signal.throwIfAborted();
    const headers = new Headers(options.headers);
    headers.set('X-Expected-Workspace', expected);
    try {
      const data = await requestJson<T>(path, { ...options, headers, signal });
      signal.throwIfAborted();
      return data;
    } catch (error) {
      signal.throwIfAborted();
      if (error instanceof SeedApiError && (
        error.status === 409 && error.code === 'workspace_changed' ||
        error.status === 401 && error.code === 'session_required'
      )) onSessionInvalid();
      throw error;
    }
  }

  function send<T>(path: string, method: 'POST' | 'PUT', body: unknown) {
    return request<T>(path, { method, body: JSON.stringify(body) });
  }

  function events(path: string, signal: AbortSignal) {
    const active = AbortSignal.any([lifetime.signal, signal]);
    if (active.aborted) return null;
    const url = new URL('/api/v1' + path, window.location.origin);
    url.searchParams.set('expected_workspace_id', expected);
    const source = new EventSource(url.pathname + url.search);
    active.addEventListener('abort', () => source.close(), { once: true });
    return source;
  }

  return { request, send, events, close: () => lifetime.abort() };
}

type WorkspaceApi = ReturnType<typeof createWorkspaceApi>;
export const WorkspaceApiContext = createContext<WorkspaceApi | null>(null);

export function useWorkspaceApi() {
  const api = useContext(WorkspaceApiContext);
  if (!api) throw new Error('Workspace API used outside an authenticated workspace');
  return api;
}
