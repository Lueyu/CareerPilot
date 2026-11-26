import { useState } from "react";
import { ArrowRightLeft, Copy, Check, GitCompare, RotateCcw } from "lucide-react";
import { diffVersions, translatePair } from "./model";
import {
  ToolCard,
  chipClass,
  ghostBtnClass,
  hintClass,
  primaryBtnClass,
  sectionLabelClass,
} from "./ui";

// ===== 翻译润色 =====

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={ghostBtnClass + " text-[11px]"}
      onClick={() => {
        void navigator.clipboard?.writeText(text).catch(() => {});
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? "已复制" : "复制"}
    </button>
  );
}

export function TranslateCard() {
  const [direction, setDirection] = useState<"zh2en" | "en2zh">("zh2en");
  const original = direction === "zh2en" ? translatePair.zh : translatePair.en;
  const translated = direction === "zh2en" ? translatePair.en : translatePair.zh;
  return (
    <ToolCard
      icon={<ArrowRightLeft size={15} />}
      title="翻译润色"
      detail={
        <span className="inline-flex overflow-hidden rounded-lg bg-[var(--ia-bg)]">
          {(
            [
              ["zh2en", "中 → 英"],
              ["en2zh", "英 → 中"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setDirection(value)}
              className={
                "px-3 py-1 text-xs " +
                (direction === value
                  ? "rounded-lg bg-[var(--ia-surface)] font-medium text-[var(--ia-ink)] shadow-[var(--shadow)]"
                  : "text-[var(--ia-muted)]")
              }
              aria-pressed={direction === value}
            >
              {label}
            </button>
          ))}
        </span>
      }
    >
      <div className="flex gap-3">
        <div className="min-w-0 flex-1 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] p-2.5">
          <div className="mb-1.5 flex items-center gap-2">
            <span className={hintClass}>原文 · {direction === "zh2en" ? "中文" : "英文"}</span>
            <span className="ml-auto">
              <CopyButton text={original} />
            </span>
          </div>
          <p className="text-xs leading-relaxed text-[var(--ia-muted)]">{original}</p>
        </div>
        <div className="min-w-0 flex-1 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] p-2.5">
          <div className="mb-1.5 flex items-center gap-2">
            <span className={hintClass}>译文 · {direction === "zh2en" ? "英文" : "中文"}</span>
            <span className="ml-auto">
              <CopyButton text={translated} />
            </span>
          </div>
          <p className="text-xs leading-relaxed text-[var(--ia-ink)]">{translated}</p>
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <p className={sectionLabelClass}>润色对比</p>
        <p className="rounded-lg bg-[var(--ia-bg)] px-3 py-2 text-xs text-[var(--ia-muted)]">
          <span className="mr-2 text-[11px] text-[var(--ia-muted)]">原句</span>
          {translatePair.polish.before.map((part, index) =>
            part.kind === "del" ? (
              <span key={index} className="font-medium text-[var(--red)] line-through">
                {part.text}
              </span>
            ) : (
              <span key={index}>{part.text}</span>
            ),
          )}
        </p>
        <p className="rounded-lg bg-[var(--ia-bg)] px-3 py-2 text-xs text-[var(--ia-muted)]">
          <span className="mr-2 text-[11px] text-[var(--ia-muted)]">润色</span>
          {translatePair.polish.after.map((part, index) =>
            part.kind === "ins" ? (
              <span
                key={index}
                className="rounded bg-[var(--ia-mint)] px-1 font-medium text-[var(--ia-green)]"
              >
                {part.text}
              </span>
            ) : (
              <span key={index}>{part.text}</span>
            ),
          )}
        </p>
      </div>
    </ToolCard>
  );
}

// ===== Git 式版本对比 =====

const lineClass: Record<string, string> = {
  ins: "bg-[var(--ia-mint)] text-[var(--ia-green)]",
  del: "bg-[var(--red-bg)] text-[var(--red)]",
  norm: "text-[var(--ia-muted)]",
};

export function DiffCard() {
  const [restored, setRestored] = useState(false);
  const oldLines = restored
    ? diffVersions.old.map((line) => ({ ...line, kind: "norm" as const }))
    : diffVersions.old;
  return (
    <ToolCard
      icon={<GitCompare size={15} />}
      title="版本对比"
      detail={
        <span className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
          {diffVersions.scope}
        </span>
      }
    >
      <div className="flex gap-3">
        <div className="min-w-0 flex-1 overflow-hidden rounded-lg bg-[var(--ia-bg)]">
          <p className="border-b border-[var(--ia-line)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--ia-muted)]">
            {restored ? "当前生效 · v2" : "旧版本 · v2"}
          </p>
          <div className="p-1.5">
            {oldLines.map((line, index) => (
              <p
                key={index}
                className={
                  "flex gap-1.5 rounded px-1.5 py-0.5 font-mono text-[11px] " + lineClass[line.kind]
                }
              >
                <span aria-hidden="true">
                  {line.kind === "ins" ? "+" : line.kind === "del" ? "-" : " "}
                </span>
                <span className="min-w-0">{line.text}</span>
              </p>
            ))}
          </div>
        </div>
        <div className="min-w-0 flex-1 overflow-hidden rounded-lg bg-[var(--ia-bg)]">
          <p className="border-b border-[var(--ia-line)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--ia-muted)]">
            新版本 · v3
          </p>
          <div className="p-1.5">
            {diffVersions.new.map((line, index) => (
              <p
                key={index}
                className={
                  "flex gap-1.5 rounded px-1.5 py-0.5 font-mono text-[11px] " + lineClass[line.kind]
                }
              >
                <span aria-hidden="true">
                  {line.kind === "ins" ? "+" : line.kind === "del" ? "-" : " "}
                </span>
                <span className="min-w-0">{line.text}</span>
              </p>
            ))}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2.5">
        <button
          type="button"
          className={restored ? ghostBtnClass : primaryBtnClass}
          disabled={restored}
          onClick={() => setRestored(true)}
        >
          {restored ? <Check size={13} /> : <RotateCcw size={13} />}
          {restored ? "已恢复到 v2，可继续编辑" : "恢复到此版本"}
        </button>
        <span className={hintClass}>绿色为新增行，红色为删除行 · 共 4 处变更</span>
      </div>
    </ToolCard>
  );
}
