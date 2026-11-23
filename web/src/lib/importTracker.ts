/**
 * 面经导入后台追踪器(全局单例, 跨页面存活)。
 *
 * 导入提交后 trackImport() 注册; 这里以 2s 间隔轮询 GET /bank/imports/{id},
 * 直到 REVIEW/FAILED/APPROVED 终态; 通过 subscribeImports() 通知 UI(悬浮进度条),
 * 通过 onImportFinished() 通知完成回调(toast + 面经库刷新)。
 *
 * 超时放宽: 单次导入追踪上限 15 分钟(此前前端 3 分钟就报错), 到点只提示
 * "稍后可到待审核查看", 不再中断后端流水线(后台任务继续跑完落库)。
 */
import { api, ImportReview, workspaceStorageKey } from '../api/client';

export interface TrackedImport {
  id: number;
  shortCode: string;
  step: number;
  total: number;
  note: string;
  status: string;
  startedAt: number;
  elapsedMs: number;
}

export interface ImportFinished {
  id: number;
  shortCode: string;
  status: string; // REVIEW | FAILED | APPROVED | TIMEOUT
  itemCount: number;
  error?: string;
}

type Listener = (list: TrackedImport[]) => void;
type FinishFn = (r: ImportFinished) => void;

const TRACK_TIMEOUT_MS = 15 * 60 * 1000; // 15 分钟(原 3 分钟)

const active = new Map<number, TrackedImport>();
const listeners = new Set<Listener>();
const finishCbs = new Set<FinishFn>();
let timer: ReturnType<typeof setInterval> | null = null;

// sessionStorage 持久化: 页面刷新(F5/整页跳转)后恢复追踪, 不丢后台任务的可视性。
let generation = 0;
let restoredKey: string | undefined;
const STORE_KEY = 'cp-import-tracking-v1';

function persist(): void {
  try {
    sessionStorage.setItem(workspaceStorageKey(STORE_KEY), JSON.stringify([...active.values()]));
  } catch { /* 存储不可用时静默降级 */ }
}

function restore(): void {
  const key = workspaceStorageKey(STORE_KEY);
  if (restoredKey === key) return;
  reset();
  restoredKey = key;
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return;
    const arr = JSON.parse(raw) as TrackedImport[];
    arr.forEach((t) => {
      if (!t || typeof t.id !== 'number') return;
      if (Date.now() - t.startedAt > TRACK_TIMEOUT_MS) return;
      active.set(t.id, { ...t, elapsedMs: Date.now() - t.startedAt });
    });
    if (active.size && !timer) timer = setInterval(() => void pollOnce(), 2000);
  } catch { /* 数据损坏时忽略 */ }
}

function reset(): void {
  generation += 1;
  restoredKey = undefined;
  active.clear();
  if (timer) clearInterval(timer);
  timer = null;
}

function notify(): void {
  const list = [...active.values()];
  listeners.forEach((l) => l(list));
}

async function pollOnce(): Promise<void> {
  const current = generation;
  const ids = [...active.keys()];
  if (!ids.length) return;
  const settled = await Promise.allSettled(
    ids.map((id) => api.get<ImportReview>(`/bank/imports/${id}`)),
  );
  if (current !== generation) return;
  settled.forEach((res, i) => {
    const id = ids[i];
    const t = active.get(id);
    if (!t) return;
    if (res.status !== 'fulfilled') {
      // 网络抖动/后端重启: 保留追踪, 超过总时长才放弃 UI 追踪
      t.elapsedMs = Date.now() - t.startedAt;
      if (t.elapsedMs > TRACK_TIMEOUT_MS) {
        active.delete(id);
        persist();
        finishCbs.forEach((f) => f({ id, shortCode: t.shortCode, status: 'TIMEOUT', itemCount: 0 }));
      }
      return;
    }
    const r = res.value;
    t.step = Number(r.meta?.progress?.step ?? t.step);
    t.total = Number(r.meta?.progress?.total ?? 5);
    t.note = r.meta?.progress?.note || t.note;
    t.status = r.status;
    t.elapsedMs = Date.now() - t.startedAt;
    if (r.status === 'REVIEW' || r.status === 'FAILED' || r.status === 'APPROVED') {
      active.delete(id);
      persist();
      finishCbs.forEach((f) => f({
        id,
        shortCode: t.shortCode,
        status: r.status,
        itemCount: r.items?.length ?? 0,
        error: r.meta?.error,
      }));
    }
  });
  notify();
  if (!active.size && timer) {
    clearInterval(timer);
    timer = null;
  }
}

/** 注册一次后台导入(POST 成功后立即调用, 用户即可离开当前页面)。 */
export function trackImport(id: number, shortCode: string): void {
  restore();
  active.set(id, {
    id,
    shortCode,
    step: 0,
    total: 5,
    note: '已接收，后台解析中',
    status: 'UPLOADED',
    startedAt: Date.now(),
    elapsedMs: 0,
  });
  if (!timer) timer = setInterval(() => void pollOnce(), 2000);
  persist();
  notify();
}

/** 订阅进行中的导入列表(悬浮进度条用)。返回退订函数。 */
export function subscribeImports(fn: Listener): () => void {
  restore();
  listeners.add(fn);
  fn([...active.values()]);
  return () => {
    listeners.delete(fn);
    if (!listeners.size) reset();
  };
}

/** 订阅导入完成事件(toast/刷新用)。返回退订函数。 */
export function onImportFinished(fn: FinishFn): () => void {
  finishCbs.add(fn);
  return () => { finishCbs.delete(fn); };
}
