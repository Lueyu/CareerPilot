import { useState } from "react";
import { Link } from "react-router-dom";
import { ReadCard } from "./ui";
import { chatUrl, questionSearch, type LibraryInventory } from "./model";

interface BenchmarkRow {
  skill: { key: string; name: string };
  target_level: number;
  current_level: number | null;
  weight_pct: number;
  status: string;
  evidence_coverage: number;
}
interface Benchmark {
  standard: { role: string; level: string; is_seed: boolean; note?: string } | null;
  rows: BenchmarkRow[];
}

function SkillRadar({ rows }: { rows: BenchmarkRow[] }) {
  const ceiling = Math.max(
    4,
    ...rows.map((row) => Math.max(row.target_level, row.current_level ?? 0)),
  );
  const axis = (index: number, ratio: number) => {
    const angle = (index * 2 * Math.PI) / rows.length - Math.PI / 2;
    return [110 + Math.cos(angle) * 62 * ratio, 96 + Math.sin(angle) * 62 * ratio];
  };
  const grid = (ratio: number) => rows.map((_, index) => axis(index, ratio).join(",")).join(" ");
  const target = rows
    .map((row, index) => axis(index, row.target_level / ceiling).join(","))
    .join(" ");
  const observed = rows.map((row, index) =>
    row.current_level == null ? null : axis(index, row.current_level / ceiling),
  );
  return (
    <svg
      className="ia-chat-radar"
      viewBox="0 0 220 198"
      role="img"
      aria-label="实测能力与岗位要求雷达图"
    >
      {[0.25, 0.5, 0.75, 1].map((ratio) => (
        <polygon key={ratio} points={grid(ratio)} fill="none" className="ia-chat-gridline" />
      ))}
      <polygon points={target} className="ia-chat-target" />
      {observed.every((point) => point !== null) && (
        <polygon points={observed.join(" ")} className="ia-chat-series" />
      )}
      {rows.map((row, index) => {
        const [x, y] = axis(index, 1.17);
        const point = observed[index];
        return (
          <g key={row.skill.key}>
            <line
              x1="110"
              y1="96"
              x2={axis(index, 1)[0]}
              y2={axis(index, 1)[1]}
              className="ia-chat-gridline"
            />
            <text x={x} y={y + 4} textAnchor={x < 99 ? "end" : x > 121 ? "start" : "middle"}>
              {row.skill.name.slice(0, 6)}
            </text>
            {point && (
              <circle cx={point[0]} cy={point[1]} r={3.2} className="ia-chat-point">
                <title>
                  {row.skill.name} {row.current_level}
                </title>
              </circle>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function BenchmarkCard() {
  return (
    <ReadCard<Benchmark>
      title="岗位能力快照"
      path="/benchmark"
      render={({ standard, rows }) => {
        if (!standard || !rows.length)
          return <p>此工作区暂无岗位标准。导入一份 JD 后，可以查看其要求和现有能力证据。</p>;
        const measured = rows.flatMap((row) =>
          row.current_level == null ? [] : [{ ...row, level: row.current_level }],
        );
        const scored = measured.filter((row) => row.target_level > 0 && row.weight_pct > 0);
        const weight = scored.reduce((sum, row) => sum + row.weight_pct, 0);
        const ratio = weight
          ? Math.round(
              (100 *
                scored.reduce(
                  (sum, row) => sum + row.weight_pct * Math.min(1, row.level / row.target_level),
                  0,
                )) /
                weight,
            )
          : null;
        const largest = [...measured].sort(
          (a, b) => b.target_level - b.level - (a.target_level - a.level),
        )[0];
        const missing = rows.length - measured.length;
        return (
          <>
            <p>
              {largest && largest.level < largest.target_level
                ? "优先检查「" +
                  largest.skill.name +
                  "」。它在已评估维度里与岗位目标差距最大，适合先用新题验证具体缺口。"
                : measured.length
                  ? "已测维度达到当前岗位目标，可以安排延迟复测，检查换一道题后是否仍能独立作答。"
                  : "目前缺少可比的独立作答证据。可以先做一次正式诊断，再判断应优先补哪项。"}
              {missing > 0 && " 还有 " + missing + " 个维度缺少证据。"}
            </p>
            <div className="ia-chat-metrics">
              {rows.length >= 3 && <SkillRadar rows={rows.slice(0, 6)} />}
              <div className="ia-chat-ratio">
                {ratio === null ? (
                  <strong>待评估</strong>
                ) : (
                  <>
                    <svg viewBox="0 0 84 84" aria-hidden="true">
                      <circle cx="42" cy="42" r="36" className="ia-chat-ratio-track" />
                      <circle
                        cx="42"
                        cy="42"
                        r="36"
                        pathLength="100"
                        strokeDasharray={ratio + " 100"}
                        className="ia-chat-ratio-ring"
                      />
                    </svg>
                    <strong>{ratio}%</strong>
                  </>
                )}
                <small>
                  已测维度匹配度 · {measured.length}/{rows.length}
                </small>
              </div>
            </div>
            {rows.length >= 3 && (
              <div className="ia-chat-chart-legend">
                <span>实测能力</span>
                <span>岗位要求</span>
              </div>
            )}
            <details>
              <summary>各维度与证据</summary>
              <p>匹配度按岗位权重计算，只汇总已评估维度，各项达到要求后按满分计。</p>
              <ul className="ia-chat-data-list">
                {rows.map((row) => (
                  <li key={row.skill.key}>
                    <strong>{row.skill.name}</strong>
                    <span>
                      实测 {row.current_level ?? "未知"} / 目标 {row.target_level} ·{" "}
                      {row.evidence_coverage} 份覆盖
                    </span>
                  </li>
                ))}
              </ul>
            </details>
            <div className="ia-chat-card-foot">
              <small>
                {standard.role} ·{" "}
                {standard.note || (standard.is_seed ? "种子标准" : standard.level)}
              </small>
              <Link to="/competency">查看对标 →</Link>
            </div>
          </>
        );
      }}
    />
  );
}

interface Plan {
  today: string;
  days: {
    date: string;
    items: {
      key: string;
      title: string;
      minutes: number;
      criteria: string;
      question_id?: number | null;
      study_session_id?: number | null;
      status: string;
    }[];
  }[];
}

export function PlanCard({ practice }: { practice: (title: string, questionId?: number) => void }) {
  return (
    <ReadCard<Plan>
      title="每日学习计划"
      path="/learning/daily-plan"
      render={(plan) => {
        const day = plan.days.find((item) => item.date === plan.today);
        const next = plan.days.find(
          (item) =>
            item.date > plan.today && item.items.some((task) => task.status !== "completed"),
        );
        if (!day?.items.length)
          return (
            <p>
              {next
                ? "今天没有排定任务。最近一次安排在 " +
                  next.date +
                  "，可以先在这里做一题，确认接下来需要补什么。"
                : "目前没有排定练习。可以先做一次练习或独立考试，再依据结果安排后续学习。"}
            </p>
          );
        return (
          <>
            <p>
              {plan.today} 有 {day.items.length}{" "}
              项安排。先读验收要求，再做题或复习，便于判断这次学习是否解决了原来的失分点。
            </p>
            <ul className="ia-chat-data-list">
              {day.items.slice(0, 8).map((item) => (
                <li key={item.key}>
                  <div>
                    <strong>{item.title}</strong>
                    <small>
                      {item.minutes} 分钟{item.status === "completed" ? " · 已完成" : ""}
                    </small>
                  </div>
                  {item.criteria && <p>{item.criteria}</p>}
                  {item.study_session_id ? (
                    <Link to={chatUrl(item.study_session_id)}>继续对应对话</Link>
                  ) : (
                    item.status !== "completed" && (
                      <button onClick={() => practice(item.title, item.question_id ?? undefined)}>
                        在聊天内练习
                      </button>
                    )
                  )}
                </li>
              ))}
            </ul>
          </>
        );
      }}
    />
  );
}

export function LibraryInventoryCard({
  inventory,
  select,
  disabled,
}: {
  inventory: LibraryInventory;
  select: (title: string, id: number) => void;
  disabled: boolean;
}) {
  const [page, setPage] = useState(0);
  const { items, total, language } = inventory;
  const english = language === "en";
  const pageSize = 10;
  const start = page * pageSize;
  const end = Math.min(start + pageSize, items.length);
  return (
    <section
      className="ia-chat-output"
      aria-label={english ? "Question bank results" : "题库检索结果"}
    >
      {items.length > 0 && (
        <>
          <ol className="ia-chat-data-list" start={start + 1}>
            {items.slice(start, end).map((row, index) => (
              <li key={row.id}>
                <div>
                  <strong>
                    {start + index + 1}. {row.title}
                  </strong>
                  <small>
                    {[row.difficulty + " / 5", row.topic, ...row.tags].filter(Boolean).join(" · ")}
                  </small>
                </div>
                <button
                  disabled={disabled}
                  aria-label={(english ? "Answer in chat: " : "在聊天内作答：") + row.title}
                  onClick={() => select(row.title, row.id)}
                >
                  {english ? "Answer in chat" : "在聊天内作答"}
                </button>
              </li>
            ))}
          </ol>
          {items.length > pageSize && (
            <div className="ia-chat-actions" aria-label={english ? "Results pages" : "结果分页"}>
              <button disabled={start === 0} onClick={() => setPage(page - 1)}>
                {english ? "Previous" : "上一页"}
              </button>
              <span aria-live="polite">
                {english
                  ? `${start + 1}–${end} of ${total}`
                  : `第 ${start + 1}–${end} 道，共 ${total} 道`}
              </span>
              <button disabled={end === items.length} onClick={() => setPage(page + 1)}>
                {english ? "Next" : "下一页"}
              </button>
            </div>
          )}
        </>
      )}
      {(items.length === 0 || items.length < total) && (
        <Link to="/library/questions">{english ? "Open the question bank" : "打开资料库题库"}</Link>
      )}
    </section>
  );
}

interface BankSearch {
  total: number;
  items: { id: number; title: string; short_code: string; skill?: string; source_type: string }[];
}

export function QuestionSearchCard({
  content,
  select,
}: {
  content: string;
  select: (title: string, id: number) => void;
}) {
  const [input, setInput] = useState(questionSearch(content));
  const [query, setQuery] = useState(input);
  const limit = Math.min(
    12,
    Math.max(1, Number(/(?:抽|找|挑)\s*(\d+)\s*道/.exec(content)?.[1] ?? 5)),
  );
  return (
    <section className="ia-chat-output">
      <form
        className="ia-chat-actions"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(input.trim());
        }}
      >
        <input
          aria-label="题库关键词"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="按题目关键词检索"
        />
        <button>查询</button>
      </form>
      <ReadCard<BankSearch>
        title="题库检索"
        path={"/bank?" + new URLSearchParams({ q: query, limit: String(limit) })}
        render={(data) => (
          <>
            <p>
              {data.total
                ? "按题目标题找到 " +
                  data.total +
                  " 道匹配题。先选择一题自主作答，再根据反馈决定是否查看参考材料。"
                : "题库没有匹配的标题。换一个更短的关键词，或者清空关键词浏览已有题目。"}
            </p>
            <ul className="ia-chat-data-list">
              {data.items.map((row) => (
                <li key={row.id}>
                  <div>
                    <strong>{row.title}</strong>
                    <small>
                      {row.short_code}
                      {row.skill ? " · " + row.skill : ""}
                    </small>
                  </div>
                  <button onClick={() => select(row.title, row.id)}>在聊天内作答</button>
                </li>
              ))}
            </ul>
            <Link to="/library/questions">打开资料库题库</Link>
          </>
        )}
      />
    </section>
  );
}

interface ExamHistory {
  history: {
    id: number;
    title: string;
    status: string;
    score: number | null;
    created_at: string;
  }[];
}
export function HistoryCard({ sessionId }: { sessionId?: number }) {
  return (
    <ReadCard<ExamHistory>
      title="最近考试记录"
      path="/exams/history"
      render={({ history }) => {
        if (!history.length)
          return <p>此工作区还没有考试记录。正式提交后，可以在这里查看结果与逐题反馈。</p>;
        const scored = history
          .filter((item) => item.score !== null && item.status === "COMPLETED")
          .slice(0, 8)
          .reverse();
        const height = 100;
        const points = scored.map((item, index) => {
          const x = 18 + (scored.length === 1 ? 132 : (264 * index) / (scored.length - 1));
          const y = 12 + (Math.min(100, Math.max(0, 100 - (item.score ?? 0))) * height) / 100;
          return { x, y, item };
        });
        return (
          <>
            <p>
              {scored.length >= 2
                ? "折线按考试发生顺序显示已评分记录。考试的范围和难度可能不同，分数变化需要结合报告理解。"
                : "已评分记录还不足以判断趋势。可以打开单次报告，核对哪些题丢分以及原因。"}
            </p>
            {points.length > 0 && (
              <svg
                className="ia-chat-trend"
                viewBox="0 0 300 125"
                role="img"
                aria-label="近期考试分数"
              >
                {[12, 62, 112].map((y) => (
                  <line key={y} x1="18" x2="282" y1={y} y2={y} className="ia-chat-gridline" />
                ))}
                <polyline
                  points={points.map(({ x, y }) => x + "," + y).join(" ")}
                  className="ia-chat-trend-line"
                />
                {points.map(({ x, y, item }) => (
                  <circle key={item.id} cx={x} cy={y} r="4" className="ia-chat-point">
                    <title>
                      {item.title} {item.score} 分
                    </title>
                  </circle>
                ))}
              </svg>
            )}
            <ul className="ia-chat-data-list">
              {history.slice(0, 5).map((item) => (
                <li key={item.id}>
                  <div>
                    <strong>{item.title}</strong>
                    <small>
                      {item.created_at.slice(0, 10)}
                      {item.score !== null ? " · " + item.score + " 分" : ""}
                    </small>
                  </div>
                  {[
                    "COMPLETED",
                    "SCORING",
                    "SCORING_FAILED",
                    "NEEDS_REVIEW",
                    "FROZEN",
                    "SCORING_RETRY_WAIT",
                  ].includes(item.status) && (
                    <Link to={chatUrl(sessionId, item.id)}>在聊天查看结果</Link>
                  )}
                </li>
              ))}
            </ul>
          </>
        );
      }}
    />
  );
}

interface ExamReport {
  title: string;
  status: string;
  score?: number | null;
  gained_points?: number | null;
  total_points?: number | null;
  overall?: { conclusion?: string } | null;
  questions?: {
    title: string;
    order_no: number;
    skill?: string;
    answer?: { points?: number; max_points?: number; verdict?: string } | null;
  }[];
}
const reportStates: Record<string, string> = {
  SCORING: "正在评分。评分完成后重新读取即可查看逐题结果。",
  SCORING_RETRY_WAIT: "评分暂时中断，系统正在等待重试。",
  SCORING_FAILED: "评分失败，目前没有可确认的考试成绩。",
  NEEDS_REVIEW: "评分需要复核，复核前暂无确认成绩。",
  FROZEN: "这份报告已冻结，暂无可用成绩。",
};

export function ReportCard({ id, close }: { id: number; close: () => void }) {
  return (
    <div className="ia-chat-output">
      <p>考试结果取自本次提交的正式报告。可以先看失分最多的题，再打开逐题解释核对依据。</p>
      <ReadCard<ExamReport>
        path={"/exams/" + id + "/report"}
        title="考试结果"
        render={(report) => {
          const open = "/chat/exam/" + id + "/report";
          const low = report.questions?.filter(
            (row) =>
              row.answer?.points != null &&
              row.answer.max_points != null &&
              row.answer.points < row.answer.max_points,
          );
          return (
            <>
              <h4>{report.title}</h4>
              {report.status !== "COMPLETED" ? (
                <p>{reportStates[report.status] ?? "报告尚未完成评分。"}</p>
              ) : (
                <>
                  {report.score != null && (
                    <p className="ia-chat-report-score">
                      <strong>{report.score}</strong> 分
                      {report.gained_points != null && report.total_points != null && (
                        <small>
                          {" "}
                          · {report.gained_points}/{report.total_points} 原始分
                        </small>
                      )}
                    </p>
                  )}
                  <p>
                    {report.overall?.conclusion ||
                      (low?.length
                        ? "有 " +
                          low.length +
                          " 道题仍有失分。建议先从「" +
                          low[0].title +
                          "」的评分项查找漏答或不清楚的地方。"
                        : "报告已存档。打开逐题报告可核对作答及各评分项。")}
                  </p>
                </>
              )}
              <div className="ia-chat-actions">
                <Link to={open}>查看逐题报告 →</Link>
                <button onClick={close}>收起结果</button>
              </div>
            </>
          );
        }}
      />
    </div>
  );
}

interface ProjectList {
  items: { id: number; name: string; description?: string }[];
}
export function ProjectCard({ choose }: { choose: (id: number, title: string) => void }) {
  return (
    <ReadCard<ProjectList>
      path="/projects"
      title="选择已有项目"
      render={({ items }) => (
        <>
          <p>
            {items.length
              ? "选一个已有项目后，可以依据项目记录梳理 STAR 话术。缺失的职责、结果或数字需要你确认。"
              : "资料库还没有项目。可以在聊天里描述一次项目经历，先梳理你亲自做过的部分。"}
          </p>
          <ul className="ia-chat-data-list">
            {items.slice(0, 8).map((item) => (
              <li key={item.id}>
                <strong>{item.name}</strong>
                <button onClick={() => choose(item.id, item.name)}>以此项目继续</button>
              </li>
            ))}
          </ul>
          <Link to="/library/projects">打开项目资料</Link>
        </>
      )}
    />
  );
}
