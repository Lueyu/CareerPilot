import { createContext, useContext } from 'react';

export type AccountInfo = { username: string; workspace_id: number };
export const sessionChangeKey = 'careerpilot-public-session-change';

export class SeedApiError extends Error {
  constructor(public status: number, detail: string, public code?: string) {
    super(detail);
  }
}

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetchResponse(path, options);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function fetchResponse(path: string, options: RequestInit) {
  const headers = new Headers(options.headers);
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
  const response = await fetch('/api/v1' + path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = typeof data.detail === 'string' ? data.detail : data.detail?.message;
    throw new SeedApiError(response.status, detail || (response.status === 422 ? '请检查输入内容后重试。' : '请求未完成，请稍后重试。'), data.detail?.code);
  }
  return response;
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

  async function response(path: string, options: RequestInit = {}): Promise<Response> {
    const signal = options.signal ? AbortSignal.any([lifetime.signal, options.signal]) : lifetime.signal;
    signal.throwIfAborted();
    const headers = new Headers(options.headers);
    headers.set('X-Expected-Workspace', expected);
    try {
      const data = await fetchResponse(path, { ...options, headers, signal });
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

  async function request<T>(path: string, options?: RequestInit, expectedStatus?: number): Promise<T> {
    const result = await response(path, options);
    if (expectedStatus !== undefined && result.status !== expectedStatus) {
      throw new SeedApiError(result.status, '请求未完成，请稍后重试。');
    }
    const data = result.status === 204 ? undefined : await result.json();
    lifetime.signal.throwIfAborted();
    options?.signal?.throwIfAborted();
    return data as T;
  }

  function send<T>(path: string, method: 'POST' | 'PUT', body: unknown,
    options: Pick<RequestInit, 'signal' | 'mode' | 'redirect'> & { expectedStatus?: number } = {}) {
    const { expectedStatus, ...transport } = options;
    return request<T>(path, { ...transport, method, body: JSON.stringify(body) }, expectedStatus);
  }

  async function events(path: string, signal: AbortSignal, onData: (data: string) => void) {
    const stream = await response(path, { signal, headers: { Accept: 'text/event-stream' } });
    if (!stream.body) return;
    const reader = stream.body.getReader();
    const active = AbortSignal.any([signal, lifetime.signal]);
    const cancel = () => { void reader.cancel().catch(() => undefined); };
    active.addEventListener('abort', cancel, { once: true });
    if (active.aborted) cancel();
    const decoder = new TextDecoder();
    let pending = '';
    try {
      while (!active.aborted) {
        const { done, value } = await reader.read();
        if (done || active.aborted) break;
        pending += decoder.decode(value, { stream: true });
        let frame = /\r?\n\r?\n/.exec(pending);
        while (frame && !active.aborted) {
          const lines = pending.slice(0, frame.index).split(/\r?\n/);
          pending = pending.slice(frame.index + frame[0].length);
          if (lines.includes('event: unavailable')) return;
          const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n');
          if (data) onData(data);
          frame = /\r?\n\r?\n/.exec(pending);
        }
      }
    } finally {
      active.removeEventListener('abort', cancel);
      if (!active.aborted) cancel();
      reader.releaseLock();
    }
  }

  return { request, response, send, events, signal: lifetime.signal, close: () => lifetime.abort() };
}

type WorkspaceApi = ReturnType<typeof createWorkspaceApi>;
export const WorkspaceApiContext = createContext<WorkspaceApi | null>(null);

export function useWorkspaceApi() {
  const api = useContext(WorkspaceApiContext);
  if (!api) throw new Error('Workspace API used outside an authenticated workspace');
  return api;
}
