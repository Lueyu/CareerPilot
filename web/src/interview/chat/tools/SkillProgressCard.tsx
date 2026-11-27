import { useState } from "react";
import { useLocale } from "../../../i18n";
import { useToolCopy } from "./copy";
import { demoSample } from "./model";
import {
  ToolIcon,
  DemoDisabledButton,
  ToolCard,
  chipClass,
  ghostBtnClass,
  hintClass,
  primaryBtnClass,
  sectionLabelClass,
} from "./ui";

// 用索引保存演示参数，语言切换时不会出现中文枚举值。
export interface SkillParams {
  questionCount: number;
  levelIndex: number;
  focusIndex: number;
}

export function defaultSkillParams(): SkillParams {
  return { questionCount: 5, levelIndex: 1, focusIndex: 0 };
}

export function SkillProgressCard({
  params,
  onParamsChange,
  status,
  onStatusChange,
}: {
  params: SkillParams;
  onParamsChange: (next: SkillParams) => void;
  status: "running" | "paused";
  onStatusChange: (next: "running" | "paused") => void;
}) {
  const t = useToolCopy();
  const sample = demoSample(useLocale());
  const [editingParams, setEditingParams] = useState(false);
  const doneCount = sample.skillSteps.filter((step) => step.status === "done").length;
  const percent = Math.round((doneCount / sample.skillSteps.length) * 100);
  const level = sample.levels[params.levelIndex] ?? sample.levels[0];
  const focus = sample.focusOptions[params.focusIndex] ?? sample.focusOptions[0];

  return (
    <ToolCard
      icon={<ToolIcon name="listChecks" size={15} />}
      title={t("card.skill")}
      detail={
        <span
          className={
            chipClass +
            (status === "running"
              ? " bg-[var(--ia-tint)] text-[var(--ia-accent)]"
              : " bg-[var(--ia-sand)] text-[var(--ia-amber)]")
          }
        >
          {status === "running" ? t("skill.running") : t("skill.paused")}
        </span>
      }
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-[var(--ia-muted)]">{t("skill.progress")}</span>
        <span className="ml-auto text-xs font-semibold text-[var(--ia-accent)]">
          {t("skill.percent", { percent, done: doneCount, total: sample.skillSteps.length })}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--ia-line)]">
        <div
          className="h-full rounded-full bg-[var(--ia-accent)] transition-[width] duration-500"
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="flex flex-col">
        {sample.skillSteps.map((step, index) => (
          <div key={step.label} className="flex gap-2.5">
            <div className="relative flex w-5 shrink-0 flex-col items-center">
              <span
                className={
                  "mt-0.5 flex h-[18px] w-[18px] items-center justify-center rounded-full text-[10px] font-semibold " +
                  (step.status === "done"
                    ? "bg-[var(--ia-green)] text-white"
                    : step.status === "current"
                      ? "border-2 border-[var(--ia-accent)] bg-[var(--ia-tint)]"
                      : "bg-[var(--ia-bg)]")
                }
              >
                {step.status === "done" ? "✓" : ""}
              </span>
              {index < sample.skillSteps.length - 1 && (
                <span className="my-0.5 block w-px flex-1 bg-[var(--ia-line)]" />
              )}
            </div>
            <div className="pb-3.5">
              <p
                className={
                  "text-[12.5px] " +
                  (step.status === "current"
                    ? "font-semibold text-[var(--ia-ink)]"
                    : "text-[var(--ia-muted)]")
                }
              >
                {step.label}
              </p>
              <p
                className={
                  "text-xs " +
                  (step.status === "current" ? "text-[var(--ia-accent)]" : "text-[var(--ia-muted)]")
                }
              >
                {step.meta}
              </p>
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-2.5 rounded-lg bg-[var(--ia-bg)] p-3">
        <p className={sectionLabelClass}>{t("skill.params")}</p>
        <div className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-xs text-[var(--ia-muted)]">{t("skill.count")}</span>
          <div className="flex items-center gap-2.5 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-2.5 py-1">
            <button
              type="button"
              aria-label={t("skill.decrease")}
              className="text-[13px] text-[var(--ia-muted)] hover:text-[var(--ia-accent)]"
              onClick={() =>
                onParamsChange({ ...params, questionCount: Math.max(1, params.questionCount - 1) })
              }
            >
              <ToolIcon name="minus" size={12} />
            </button>
            <span className="min-w-4 text-center text-[13px] font-semibold text-[var(--ia-ink)]">
              {params.questionCount}
            </span>
            <button
              type="button"
              aria-label={t("skill.increase")}
              className="text-[13px] text-[var(--ia-accent)] hover:opacity-70"
              onClick={() =>
                onParamsChange({ ...params, questionCount: Math.min(20, params.questionCount + 1) })
              }
            >
              <ToolIcon name="plus" size={12} />
            </button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="w-16 shrink-0 text-xs text-[var(--ia-muted)]">{t("skill.level")}</span>
          <div className="flex flex-wrap overflow-hidden rounded-lg bg-[var(--ia-bg)]">
            {sample.levels.map((item, index) => (
              <button
                key={item}
                type="button"
                onClick={() => onParamsChange({ ...params, levelIndex: index })}
                className={
                  "px-3 py-1 text-xs " +
                  (params.levelIndex === index
                    ? "rounded-lg bg-[var(--ia-surface)] font-medium text-[var(--ia-ink)] shadow-[var(--shadow)]"
                    : "text-[var(--ia-muted)]")
                }
                aria-pressed={params.levelIndex === index}
              >
                {item}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="w-16 shrink-0 text-xs text-[var(--ia-muted)]">{t("skill.focus")}</span>
          {editingParams ? (
            <select
              aria-label={t("skill.focus")}
              value={params.focusIndex}
              onChange={(event) => {
                onParamsChange({ ...params, focusIndex: Number(event.target.value) });
                setEditingParams(false);
              }}
              className="rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-2 py-1 text-xs text-[var(--ia-ink)]"
            >
              {sample.focusOptions.map((item, index) => (
                <option key={item} value={index}>
                  {item}
                </option>
              ))}
            </select>
          ) : (
            <span className="rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-2.5 py-1 text-xs text-[var(--ia-ink)]">
              {focus}
            </span>
          )}
        </div>
        <p className={hintClass}>
          {t("skill.paramHint")} {level}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className={ghostBtnClass}
          onClick={() => onStatusChange(status === "running" ? "paused" : "running")}
        >
          <ToolIcon name="pause" size={13} />
          {status === "running" ? t("skill.pause") : t("skill.resume")}
        </button>
        <DemoDisabledButton>
          <ToolIcon name="skipForward" size={13} />
          {t("skill.skip")}
        </DemoDisabledButton>
        <button
          type="button"
          className={primaryBtnClass}
          onClick={() => setEditingParams((value) => !value)}
        >
          <ToolIcon name="pencil" size={13} />
          {editingParams ? t("skill.finishEdit") : t("skill.editParams")}
        </button>
      </div>
      {status === "paused" && (
        <p
          className={hintClass + " rounded-lg bg-[var(--ia-sand)] px-3 py-2 text-[var(--ia-amber)]"}
        >
          {t("skill.pausedHint")}
        </p>
      )}
    </ToolCard>
  );
}
