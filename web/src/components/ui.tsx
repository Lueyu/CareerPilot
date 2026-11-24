import { t, useT } from '../i18n';
import React, { ReactNode } from 'react';
import { STATUS_META } from '../data/demo';
import { getWorkspace } from '../api/client';

const STATUS_LABEL_KEYS = new Map([
  ['UNKNOWN', 'shell.statusUnknown'],
  ['NEEDS_VALIDATION', 'shell.statusNeedsValidation'],
  ['BELOW_TARGET', 'shell.statusBelowTarget'],
  ['MEETS_TARGET', 'shell.statusMeetsTarget'],
  ['ABOVE_TARGET', 'shell.statusAboveTarget'],
]);

// —— 状态徽章 ——
export function StatusChip({ status, label }: { status: string; label?: string }) {
  const t = useT();
  const m = (STATUS_META as Record<string, { label: string; cls: string; dot: string }>)[status] || { label: status, cls: 'chip-slate', dot: 'dot-slate' };
  const labelKey = STATUS_LABEL_KEYS.get(status);
  return (
    <span className={`chip ${m.cls}`}>
      <span className={`status-dot ${m.dot}`} />
      {label ?? (labelKey ? t(labelKey) : m.label)}
    </span>
  );
}

export { PageHead } from './PageHead';

// —— DEMO 横幅 ——
export function DemoBanner({ text }: { text?: string }) {
  const t = useT();
  if (getWorkspace() !== 'demo') return null;
  return (
    <div className="demo-banner">
      <span className="demo-tag">{t('shell.demoTag')}</span>
      <span>{text ?? t('shell.demoBanner')}</span>
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
  const t = useT();
  return (
    <div className="modal-mask" onClick={onClose}>
      <div
        className={`modal ${size === 'lg' ? 'modal-lg' : size === 'sm' ? 'modal-sm' : size === 'xl' ? 'modal-xl' : ''}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <div className="modal-title">{title}</div>
          <button className="modal-x" onClick={onClose} aria-label={t('shell.close')}>✕</button>
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
  const t = useT();
  return (
    <div className="seg-bars">
      {items.map((it, index) => (
        <div className="seg-row" key={index}>
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
          <span className="seg-val">{it.current == null ? t('shell.unknown') : it.current.toFixed(1)}</span>
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
  const t = useT();
  const map: Record<number, [string, string]> = {
    1: ['shell.difficultyEasy', 'chip-green'],
    2: ['shell.difficultyMedium', 'chip-amber'],
    3: ['shell.difficultyChallenging', 'chip-red'],
    4: ['shell.difficultyHard', 'chip-red'],
  };
  const [label, cls] = map[d] ?? ['shell.difficultyMedium', 'chip-amber'];
  return <span className={`chip ${cls}`}>{t(label)}</span>;
}

// —— v3.1 M3: 等级 → 通俗语言 ——
export function plainLevel(level: number | null | undefined): string {
  if (level == null) return t('shell.levelUnassessed');
  if (level < 1.5) return t('shell.levelIntroductory');
  if (level < 2.5) return t('shell.levelBasic');
  if (level < 3.5) return t('shell.levelIndependent');
  if (level < 4.5) return t('shell.levelDesign');
  return t('shell.levelExpert');
}

// API confidence is an observation-count heuristic, not calibrated accuracy.
function ObservationCoverageNote() {
  const t = useT();
  return t('shell.coverageNote');
}

export const OBSERVATION_COVERAGE_NOTE = <ObservationCoverageNote />;
export function plainConfidence(c: number | null | undefined): string {
  if (c == null || !Number.isFinite(c)) return t('shell.coverageUnknown');
  if (c >= 0.5) return t('shell.coverageHigher');
  return t('shell.coverageLower');
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
