export class SeedApiError extends Error {
  constructor(public status: number, detail: string) {
    super(detail);
  }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
  const response = await fetch('/api/v1' + path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = typeof data.detail === 'string' ? data.detail : data.detail?.message;
    if (response.status === 401 && !path.startsWith('/auth/')) {
      window.dispatchEvent(new Event('careerpilot-session-expired'));
    }
    throw new SeedApiError(response.status, detail || (response.status === 422 ? '请检查输入内容后重试。' : '请求未完成，请稍后重试。'));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
