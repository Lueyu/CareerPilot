import { useState } from "react";
import { ListChecks, Minus, Pause, Pencil, SkipForward, Plus } from "lucide-react";
import { skillSteps } from "./model";
import { ToolCard, chipClass, ghostBtnClass, hintClass, primaryBtnClass, sectionLabelClass } from "./ui";

// 技能进度卡 · 工作记忆可视化（mock）
// 进度百分比、步骤时间线、可实时修改的临时参数、暂停 / 跳过 / 修改参数。

export interface SkillParams {
  questionCount: number;
  level: "基础" | "进阶" | "挑战";
  focus: string;
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
  const [editingParams, setEditingParams] = useState(false);
  const doneCount = skillSteps.filter((step) => step.status === "done").length;
  const percent = Math.round((doneCount / skillSteps.length) * 100);

  return (
    <ToolCard
      icon={<ListChecks size={15} />}
      title="技能进度 · 岗位备战计划"
      detail={
        <span
          className={
            chipClass +
            (status === "running"
              ? " bg-[var(--ia-tint)] text-[var(--ia-accent)]"
              : " bg-[var(--ia-sand)] text-[var(--ia-amber)]")
          }
        >
          {status === "running" ? "进行中" : "已暂停"}
        </span>
      }
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-[var(--ia-muted)]">整体进度</span>
        <span className="ml-auto text-xs font-semibold text-[var(--ia-accent)]">
          {percent}% · {doneCount} / {skillSteps.length} 步
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--ia-line)]">
        <div
          className="h-full rounded-full bg-[var(--ia-accent)] transition-[width] duration-500"
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="flex flex-col">
        {skillSteps.map((step, index) => (
          <div key={step.label} className="flex gap-2.5">
            <div className="relative flex w-5 shrink-0 flex-col items-center">
              <span
                className={
                  "mt-0.5 flex h-[18px] w-[18px] items-center justify-center rounded-full text-[9px] font-semibold " +
                  (step.status === "done"
                    ? "bg-[var(--ia-green)] text-white"
                    : step.status === "current"
                      ? "border-2 border-[var(--ia-accent)] bg-[var(--ia-tint)]"
                      : "bg-[var(--ia-bg)]")
                }
              >
                {step.status === "done" ? "✓" : ""}
              </span>
              {index < skillSteps.length - 1 && (
                <span className="my-0.5 block w-px flex-1 bg-[var(--ia-line)]" />
              )}
            </div>
            <div className="pb-3.5">
              <p
                className={
                  "text-[12.5px] " +
                  (step.status === "current"
                    ? "font-semibold text-[var(--ia-ink)]"
                    : step.status === "done"
                      ? "text-[var(--ia-muted)]"
                      : "text-[var(--ia-muted)]")
                }
              >
                {step.label}
              </p>
              <p
                className={
                  "text-[11px] " +
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
        <p className={sectionLabelClass}>临时参数</p>
        <div className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-xs text-[var(--ia-muted)]">题目数量</span>
          <div className="flex items-center gap-2.5 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-2.5 py-1">
            <button
              type="button"
              aria-label="减少题目数量"
              className="text-[13px] text-[var(--ia-muted)] hover:text-[var(--ia-accent)]"
              onClick={() =>
                onParamsChange({ ...params, questionCount: Math.max(1, params.questionCount - 1) })
              }
            >
              <Minus size={12} />
            </button>
            <span className="min-w-4 text-center text-[13px] font-semibold text-[var(--ia-ink)]">
              {params.questionCount}
            </span>
            <button
              type="button"
              aria-label="增加题目数量"
              className="text-[13px] text-[var(--ia-accent)] hover:opacity-70"
              onClick={() =>
                onParamsChange({ ...params, questionCount: Math.min(20, params.questionCount + 1) })
              }
            >
              <Plus size={12} />
            </button>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-xs text-[var(--ia-muted)]">难度级别</span>
          <div className="flex overflow-hidden rounded-lg bg-[var(--ia-bg)]">
            {(["基础", "进阶", "挑战"] as const).map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => onParamsChange({ ...params, level })}
                className={
                  "px-3 py-1 text-xs " +
                  (params.level === level
                    ? "rounded-lg bg-[var(--ia-surface)] font-medium text-[var(--ia-ink)] shadow-[var(--shadow)]"
                    : "text-[var(--ia-muted)]")
                }
                aria-pressed={params.level === level}
              >
                {level}
              </button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="w-16 shrink-0 text-xs text-[var(--ia-muted)]">侧重方向</span>
          {editingParams ? (
            <select
              aria-label="侧重方向"
              value={params.focus}
              onChange={(event) => {
                onParamsChange({ ...params, focus: event.target.value });
                setEditingParams(false);
              }}
              className="rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-2 py-1 text-xs text-[var(--ia-ink)]"
            >
              {["消息不丢失", "一致性优先", "吞吐优先"].map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          ) : (
            <span className="rounded-lg border border-[var(--ia-line)] bg-[var(--ia-surface)] px-2.5 py-1 text-xs text-[var(--ia-ink)]">
              {params.focus}
            </span>
          )}
        </div>
        <p className={hintClass}>参数修改后从下一步开始生效，不影响已完成的练习。</p>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          className={ghostBtnClass}
          onClick={() => onStatusChange(status === "running" ? "paused" : "running")}
        >
          <Pause size={13} />
          {status === "running" ? "暂停" : "继续"}
        </button>
        <button type="button" className={ghostBtnClass}>
          <SkipForward size={13} />
          跳过本步
        </button>
        <button type="button" className={primaryBtnClass} onClick={() => setEditingParams((v) => !v)}>
          <Pencil size={13} />
          {editingParams ? "完成修改" : "修改参数"}
        </button>
      </div>
      {status === "paused" && (
        <p
          className={
            hintClass + " rounded-lg bg-[var(--ia-sand)] px-3 py-2 text-[var(--ia-amber)]"
          }
        >
          技能已暂停，参数会保留；点击「继续」恢复执行。
        </p>
      )}
    </ToolCard>
  );
}
