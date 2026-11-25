/**
 * 无感产品遥测(匿名聚合, 无 PII)。
 *
 * - 只收集: 匿名 client_id / 事件名 / 页面路径 / 设备 / locale / 时区 / 视口
 * - 绝不收集: IP 原文、用户名、消息内容、任何业务数据
 * - 静默失败: sendBeacon 发送, 任何异常都不打扰用户、不抛错、不写控制台
 */
const env = (import.meta as any).env;
const localApi = env?.DEV && typeof window !== 'undefined' && env['VITE_DEV_API_' + window.location.port];
const base = localApi || env?.VITE_API_BASE || 'http://127.0.0.1:8000/api/v1';

const ID_KEY = 'careerpilot.telemetry.id';
const FLUSH_MS = 2000;
const MAX_BATCH = 25;

interface TelemetryItem {
  name: string;
  page?: string | null;
  props?: Record<string, unknown> | null;
}

let clientId: string | null = null;
let queue: TelemetryItem[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

function storedId(): string {
  if (clientId) return clientId;
  try {
    let id = window.localStorage?.getItem(ID_KEY);
    if (!id) {
      id = crypto?.randomUUID?.() ?? `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
      window.localStorage.setItem(ID_KEY, id);
    }
    clientId = id;
  } catch {
    clientId = `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  }
  return clientId;
}

function deviceKind(): string {
  const ua = window.navigator?.userAgent || '';
  if (/iPad|Tablet/i.test(ua) || (/Macintosh/i.test(ua) && window.navigator?.maxTouchPoints > 1)) return 'tablet';
  if (/Mobi|Android|iPhone/i.test(ua)) return 'mobile';
  return 'desktop';
}

function timezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch {
    return '';
  }
}

function flush() {
  timer = null;
  if (!queue.length) return;
  const events = queue;
  queue = [];
  try {
    const payload = JSON.stringify({
      client_id: storedId(),
      events,
      locale: (typeof document !== 'undefined' && document.documentElement.lang) || 'zh-CN',
      timezone: timezone(),
      device: deviceKind(),
      viewport: typeof window !== 'undefined' ? `${window.innerWidth}x${window.innerHeight}` : '',
    });
    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      const ok = navigator.sendBeacon(`${base}/telemetry/events`, new Blob([payload], { type: 'application/json' }));
      if (ok) return;
    }
    fetch(`${base}/telemetry/events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    /* telemetry is best-effort only */
  }
}

/** 记录一个匿名事件(静默, 批量发送)。 */
export function track(name: string, page?: string | null, props?: Record<string, unknown>) {
  try {
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    const path = page ?? (window.location.pathname + window.location.search);
    queue.push({ name, page: path, props: props ?? null });
    if (queue.length >= MAX_BATCH) {
      flush();
      return;
    }
    if (!timer) timer = setTimeout(flush, FLUSH_MS);
  } catch {
    /* never throw from telemetry */
  }
}

/** 页面浏览事件(路由变化时由 TelemetryTracker 调用)。 */
export function pageView(path: string) {
  track('page_view', path);
}

if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => flush());
}
