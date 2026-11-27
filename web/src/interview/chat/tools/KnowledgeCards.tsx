import { useEffect, useRef, useState } from "react";
import { useLocale } from "../../../i18n";
import { useToolCopy } from "./copy";
import { demoSample, type MindMapNode, type Reminder } from "./model";
import {
  ToolIcon,
  DemoDisabledButton,
  ToolCard,
  chipClass,
  ghostBtnClass,
  hintClass,
  outlineBtnClass,
} from "./ui";

function MindMapBranch({
  node,
  depth,
  openMap,
  toggle,
}: {
  node: MindMapNode;
  depth: number;
  openMap: Record<string, boolean>;
  toggle: (label: string) => void;
}) {
  const hasChildren = !!node.children?.length;
  const open = openMap[node.label] ?? depth === 0;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5" style={{ paddingLeft: depth * 18 }}>
        {depth > 0 && (
          <span className="flex h-5 items-center" aria-hidden="true">
            <span className="block h-3 w-px bg-[var(--ia-line)]" />
            <span className="block h-px w-2.5 bg-[var(--ia-line)]" />
          </span>
        )}
        <button
          type="button"
          onClick={() => hasChildren && toggle(node.label)}
          className={
            "inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs transition-colors " +
            (depth === 0
              ? "bg-[var(--ia-accent)] font-medium text-white"
              : hasChildren
                ? "border border-[var(--ia-line)] bg-[var(--ia-surface)] text-[var(--ia-ink)] hover:border-[var(--ia-accent)]"
                : "bg-[var(--ia-bg)] text-[var(--ia-muted)]")
          }
          aria-expanded={hasChildren ? open : undefined}
        >
          {node.label}
          {node.hint && !open && (
            <span
              className={
                chipClass + " bg-[var(--ia-tint)] px-1.5 py-0 text-[11px] text-[var(--ia-accent)]"
              }
            >
              {node.hint}
            </span>
          )}
          {hasChildren &&
            (open ? (
              <ToolIcon name="chevronDown" size={12} />
            ) : (
              <ToolIcon name="chevronRight" size={12} />
            ))}
        </button>
      </div>
      {hasChildren && open && (
        <div className="flex flex-col gap-1.5">
          {node.children!.map((child) => (
            <MindMapBranch
              key={child.label}
              node={child}
              depth={depth + 1}
              openMap={openMap}
              toggle={toggle}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function escapeXml(value: string) {
  return value.replace(/[<>&"']/g, (ch) => {
    switch (ch) {
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case "&":
        return "&amp;";
      case '"':
        return "&quot;";
      default:
        return "&apos;";
    }
  });
}

function mindMapSvg(node: MindMapNode): string {
  const lines: string[] = [];
  const walk = (current: MindMapNode, depth: number) => {
    lines.push(
      `<text x="${12 + depth * 24}" y="${18 * lines.length + 20}" font-size="12">${escapeXml(current.label)}</text>`,
    );
    current.children?.forEach((child) => walk(child, depth + 1));
  };
  walk(node, 0);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="${18 * lines.length + 36}" font-family="sans-serif"><rect width="100%" height="100%" fill="#ffffff"/>${lines.join("")}</svg>`;
}

export function MindMapCard() {
  const t = useToolCopy();
  const locale = useLocale();
  const sample = demoSample(locale);
  const [openMap, setOpenMap] = useState<Record<string, boolean>>({});
  const [exported, setExported] = useState(false);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((id) => window.clearTimeout(id));
  }, []);

  const toggle = (label: string) =>
    setOpenMap((current) => ({ ...current, [label]: !(current[label] ?? false) }));

  function exportSvg() {
    const url = URL.createObjectURL(
      new Blob([mindMapSvg(sample.mindMap)], { type: "image/svg+xml" }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "demo-mindmap.svg";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    timers.current.push(window.setTimeout(() => URL.revokeObjectURL(url), 1000));
    setExported(true);
    timers.current.push(window.setTimeout(() => setExported(false), 2000));
  }

  return (
    <ToolCard
      icon={<ToolIcon name="network" size={15} />}
      title={t("card.mindmap")}
      detail={
        <button type="button" className={ghostBtnClass + " text-xs"} onClick={exportSvg}>
          <ToolIcon name="download" size={12} />
          {exported ? t("mindmap.exported") : t("mindmap.export")}
        </button>
      }
    >
      <p className="text-sm font-medium text-[var(--ia-ink)]">{sample.mindMapTopic}</p>
      <MindMapBranch node={sample.mindMap} depth={0} openMap={openMap} toggle={toggle} />
      <p className={hintClass}>
        {t("mindmap.hint")} ·{" "}
        {t("mindmap.layers", { count: Object.values(openMap).filter(Boolean).length })}
      </p>
    </ToolCard>
  );
}

function buildMonthWeeks(now: Date): number[][] {
  const year = now.getFullYear();
  const month = now.getMonth();
  const offset = (new Date(year, month, 1).getDay() + 6) % 7;
  const days = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = Array.from({ length: offset }, () => null);
  for (let day = 1; day <= days; day += 1) cells.push(day);
  while (cells.length % 7 !== 0) cells.push(null);
  const weeks: number[][] = [];
  for (let index = 0; index < cells.length; index += 7) {
    weeks.push(cells.slice(index, index + 7).map((cell) => cell ?? 0));
  }
  return weeks;
}

export function CalendarCard() {
  const t = useToolCopy();
  const locale = useLocale();
  const sample = demoSample(locale);
  const [reminders, setReminders] = useState<Reminder[]>(sample.reminders);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [type, setType] = useState<Reminder["type"]>("study");

  const now = new Date();
  const weeks = buildMonthWeeks(now);
  const today = now.getDate();
  const monthLabel = new Intl.DateTimeFormat(locale === "en" ? "en-US" : "zh-CN", {
    year: "numeric",
    month: "long",
  }).format(now);
  const sampleDays = new Set([today, Math.min(today + 2, 31), Math.min(today + 7, 31)]);
  const typeStyles: Record<Reminder["type"], string> = {
    study: "bg-[var(--ia-mint)] text-[var(--ia-green)]",
    review: "bg-[var(--ia-sand)] text-[var(--ia-amber)]",
    interview: "bg-[var(--ia-tint)] text-[var(--ia-accent)]",
  };

  function add() {
    if (!title.trim()) return;
    setReminders((current) => [
      ...current,
      {
        id: current.reduce((max, item) => Math.max(max, item.id), 0) + 1,
        title: title.trim(),
        time: sample.reminders[0].time,
        type,
        done: false,
      },
    ]);
    setTitle("");
    setAdding(false);
  }

  return (
    <ToolCard
      icon={<ToolIcon name="calendarDays" size={15} />}
      title={t("calendar.title")}
      detail={monthLabel}
    >
      <div className="rounded-lg bg-[var(--ia-bg)] p-1.5">
        <div className="flex">
          {sample.weekdays.map((day, index) => (
            <span
              key={`${day}-${index}`}
              className="flex h-6 flex-1 items-center justify-center text-xs font-medium text-[var(--ia-muted)]"
            >
              {day}
            </span>
          ))}
        </div>
        {weeks.map((week, weekIndex) => (
          <div key={weekIndex} className="flex">
            {week.map((day, dayIndex) => (
              <span
                key={`${weekIndex}-${dayIndex}`}
                className={
                  "flex h-[30px] flex-1 flex-col items-center justify-center gap-0.5 rounded-md " +
                  (day === today ? "border-[1.2px] border-[var(--ia-accent)]" : "")
                }
              >
                <span
                  className={
                    "text-xs " +
                    (day === today
                      ? "font-semibold text-[var(--ia-accent)]"
                      : day
                        ? "text-[var(--ia-muted)]"
                        : "text-[var(--ia-line)]")
                  }
                >
                  {day || ""}
                </span>
                {day > 0 && sampleDays.has(day) && (
                  <span className="block h-1 w-1 rounded-full bg-[var(--ia-accent)]" />
                )}
              </span>
            ))}
          </div>
        ))}
      </div>

      <ul className="flex flex-col gap-1.5">
        {reminders.map((item) => (
          <li
            key={item.id}
            className="flex items-center gap-2.5 rounded-lg bg-[var(--ia-bg)] px-3 py-2"
          >
            <button
              type="button"
              aria-label={item.done ? t("calendar.markUndone") : t("calendar.markDone")}
              onClick={() =>
                setReminders((current) =>
                  current.map((entry) =>
                    entry.id === item.id ? { ...entry, done: !entry.done } : entry,
                  ),
                )
              }
              className={
                "flex h-4 w-4 items-center justify-center rounded border " +
                (item.done
                  ? "border-[var(--ia-green)] bg-[var(--ia-green)] text-white"
                  : "border-[var(--ia-line)] bg-[var(--ia-surface)]")
              }
            >
              {item.done && <ToolIcon name="check" size={11} />}
            </button>
            <span className="min-w-0 flex-1">
              <span
                className={
                  "block truncate text-xs " +
                  (item.done ? "text-[var(--ia-muted)]" : "font-medium text-[var(--ia-ink)]")
                }
              >
                {item.title}
              </span>
              <span className="block text-xs text-[var(--ia-muted)]">{item.time}</span>
            </span>
            <span className={chipClass + " " + typeStyles[item.type]}>
              {item.type === "study"
                ? t("calendar.study")
                : item.type === "review"
                  ? t("calendar.review")
                  : t("calendar.interview")}
            </span>
          </li>
        ))}
      </ul>

      {adding ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] px-3 py-2">
          <input
            autoFocus
            value={title}
            maxLength={60}
            placeholder={t("calendar.placeholder")}
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") add();
            }}
            className="min-w-0 flex-1 bg-transparent text-xs text-[var(--ia-ink)] outline-none placeholder:text-[var(--ia-muted)]"
          />
          <select
            aria-label={t("calendar.type")}
            value={type}
            onChange={(event) => setType(event.target.value as Reminder["type"])}
            className="rounded border border-[var(--ia-line)] bg-[var(--ia-surface)] px-1.5 py-1 text-xs text-[var(--ia-muted)]"
          >
            <option value="study">{t("calendar.study")}</option>
            <option value="review">{t("calendar.review")}</option>
            <option value="interview">{t("calendar.interview")}</option>
          </select>
          <button type="button" className={outlineBtnClass} disabled={!title.trim()} onClick={add}>
            <ToolIcon name="plus" size={12} />
            {t("calendar.addTitle")}
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={ghostBtnClass + " text-xs"}
            onClick={() => setAdding(true)}
          >
            <ToolIcon name="plus" size={12} />
            {t("calendar.add")}
          </button>
          <DemoDisabledButton>{t("calendar.syncHint")}</DemoDisabledButton>
        </div>
      )}
    </ToolCard>
  );
}
