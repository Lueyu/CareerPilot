import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  DEMO_CONTEXT_RESET_EVENT,
  isDemoContextReset,
  resetDemoContext,
  restoreDemoMemory,
  useDemoMemory,
} from "./contextStore";
import { useToolCopy } from "./copy";
import { ToolIcon, chipClass, demoTagClass, ghostBtnClass, hintClass, outlineBtnClass } from "./ui";

// 仅 Demo 工作区可见：顶栏示例记忆胶囊、演示摘要提示、输入区演示控制条。
// 真实记忆计数与真实记忆面板由 InterviewShell 原生控件提供，不被这里替代。

export function MemoryIndicator({ onOpenLongTerm }: { onOpenLongTerm?: () => void }) {
  const t = useToolCopy();
  const memory = useDemoMemory();
  const cleared = isDemoContextReset();
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) {
        setOpen(false);
        setConfirming(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        setConfirming(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const summary = cleared
    ? t("memory.chipEmpty")
    : t("memory.chip", { count: memory.weakPoints.length });

  return (
    <div className="relative" ref={root}>
      <button
        type="button"
        className="inline-flex items-center gap-2 rounded-[10px] border border-[var(--ia-line)] bg-[var(--ia-surface)] px-3 py-1.5 text-xs text-[var(--ia-muted)] transition-colors hover:border-[var(--ia-accent)]"
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
          setConfirming(false);
        }}
      >
        <span className={demoTagClass}>{t("memory.sample")}</span>
        <span className="max-w-[120px] truncate">{summary}</span>
        <ToolIcon
          name="chevronDown"
          size={13}
          className={"shrink-0 transition-transform " + (open ? "rotate-180" : "")}
        />
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-30 w-[380px] max-w-[90vw] rounded-xl border border-[var(--ia-line)] bg-[var(--ia-surface)] p-4 shadow-[var(--shadow-lg)]">
          <div className="flex items-center gap-2">
            <ToolIcon name="brain" size={15} className="text-[var(--ia-accent)]" />
            <p className="text-[15px] font-semibold text-[var(--ia-ink)]">
              {t("memory.panelTitle")}
            </p>
          </div>
          <p className={"mt-1 " + hintClass}>{t("memory.panelHint")}</p>
          {cleared ? (
            <div className="mt-3 flex flex-col gap-2">
              <p className={"rounded-lg bg-[var(--ia-bg)] px-3 py-3 " + hintClass}>
                {t("memory.longTermNote")}
              </p>
              <button type="button" className={outlineBtnClass} onClick={() => restoreDemoMemory()}>
                {t("memory.restore")}
              </button>
            </div>
          ) : (
            <div className="mt-3 flex flex-col gap-2.5">
              <MemoryRow label={t("memory.target")}>
                <span className={chipClass + " bg-[var(--ia-tint)] text-[var(--ia-accent)]"}>
                  {memory.targetRole}
                </span>
              </MemoryRow>
              <MemoryRow label={t("memory.weak")}>
                {memory.weakPoints.length ? (
                  memory.weakPoints.map((item) => (
                    <span
                      key={item}
                      className={chipClass + " bg-[var(--red-bg)] text-[var(--red)]"}
                    >
                      {item}
                    </span>
                  ))
                ) : (
                  <span className={hintClass}>{t("memory.none")}</span>
                )}
              </MemoryRow>
              <MemoryRow label={t("memory.skill")}>
                <span className={chipClass + " bg-[var(--ia-tint)] text-[var(--ia-accent)]"}>
                  {memory.currentSkill ?? t("memory.notStarted")}
                </span>
              </MemoryRow>
              <MemoryRow label={t("memory.exams")}>
                <span className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
                  {t("memory.count", { count: memory.examCount })}
                </span>
              </MemoryRow>
            </div>
          )}
          <div className="mt-3 border-t border-[var(--ia-line)] pt-3">
            {confirming ? (
              <div className="rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] p-3">
                <p className="text-[13px] font-semibold text-[var(--ia-ink)]">
                  {t("memory.clearTitle")}
                </p>
                <p className={"mt-1 " + hintClass}>{t("memory.clearDesc")}</p>
                <div className="mt-2.5 flex justify-end gap-2">
                  <button
                    type="button"
                    className={ghostBtnClass + " text-xs"}
                    onClick={() => setConfirming(false)}
                  >
                    {t("demo.cancel")}
                  </button>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--red)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                    onClick={() => {
                      resetDemoContext();
                      setConfirming(false);
                      setOpen(false);
                    }}
                  >
                    <ToolIcon name="eraser" size={12} />
                    {t("memory.clearConfirm")}
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className={ghostBtnClass + " text-xs text-[var(--red)]"}
                  onClick={() => setConfirming(true)}
                >
                  <ToolIcon name="eraser" size={12} />
                  {t("memory.clear")}
                </button>
                <button
                  type="button"
                  className={hintClass + " inline-flex items-center gap-1 text-[var(--ia-muted)]"}
                  onClick={() =>
                    onOpenLongTerm
                      ? onOpenLongTerm()
                      : window.dispatchEvent(new Event("interview-memory-open"))
                  }
                >
                  <ToolIcon name="history" size={12} />
                  {t("memory.openReal")}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function MemoryRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-xs font-medium text-[var(--ia-muted)]">{label}</p>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

export function CompressionNotice() {
  const t = useToolCopy();
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-[10px] bg-[var(--ia-sand)] px-3.5 py-2.5">
      <button
        type="button"
        className="flex w-full flex-wrap items-center gap-2.5 text-left"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={chipClass + " shrink-0 bg-[var(--ia-amber)] text-[var(--ia-sand)]"}>
          <ToolIcon name="sparkles" size={11} />
          {t("summary.tag")}
        </span>
        <span className="min-w-0 flex-1 text-[13px] text-[var(--ia-amber)]">
          {t("summary.text")}
        </span>
        <span className="shrink-0 text-xs font-medium text-[var(--ia-amber)]">
          {open ? t("summary.collapse") : t("summary.expand")}
        </span>
      </button>
      {open && (
        <div className="mt-2 border-t border-[var(--ia-line)] pt-2">
          <p className="text-[13px] leading-relaxed text-[var(--ia-amber)]">{t("summary.full")}</p>
          <p className={"mt-1.5 " + hintClass + " text-[var(--ia-amber)]"}>{t("summary.foot")}</p>
        </div>
      )}
    </div>
  );
}

export function ContextControls({ onNewChat }: { onNewChat: () => void }) {
  const t = useToolCopy();
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className={outlineBtnClass} onClick={onNewChat}>
        <ToolIcon name="messageSquarePlus" size={13} />
        {t("demo.newChat")}
      </button>
      {confirming ? (
        <span className="inline-flex flex-wrap items-center gap-2 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] px-2.5 py-1">
          <span className={hintClass}>{t("demo.clearShort")}</span>
          <button
            type="button"
            className={hintClass + " text-[var(--ia-muted)]"}
            onClick={() => setConfirming(false)}
          >
            {t("demo.cancel")}
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-md bg-[var(--red)] px-2 py-1 text-xs font-medium text-white hover:opacity-90"
            onClick={() => {
              resetDemoContext();
              setConfirming(false);
            }}
          >
            {t("demo.confirm")}
          </button>
        </span>
      ) : (
        <button type="button" className={ghostBtnClass} onClick={() => setConfirming(true)}>
          <ToolIcon name="eraser" size={13} />
          {t("demo.reset")}
        </button>
      )}
      <span className={hintClass + " hidden sm:inline"}>{t("demo.contextHint")}</span>
      <span className={hintClass + " hidden sm:inline"}>{t("demo.onlyDemo")}</span>
    </div>
  );
}

export { DEMO_CONTEXT_RESET_EVENT };
