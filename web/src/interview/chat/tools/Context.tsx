import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Brain,
  ChevronDown,
  Eraser,
  History,
  MessageSquarePlus,
  Sparkles,
} from "lucide-react";
import { COMPRESSION_SUMMARY } from "./model";
import { clearAgentContext, useAgentMemory, CONTEXT_CLEARED_EVENT } from "./contextStore";
import { chipClass, ghostBtnClass, hintClass, outlineBtnClass } from "./ui";

// ===== 记忆状态指示器（顶部栏） =====
// 摘要胶囊 + 可展开的工作记忆面板 + 清除上下文（二次确认）。

export function MemoryIndicator({
  compact,
  onOpenLongTerm,
}: {
  compact?: boolean;
  onOpenLongTerm?: () => void;
}) {
  const memory = useAgentMemory();
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
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const loaded =
    !!memory.targetRole ||
    memory.weakPoints.length > 0 ||
    !!memory.currentSkill ||
    memory.examCount > 0;
  const summary = loaded
    ? `已加载记忆：目标岗位=${memory.targetRole ?? "未设置"}${
        memory.weakPoints.length ? "，薄弱项=" + memory.weakPoints[0] : ""
      }${memory.weakPoints.length > 1 ? ` 等 ${memory.weakPoints.length} 项` : ""}`
    : "上下文已清除，本轮对话从空白记忆开始";

  return (
    <div className="relative" ref={root}>
      <button
        type="button"
        className={
          "inline-flex items-center gap-2 rounded-[10px] border border-[var(--ia-line)] bg-[var(--ia-surface)] px-3 py-1.5 text-xs text-[var(--ia-muted)] transition-colors hover:border-[var(--ia-accent)] " +
          (compact ? "max-w-[280px]" : "max-w-[420px]")
        }
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
          setConfirming(false);
        }}
      >
        <span
          className={"h-2 w-2 shrink-0 rounded-full " + (loaded ? "bg-[var(--ia-green)]" : "bg-[var(--ia-line)]")}
        />
        <span className="truncate">{compact ? summary.slice(0, 18) + "…" : summary}</span>
        <ChevronDown
          size={13}
          className={"shrink-0 text-[var(--ia-muted)] transition-transform " + (open ? "rotate-180" : "")}
        />
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+6px)] z-30 w-[380px] rounded-xl border border-[var(--ia-line)] bg-[var(--ia-surface)] p-4 shadow-[var(--shadow-lg)]">
          <div className="flex items-center gap-2">
            <Brain size={15} className="text-[var(--ia-accent)]" />
            <p className="text-[15px] font-semibold text-[var(--ia-ink)]">已加载的记忆</p>
            <span className="ml-auto text-[11px] text-[var(--ia-muted)]">
              会话内临时上下文 · 可随时清除
            </span>
          </div>
          {loaded ? (
            <div className="mt-3 flex flex-col gap-2.5">
              <MemoryRow label="目标岗位">
                <span className={chipClass + " bg-[var(--ia-tint)] text-[var(--ia-accent)]"}>
                  {memory.targetRole}
                </span>
              </MemoryRow>
              <MemoryRow label="薄弱项">
                {memory.weakPoints.length ? (
                  memory.weakPoints.map((item) => (
                    <span key={item} className={chipClass + " bg-[var(--red-bg)] text-[var(--red)]"}>
                      {item}
                    </span>
                  ))
                ) : (
                  <span className={hintClass}>暂无</span>
                )}
              </MemoryRow>
              <MemoryRow label="当前执行的技能">
                <span className={chipClass + " bg-[var(--ia-tint)] text-[var(--ia-accent)]"}>
                  {memory.currentSkill ?? "未开始"}
                </span>
              </MemoryRow>
              <MemoryRow label="近期考试">
                <span className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
                  {memory.examCount} 次 · 最近一次 3 天前
                </span>
              </MemoryRow>
            </div>
          ) : (
            <p className={"mt-3 rounded-lg bg-[var(--ia-bg)] px-3 py-3 " + hintClass}>
              长期记忆与历史笔记仍会按需引用；本轮的岗位、薄弱项与技能状态会随对话重新累积。
            </p>
          )}
          <div className="mt-3 border-t border-[var(--ia-line)] pt-3">
            {confirming ? (
              <div className="rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] p-3">
                <p className="text-[13px] font-semibold text-[var(--ia-ink)]">清除当前上下文？</p>
                <p className={"mt-1 " + hintClass}>
                  将清空本轮对话的临时上下文与工作记忆展示。长期记忆、笔记与题库不受影响。
                </p>
                <div className="mt-2.5 flex justify-end gap-2">
                  <button
                    type="button"
                    className={ghostBtnClass + " text-xs"}
                    onClick={() => setConfirming(false)}
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--red)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                    onClick={() => {
                      clearAgentContext();
                      setConfirming(false);
                      setOpen(false);
                    }}
                  >
                    <Eraser size={12} />
                    确认清除
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className={ghostBtnClass + " text-xs text-[var(--red)]"}
                  onClick={() => setConfirming(true)}
                >
                  <Eraser size={12} />
                  清除上下文
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
                  <History size={12} />
                  查看会话记忆（长期）
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

// ===== 对话压缩提示 =====

export function CompressionNotice({ rounds }: { rounds: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-[10px] bg-[var(--ia-sand)] px-3.5 py-2.5">
      <button
        type="button"
        className="flex w-full items-center gap-2.5 text-left"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={chipClass + " shrink-0 bg-[var(--ia-amber)] text-[var(--ia-sand)]"}>
          <Sparkles size={11} />
          系统 · 上下文压缩
        </span>
        <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--ia-amber)]">
          前 {rounds} 轮对话已压缩为摘要：用户讨论了 Redis 持久化与主从复制…
        </span>
        <span className="shrink-0 text-xs font-medium text-[var(--ia-amber)]">
          {open ? "收起" : "展开"}
        </span>
      </button>
      {open && (
        <div className="mt-2 border-t border-[var(--ia-line)] pt-2">
          <p className="text-[13px] leading-relaxed text-[var(--ia-amber)]">
            {COMPRESSION_SUMMARY}
          </p>
          <p className={"mt-1.5 " + hintClass + " text-[var(--ia-amber)] opacity-75"}>
            由模型自动压缩 · 原文可在长对话归档中查看
          </p>
        </div>
      )}
    </div>
  );
}

// ===== 上下文控制按钮组 =====

export function ContextControls({ onNewChat }: { onNewChat: () => void }) {
  const [confirming, setConfirming] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <button type="button" className={outlineBtnClass} onClick={onNewChat}>
        <MessageSquarePlus size={13} />
        新建对话
      </button>
      {confirming ? (
        <span className="inline-flex items-center gap-2 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] px-2.5 py-1">
          <span className={hintClass}>清空本轮临时上下文？长期记忆保留。</span>
          <button
            type="button"
            className={hintClass + " text-[var(--ia-muted)]"}
            onClick={() => setConfirming(false)}
          >
            取消
          </button>
          <button
            type="button"
            className="inline-flex items-center gap-1 rounded-md bg-[var(--red)] px-2 py-1 text-xs font-medium text-white hover:opacity-90"
            onClick={() => {
              clearAgentContext();
              setConfirming(false);
            }}
          >
            确认
          </button>
        </span>
      ) : (
        <button type="button" className={ghostBtnClass} onClick={() => setConfirming(true)}>
          <Eraser size={13} />
          清除上下文
        </button>
      )}
      <span className={hintClass + " hidden sm:inline"}>临时上下文随对话累积，可随时重置</span>
    </div>
  );
}

// 供外部监听清除事件（例如重置 mock 消息流）
export { CONTEXT_CLEARED_EVENT };
