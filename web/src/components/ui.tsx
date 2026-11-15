import React, { ReactNode } from 'react';
import { STATUS_META, DomainSkill } from '../data/demo';

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

// —— 页面头 ——
export function PageHead({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}

// —— DEMO 横幅 ——
export function DemoBanner({ text }: { text?: string }) {
  return (
    <div className="demo-banner">
      <span className="demo-tag">DEMO</span>
      <span>{text ?? '个人工作空间 · 全部为示例数据，非真实能力评估或行业统计。'}</span>
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
  size?: 'sm' | 'lg';
}) {
  return (
    <div className="modal-mask" onClick={onClose}>
      <div
        className={`modal ${size === 'lg' ? 'modal-lg' : size === 'sm' ? 'modal-sm' : ''}`}
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
  items: { name: string; current: number; weight: number; color?: string }[];
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
                width: `${(it.current / 5) * 100}%`,
                background: it.color ?? 'var(--accent-grad)',
              }}
            />
          </div>
          <span className="seg-val">{it.current.toFixed(1)}</span>
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
