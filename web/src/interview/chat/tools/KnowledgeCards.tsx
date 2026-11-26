import { useState } from "react";
import {
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  Network,
  Plus,
} from "lucide-react";
import { initialReminders, mindMap, type MindMapNode, type Reminder } from "./model";
import { ToolCard, chipClass, ghostBtnClass, hintClass, outlineBtnClass } from "./ui";

// ===== 思维导图 =====

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
                chipClass + " bg-[var(--ia-tint)] px-1.5 py-0 text-[10px] text-[var(--ia-accent)]"
              }
            >
              {node.hint}
            </span>
          )}
          {hasChildren &&
            (open ? (
              <ChevronDown size={12} className="opacity-60" />
            ) : (
              <ChevronRight size={12} className="opacity-60" />
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

function mindMapSvg(node: MindMapNode): string {
  const lines: string[] = [];
  const walk = (current: MindMapNode, depth: number) => {
    lines.push(
      `<text x="${12 + depth * 24}" y="${18 * lines.length + 20}" font-size="12">${current.label}</text>`,
    );
    current.children?.forEach((child) => walk(child, depth + 1));
  };
  walk(node, 0);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="${18 * lines.length + 36}" font-family="sans-serif"><rect width="100%" height="100%" fill="#ffffff"/>${lines.join("")}</svg>`;
}

export function MindMapCard() {
  const [openMap, setOpenMap] = useState<Record<string, boolean>>({});
  const [exported, setExported] = useState(false);
  const toggle = (label: string) =>
    setOpenMap((current) => ({ ...current, [label]: !(current[label] ?? false) }));
  return (
    <ToolCard
      icon={<Network size={15} />}
      title="思维导图"
      detail={
        <button
          type="button"
          className={ghostBtnClass + " text-xs"}
          onClick={() => {
            const blob = new Blob([mindMapSvg(mindMap)], { type: "image/svg+xml" });
            const url = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = url;
            anchor.download = "redis-mindmap.svg";
            anchor.click();
            URL.revokeObjectURL(url);
            setExported(true);
            setTimeout(() => setExported(false), 2500);
          }}
        >
          <Download size={12} />
          {exported ? "已导出" : "导出图片"}
        </button>
      }
    >
      <p className="text-sm font-medium text-[var(--ia-ink)]">Redis 高频考点</p>
      <MindMapBranch node={mindMap} depth={0} openMap={openMap} toggle={toggle} />
      <p className={hintClass}>
        点击节点可展开 / 折叠 · 当前展开 {Object.values(openMap).filter(Boolean).length + 1} 层
      </p>
    </ToolCard>
  );
}

// ===== 日历提醒 =====

const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
const NOVEMBER_WEEKS = [
  [26, 27, 28, 29, 30, 31, 1],
  [2, 3, 4, 5, 6, 7, 8],
  [9, 10, 11, 12, 13, 14, 15],
  [16, 17, 18, 19, 20, 21, 22],
  [23, 24, 25, 26, 27, 28, 29],
];
const REMINDER_DAYS = new Set([26, 28, 3, 10, 17]);
const TODAY = 26;
const typeStyles: Record<Reminder["type"], string> = {
  学习: "bg-[var(--ia-mint)] text-[var(--ia-green)]",
  复习: "bg-[var(--ia-sand)] text-[var(--ia-amber)]",
  面试: "bg-[var(--ia-tint)] text-[var(--ia-accent)]",
};

export function CalendarCard() {
  const [reminders, setReminders] = useState<Reminder[]>(initialReminders);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const [type, setType] = useState<Reminder["type"]>("学习");
  const nextId = reminders.reduce((max, item) => Math.max(max, item.id), 0) + 1;

  function toggleDone(id: number) {
    setReminders((current) =>
      current.map((item) => (item.id === id ? { ...item, done: !item.done } : item)),
    );
  }
  function add() {
    if (!title.trim()) return;
    setReminders((current) => [
      ...current,
      { id: nextId, title: title.trim(), time: "今天 21:00", type, done: false },
    ]);
    setTitle("");
    setAdding(false);
  }

  return (
    <ToolCard icon={<CalendarDays size={15} />} title="学习计划与提醒" detail="11 月">
      <div className="rounded-lg bg-[var(--ia-bg)] p-1.5">
        <div className="flex">
          {WEEKDAYS.map((day) => (
            <span
              key={day}
              className="flex h-6 w-[38px] items-center justify-center text-[10.5px] font-medium text-[var(--ia-muted)]"
            >
              {day}
            </span>
          ))}
        </div>
        {NOVEMBER_WEEKS.map((week, weekIndex) => (
          <div key={weekIndex} className="flex">
            {week.map((day) => {
              const inMonth = !(weekIndex === 0 && day > 7);
              const isToday = day === TODAY && inMonth;
              return (
                <span
                  key={`${weekIndex}-${day}`}
                  className={
                    "flex h-[30px] w-[38px] flex-col items-center justify-center gap-0.5 rounded-md " +
                    (isToday ? "border-[1.2px] border-[var(--ia-accent)]" : "")
                  }
                >
                  <span
                    className={
                      "text-[11px] " +
                      (isToday
                        ? "font-semibold text-[var(--ia-accent)]"
                        : inMonth
                          ? "text-[var(--ia-muted)]"
                          : "text-[var(--ia-line)]")
                    }
                  >
                    {day}
                  </span>
                  {REMINDER_DAYS.has(day) && inMonth && (
                    <span className="block h-1 w-1 rounded-full bg-[var(--ia-accent)]" />
                  )}
                </span>
              );
            })}
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
              aria-label={item.done ? "标记为未完成" : "标记为已完成"}
              onClick={() => toggleDone(item.id)}
              className={
                "flex h-4 w-4 items-center justify-center rounded border " +
                (item.done
                  ? "border-[var(--ia-green)] bg-[var(--ia-green)] text-white"
                  : "border-[var(--ia-line)] bg-[var(--ia-surface)]")
              }
            >
              {item.done && <Check size={11} />}
            </button>
            <span className="min-w-0 flex-1">
              <span
                className={
                  "block truncate text-[12.5px] " +
                  (item.done ? "text-[var(--ia-muted)]" : "font-medium text-[var(--ia-ink)]")
                }
              >
                {item.title}
              </span>
              <span className="block text-[11px] text-[var(--ia-muted)]">{item.time}</span>
            </span>
            <span className={chipClass + " " + typeStyles[item.type]}>{item.type}</span>
          </li>
        ))}
      </ul>

      {adding ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--ia-line)] bg-[var(--ia-bg)] px-3 py-2">
          <input
            autoFocus
            value={title}
            maxLength={60}
            placeholder="提醒内容，如：复习 TCP 三次握手"
            onChange={(event) => setTitle(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") add();
            }}
            className="min-w-0 flex-1 bg-transparent text-xs text-[var(--ia-ink)] outline-none placeholder:text-[var(--ia-muted)]"
          />
          <select
            aria-label="提醒类型"
            value={type}
            onChange={(event) => setType(event.target.value as Reminder["type"])}
            className="rounded border border-[var(--ia-line)] bg-[var(--ia-surface)] px-1.5 py-1 text-xs text-[var(--ia-muted)]"
          >
            <option value="学习">学习</option>
            <option value="复习">复习</option>
            <option value="面试">面试</option>
          </select>
          <button type="button" className={outlineBtnClass} disabled={!title.trim()} onClick={add}>
            <Plus size={12} />
            添加
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2">
          <button type="button" className={ghostBtnClass + " text-xs"} onClick={() => setAdding(true)}>
            <Plus size={12} />
            添加提醒
          </button>
          <span className={hintClass}>提醒会同步到你的学习计划</span>
        </div>
      )}
    </ToolCard>
  );
}
