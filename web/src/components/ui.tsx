import React, { ReactNode } from 'react';
import { STATUS_META } from '../data/demo';
import { getWorkspace } from '../api/client';

// —— 状态徽章 ——
export function StatusChip({ status, label }: { status: string; label?: string }) {
  const m = (STATUS_META as Record<string, { label: string; cls: string; dot: string }>)[status] || { label: status, cls: 'chip-slate', dot: 'dot-slate' };
  return (
    <span className={`chip ${m.cls}`}>
      <span className={`status-dot ${m.dot}`} />
      {label ?? m.label}
    </span>
  );
}

export { PageHead } from './PageHead';

// —— DEMO 横幅 ——
export function DemoBanner({ text }: { text?: string }) {
  if (getWorkspace() !== 'demo') return null;
  return (
    <div className="demo-banner">
      <span className="demo-tag">DEMO</span>
      <span>{text ?? '演示工作空间 · 与个人空间隔离，记录仅用于演示。'}</span>
    </div>
  );
}

// —— 模态框 ——
export function Modal({
  title,
  onClose,
  children,
  footer,
  size,
}: {
  title?: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'lg' | 'xl';
}) {
  return (
    <div className="modal-mask" onClick={onClose}>
      <div
        className={`modal ${size === 'lg' ? 'modal-lg' : size === 'sm' ? 'modal-sm' : size === 'xl' ? 'modal-xl' : ''}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <div className="modal-title">{title}</div>
          <button className="modal-x" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

// —— 统计卡 ——
export function StatCard({
  label,
  value,
  delta,
  dir,
  foot,
  ic,
}: {
  label: string;
  value: ReactNode;
  delta?: string;
  dir?: 'up' | 'down' | 'flat';
  foot?: string;
  ic?: string;
}) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">
        {value}
        {delta && (
          <small className={`stat-delta ${dir === 'up' ? 'delta-up' : dir === 'down' ? 'delta-down' : 'delta-flat'}`}>
            {delta}
          </small>
        )}
      </div>
      {foot && <div className="stat-foot">{foot}</div>}
      {ic && <div className="stat-ic">{ic}</div>}
    </div>
  );
}

// —— 分段权重进度条（深色 hero 内）——
export function SegBars({
  items,
}: {
  items: { name: string; current: number | null; weight: number; color?: string }[];
}) {
  return (
    <div className="seg-bars">
      {items.map((it) => (
        <div className="seg-row" key={it.name}>
          <span className="seg-name">{it.name}</span>
          <div className="seg-track">
            <div
              className="seg-fill"
              style={{
                width: `${((it.current ?? 0) / 5) * 100}%`,
                background: it.color ?? 'var(--accent-grad)',
              }}
            />
          </div>
          <span className="seg-val">{it.current == null ? '未知' : it.current.toFixed(1)}</span>
        </div>
      ))}
    </div>
  );
}

// —— 分数 / 星级 ——
export function Stars({ n, max = 5 }: { n: number; max?: number }) {
  const full = Math.round(n);
  return (
    <span className="stars">
      {'★'.repeat(full)}
      {'☆'.repeat(Math.max(0, max - full))}
    </span>
  );
}

// —— 空态 ——
export function Empty({ ic = '🗂️', title, sub }: { ic?: string; title: string; sub?: string }) {
  return (
    <div className="empty">
      <div className="empty-ic">{ic}</div>
      <b>{title}</b>
      {sub && <p>{sub}</p>}
    </div>
  );
}

// —— 难度 ——
export function DiffLabel({ d }: { d: number }) {
  const map: Record<number, [string, string]> = {
    1: ['简单', 'chip-green'],
    2: ['中等', 'chip-amber'],
    3: ['较难', 'chip-red'],
    4: ['困难', 'chip-red'],
  };
  const [label, cls] = map[d] ?? ['中等', 'chip-amber'];
  return <span className={`chip ${cls}`}>{label}</span>;
}

// —— v3.1 M3: 等级 → 通俗语言 ——
export function plainLevel(level: number | null | undefined): string {
  if (level == null) return '还没有可靠评估';
  if (level < 1.5) return '刚接触，还停留在概念层面';
  if (level < 2.5) return '初步达到基础理解，还没到独立干活的程度';
  if (level < 3.5) return '能独立完成常规任务，复杂场景还需要支持';
  if (level < 4.5) return '能够独立设计并验证方案';
  return '精通：能定义标准、带人踩坑';
}

// API confidence is an observation-count heuristic, not calibrated accuracy.
export const OBSERVATION_COVERAGE_NOTE = '观察覆盖按独立观察数量作启发式提示，不代表评分准确率，也不是结论可靠性的概率。';
export function plainConfidence(c: number | null | undefined): string {
  if (c == null || !Number.isFinite(c)) return '观察覆盖未知';
  if (c >= 0.5) return '已有更多独立观察';
  return '观察覆盖较少';
}

// —— v3.1 M2: 页面 Tab 头(合并页容器用) ——
export function PageTabs({
  items,
  active,
  onChange,
}: {
  items: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="tabs" style={{ marginBottom: 16 }}>
      {items.map((it) => (
        <button
          key={it.key}
          className={`tab ${active === it.key ? 'active' : ''}`}
          onClick={() => onChange(it.key)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
