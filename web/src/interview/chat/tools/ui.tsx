import type { ReactNode } from "react";
import { useToolCopy } from "./copy";

// 工具层专用原语。颜色全部引用 interview.css 的 --ia-* 变量，深浅主题自动适配。
// 正文字号不低于 12px，避免旧稿 10–11.5px 的低对比小字。

export const cardClass =
  "w-full max-w-full rounded-xl border border-[var(--ia-line)] bg-[var(--ia-surface)] shadow-[var(--shadow)]";
export const chipClass =
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium";
export const ghostBtnClass =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-3 py-1.5 text-[13px] font-medium text-[var(--ia-muted)] transition-colors hover:border-[var(--ia-accent)] hover:text-[var(--ia-accent)] disabled:opacity-50";
export const outlineBtnClass =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--ia-tint)] bg-[var(--ia-surface)] px-3 py-1.5 text-xs font-medium text-[var(--ia-accent)] transition-colors hover:bg-[var(--ia-tint)]";
export const primaryBtnClass =
  "inline-flex items-center gap-1.5 rounded-lg bg-[var(--ia-accent)] px-3 py-1.5 text-[13px] font-medium text-[var(--ia-surface)] transition-opacity hover:opacity-90 disabled:opacity-50";
export const sectionLabelClass = "text-[13px] font-semibold text-[var(--ia-ink)]";
export const hintClass = "text-xs text-[var(--ia-muted)]";
export const demoTagClass =
  "inline-flex shrink-0 items-center rounded-full bg-[var(--ia-sand)] px-2 py-0.5 text-[11px] font-semibold tracking-wide text-[var(--ia-amber)]";

export function DemoTag() {
  const t = useToolCopy();
  return <span className={demoTagClass}>{t("demo.tag")}</span>;
}

/** 演示模式下所有声称会写入数据的操作都渲染为禁用态。 */
export function DemoDisabledButton({ children }: { children: ReactNode }) {
  const t = useToolCopy();
  return (
    <button
      type="button"
      disabled
      title={t("demo.disabledHint")}
      aria-disabled="true"
      className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] px-3 py-1.5 text-xs font-medium text-[var(--ia-muted)] opacity-70"
    >
      {children}
    </button>
  );
}

export function DemoBanner({ action }: { action?: ReactNode }) {
  const t = useToolCopy();
  return (
    <div className="ia-tools-demo-banner" role="note">
      <DemoTag />
      <span className="ia-tools-demo-banner-text">{t("demo.banner")}</span>
      {action && <span className="ia-tools-demo-banner-action">{action}</span>}
    </div>
  );
}

// 工具层图标：沿用项目自绘 SVG 的 stroke 风格，不引入第三方图标包。
const toolIconPaths = {
  brain:
    "M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5 3 3 0 0 0 2 5 3 3 0 0 0 5 1V4a3 3 0 0 0-3-1ZM15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5 3 3 0 0 1-2 5 3 3 0 0 1-5 1V4a3 3 0 0 1 3-1ZM12 5v14",
  chevronDown: "m6 9 6 6 6-6",
  chevronRight: "m9 6 6 6-6 6",
  eraser: "m4 15 8-9a2 2 0 0 1 3 0l5 5a2 2 0 0 1 0 3l-7 7H8l-4-4ZM9 11l6 6M10 21h10",
  history: "M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5M12 7v5l3 2",
  messageSquarePlus:
    "M21 15a2 2 0 0 1-2 2H8l-4 3V6a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v9ZM12 8v6m-3-3h6",
  sparkles:
    "M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3ZM19 15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2Z",
  calendarDays: "M4 6h16v15H4zM8 3v4m8-4v4M4 11h16M8 15h2m4 0h2",
  check: "m5 12 4 4L19 6",
  download: "M12 3v12m-4-4 4 4 4-4M4 19h16",
  network:
    "M12 3a2 2 0 1 1 0 4 2 2 0 0 1 0-4ZM5 17a2 2 0 1 1 0 4 2 2 0 0 1 0-4ZM19 17a2 2 0 1 1 0 4 2 2 0 0 1 0-4ZM12 7v4M6 17l5-4m7 4-5-4",
  plus: "M12 5v14M5 12h14",
  listChecks: "m3 6 1.5 1.5L7 5m3 1h10M3 12l1.5 1.5L7 11m3 1h10M3 18l1.5 1.5L7 17m3 1h10",
  minus: "M5 12h14",
  pause: "M9 5v14M15 5v14",
  pencil: "M4 20l4-1 11-11-3-3L5 16l-1 4ZM15 6l3 3",
  skipForward: "m5 5 9 7-9 7V5ZM18 5v14",
  arrowRightLeft: "M7 7h13l-3-3m3 3-3 3M17 17H4l3-3m-3 3 3 3",
  copy: "M8 8h11v12H8zM5 16H4V4h11v1",
  gitCompare: "M6 4v12a3 3 0 0 0 3 3h9m0 0-3-3m3 3-3 3M18 4v12a3 3 0 0 1-3 3H6m0 0 3-3m-3 3 3 3",
  fileText: "M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6",
  loader: "M12 3a9 9 0 1 1-9 9",
  rotateCcw: "M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14ZM16 16l5 5",
  squareTerminal: "M3 4h18v16H3zM7 9l3 3-3 3m6 0h4",
  triangleAlert: "M12 4 2 20h20L12 4ZM12 10v5m0 3h.01",
  mic: "M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3ZM5 11a7 7 0 0 0 14 0M12 18v3m-3 0h6",
  volume2: "M4 9h4l5-4v14l-5-4H4V9ZM17 9a4 4 0 0 1 0 6m2-9a8 8 0 0 1 0 12",
} as const;

export type ToolIconName = keyof typeof toolIconPaths;

export function ToolIcon({
  name,
  size = 14,
  className,
}: {
  name: ToolIconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ flexShrink: 0 }}
    >
      <path d={toolIconPaths[name]} />
    </svg>
  );
}

export function ToolCard({
  icon,
  title,
  detail,
  children,
}: {
  icon: ReactNode;
  title: string;
  detail?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={`${cardClass} p-4`}>
      <header className="mb-3 flex flex-wrap items-center gap-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[var(--ia-tint)] text-[var(--ia-accent)]">
          {icon}
        </span>
        <DemoTag />
        <h3 className="text-[15px] font-semibold text-[var(--ia-ink)]">{title}</h3>
        {detail && <span className="ml-auto text-xs text-[var(--ia-muted)]">{detail}</span>}
      </header>
      {children}
    </section>
  );
}
