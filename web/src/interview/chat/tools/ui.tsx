import type { ReactNode } from "react";

// 工具层组件共用的小原语。颜色引用 interview 模块的 --ia-* 变量，深浅主题自动适配。

export const cardClass =
  "w-full rounded-xl border border-[var(--ia-line)] bg-[var(--ia-surface)] shadow-[var(--shadow)]";
export const chipClass =
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium";
export const ghostBtnClass =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-3 py-1.5 text-[13px] font-medium text-[var(--ia-muted)] transition-colors hover:border-[var(--ia-accent)] hover:text-[var(--ia-accent)] disabled:opacity-50";
export const outlineBtnClass =
  "inline-flex items-center gap-1.5 rounded-lg border border-[var(--ia-tint)] bg-[var(--ia-surface)] px-3 py-1.5 text-xs font-medium text-[var(--ia-accent)] transition-colors hover:bg-[var(--ia-tint)]";
export const primaryBtnClass =
  "inline-flex items-center gap-1.5 rounded-lg bg-[var(--ia-accent)] px-3 py-1.5 text-[13px] font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50";
export const sectionLabelClass = "text-[13px] font-semibold text-[var(--ia-ink)]";
export const hintClass = "text-xs text-[var(--ia-muted)]";

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
      <header className="mb-3 flex items-center gap-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-[var(--ia-tint)] text-[var(--ia-accent)]">
          {icon}
        </span>
        <h3 className="text-[15px] font-semibold text-[var(--ia-ink)]">{title}</h3>
        {detail && <span className="ml-auto text-xs text-[var(--ia-muted)]">{detail}</span>}
      </header>
      {children}
    </section>
  );
}
