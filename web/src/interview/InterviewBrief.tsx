import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, workspaceStorageKey, type DashboardData } from "../api/client";
import { InterviewIcon } from "./InterviewIcon";

const shanghaiDay = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export default function InterviewBrief() {
  const day = shanghaiDay.format(new Date());
  const dismissalKey = workspaceStorageKey("interview-brief-dismissed-" + day);
  const [dismissed, setDismissed] = useState(() => localStorage.getItem(dismissalKey) === "1");
  const [dashboard, setDashboard] = useState<DashboardData>();

  useEffect(() => {
    if (dismissed) return;
    let active = true;
    void api
      .get<DashboardData>("/dashboard")
      .then((data) => {
        if (active) setDashboard(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [dismissed]);

  if (dismissed || !dashboard) return null;
  const pending = (dashboard.today_tasks ?? []).filter(
    (task) => !["DONE", "COMPLETED", "DISMISSED"].includes(task.status?.toUpperCase()),
  );
  const focus = (dashboard.domains ?? [])
    .flatMap(({ name, status, target_level, level }) =>
      status === "BELOW_TARGET" && target_level != null && level != null
        ? [{ name, gap: target_level - level }]
        : [],
    )
    .sort((a, b) => b.gap - a.gap)[0];
  const next = pending[0];
  const recent = (dashboard.recent_activity ?? []).slice(0, 3);

  return (
    <section className="ia-brief" aria-labelledby="ia-brief-title">
      <header>
        <span className="ia-brief-icon">
          <InterviewIcon name="moon" size={17} />
        </span>
        <div>
          <h2 id="ia-brief-title">晨间整理</h2>
          <small>{day} · 依据已保存的学习记录</small>
        </div>
      </header>
      <p>
        {next
          ? `今天还有 ${pending.length} 项任务，先从「${next.title}」开始。`
          : "今天的计划里暂无待办。可以先做一道短练习，或在对话里设定备战目标。"}
        {focus && ` 最近的作答记录显示「${focus.name}」尚未达到岗位目标，可以把这项排在前面。`}
      </p>
      <div className="ia-brief-actions">
        <Link to="/chat?skill=prep">查看备战计划</Link>
        <button
          type="button"
          onClick={() => window.dispatchEvent(new CustomEvent("interview-memory-open"))}
        >
          看已保存的记忆
        </button>
        <button
          type="button"
          onClick={() => {
            localStorage.setItem(dismissalKey, "1");
            setDismissed(true);
          }}
        >
          今天不再显示
        </button>
      </div>
      {recent.length > 0 && (
        <details className="ia-brief-records">
          <summary>查看简报依据</summary>
          <ul>
            {recent.map((record) => (
              <li key={record.kind + ":" + record.id}>
                {record.title}
                {record.score != null && ` · ${record.score} 分`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
