import { useEffect, useRef, useState } from "react";
import { useLocale } from "../../../i18n";
import { useToolCopy } from "./copy";
import { demoSample, type DiffLine } from "./model";
import {
  ToolIcon,
  DemoDisabledButton,
  ToolCard,
  chipClass,
  ghostBtnClass,
  hintClass,
  sectionLabelClass,
} from "./ui";

function CopyButton({ text }: { text: string }) {
  const t = useToolCopy();
  const [state, setState] = useState<"idle" | "copied" | "unavailable">("idle");
  const timer = useRef<number>();

  useEffect(() => {
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  async function copy() {
    if (!navigator.clipboard?.writeText) {
      setState("unavailable");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("unavailable");
      return;
    }
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 1500);
  }

  const label =
    state === "copied"
      ? t("translate.copied")
      : state === "unavailable"
        ? t("translate.copyUnavailable")
        : t("translate.copy");
  return (
    <button type="button" className={ghostBtnClass + " text-xs"} onClick={() => void copy()}>
      {state === "copied" ? (
        <ToolIcon name="check" size={11} />
      ) : (
        <ToolIcon name="copy" size={11} />
      )}
      {label}
    </button>
  );
}

export function TranslateCard() {
  const t = useToolCopy();
  const sample = demoSample(useLocale());
  const [direction, setDirection] = useState<"zh2en" | "en2zh">("zh2en");
  const original = direction === "zh2en" ? sample.translateZh : sample.translateEn;
  const translated = direction === "zh2en" ? sample.translateEn : sample.translateZh;
  return (
    <ToolCard
      icon={<ToolIcon name="arrowRightLeft" size={15} />}
      title={t("card.translate")}
      detail={
        <span className="inline-flex overflow-hidden rounded-lg bg-[var(--ia-bg)]">
          {(["zh2en", "en2zh"] as const).map((value) => (
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
              {value === "zh2en" ? t("translate.zh2en") : t("translate.en2zh")}
            </button>
          ))}
        </span>
      }
    >
      <div className="flex flex-wrap gap-3">
        <div className="min-w-[220px] flex-1 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] p-2.5">
          <div className="mb-1.5 flex items-center gap-2">
            <span className={hintClass}>{t("translate.original")}</span>
            <span className="ml-auto">
              <CopyButton text={original} />
            </span>
          </div>
          <p className="text-xs leading-relaxed text-[var(--ia-muted)]">{original}</p>
        </div>
        <div className="min-w-[220px] flex-1 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] p-2.5">
          <div className="mb-1.5 flex items-center gap-2">
            <span className={hintClass}>{t("translate.translated")}</span>
            <span className="ml-auto">
              <CopyButton text={translated} />
            </span>
          </div>
          <p className="text-xs leading-relaxed text-[var(--ia-ink)]">{translated}</p>
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <p className={sectionLabelClass}>{t("translate.polish")}</p>
        <p className="rounded-lg bg-[var(--ia-bg)] px-3 py-2 text-xs text-[var(--ia-muted)]">
          <span className="mr-2 text-xs text-[var(--ia-muted)]">
            {t("translate.originalLabel")}
          </span>
          {sample.polishBefore.map((part, index) =>
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
          <span className="mr-2 text-xs text-[var(--ia-muted)]">{t("translate.polishLabel")}</span>
          {sample.polishAfter.map((part, index) =>
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

const lineClass: Record<DiffLine["kind"], string> = {
  ins: "bg-[var(--ia-mint)] text-[var(--ia-green)]",
  del: "bg-[var(--red-bg)] text-[var(--red)]",
  norm: "text-[var(--ia-muted)]",
};

function DiffColumn({ heading, lines }: { heading: string; lines: DiffLine[] }) {
  return (
    <div className="min-w-[220px] flex-1 overflow-hidden rounded-lg bg-[var(--ia-bg)]">
      <p className="border-b border-[var(--ia-line)] px-2.5 py-1.5 text-xs font-medium text-[var(--ia-muted)]">
        {heading}
      </p>
      <div className="p-1.5">
        {lines.map((line, index) => (
          <p
            key={index}
            className={
              "flex gap-1.5 rounded px-1.5 py-0.5 font-mono text-xs " + lineClass[line.kind]
            }
          >
            <span aria-hidden="true">
              {line.kind === "ins" ? "+" : line.kind === "del" ? "-" : " "}
            </span>
            <span className="min-w-0 break-words">{line.text}</span>
          </p>
        ))}
      </div>
    </div>
  );
}

export function DiffCard() {
  const t = useToolCopy();
  const sample = demoSample(useLocale());
  return (
    <ToolCard
      icon={<ToolIcon name="gitCompare" size={15} />}
      title={t("card.diff")}
      detail={
        <span className={chipClass + " bg-[var(--ia-bg)] text-[var(--ia-muted)]"}>
          {sample.diffScope}
        </span>
      }
    >
      <div className="flex flex-wrap gap-3">
        <DiffColumn heading={t("diff.old")} lines={sample.diffOld} />
        <DiffColumn heading={t("diff.new")} lines={sample.diffNew} />
      </div>
      <div className="flex flex-wrap items-center gap-2.5">
        <DemoDisabledButton>{t("diff.restore")}</DemoDisabledButton>
        <span className={hintClass}>
          {t("diff.hint", {
            count:
              sample.diffOld.filter((line) => line.kind !== "norm").length +
              sample.diffNew.filter((line) => line.kind !== "norm").length,
          })}
        </span>
      </div>
    </ToolCard>
  );
}
