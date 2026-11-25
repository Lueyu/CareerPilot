import type { ExamsData } from "../../api/client";

export type Domain = {
  key: string;
  name: string;
  current: number | null;
  target: number;
  weight: number;
  evidence: number;
  status: string;
  next: string;
};

export const percent = (level: number) => Math.round(level * 20);
export function matchPercent(domains: Domain[]) {
  const measured = domains.filter(
    (domain) => domain.current !== null && domain.target > 0 && domain.weight > 0,
  );
  const weight = measured.reduce((sum, domain) => sum + domain.weight, 0);
  if (!weight) return null;
  return Math.round(
    (measured.reduce(
      (sum, domain) => sum + Math.min(1, (domain.current ?? 0) / domain.target) * domain.weight,
      0,
    ) /
      weight) *
      100,
  );
}

export function Radar({ domains }: { domains: Domain[] }) {
  const count = domains.length;
  if (count < 3) return <p className="ial-state">雷达图至少需要三个岗位能力域。</p>;
  const angle = (index: number) => (index / count) * 2 * Math.PI - Math.PI / 2;
  const point = (index: number, fraction: number) =>
    [
      180 + Math.cos(angle(index)) * 73 * fraction,
      111 + Math.sin(angle(index)) * 73 * fraction,
    ] as const;
  const points = (value: (domain: Domain) => number) =>
    domains.map((domain, index) => point(index, value(domain)).join(",")).join(" ");
  return (
    <svg
      className="ial-radar"
      viewBox="0 0 360 222"
      role="img"
      aria-label="雷达图：你的能力估计与岗位要求。各能力域的具体数值见差距分布。"
    >
      {[0.2, 0.4, 0.6, 0.8, 1].map((value) => (
        <polygon key={value} points={points(() => value)} className="ial-radar-grid" />
      ))}
      {domains.map((domain, index) => {
        const [x, y] = point(index, 1);
        const [labelX, labelY] = point(index, 1.14);
        const cosine = Math.cos(angle(index));
        const label = domain.name.length > 9 ? domain.name.slice(0, 8) + "…" : domain.name;
        return (
          <g key={domain.key}>
            <title>
              {domain.name} · 能力 {domain.current === null ? "未知" : percent(domain.current)} ·
              要求 {percent(domain.target)}
            </title>
            <line x1={180} y1={111} x2={x} y2={y} className="ial-radar-grid" />
            <text
              x={labelX}
              y={labelY}
              textAnchor={cosine > 0.18 ? "start" : cosine < -0.18 ? "end" : "middle"}
              dominantBaseline="middle"
            >
              {label}
            </text>
          </g>
        );
      })}
      <polygon points={points((domain) => domain.target / 5)} className="ial-radar-target" />
      {domains.every((domain) => domain.current !== null) && (
        <polygon
          points={points((domain) => (domain.current ?? 0) / 5)}
          className="ial-radar-current"
        />
      )}
      {domains.map((domain, index) => {
        if (domain.current === null) return null;
        const [x, y] = point(index, domain.current / 5);
        return <circle key={domain.key} cx={x} cy={y} r="3.5" className="ial-radar-dot" />;
      })}
    </svg>
  );
}

export function MatchRing({
  value,
  measured,
  total,
}: {
  value: number | null;
  measured: number;
  total: number;
}) {
  return (
    <div className="ial-ring-box">
      <svg
        viewBox="0 0 112 112"
        role="img"
        aria-label={value == null ? "岗位匹配度未知" : "已评估维度匹配度 " + value + "%"}
      >
        <circle cx="56" cy="56" r="48" pathLength="100" className="ial-ring-track" />
        {value !== null && (
          <circle
            cx="56"
            cy="56"
            r="48"
            pathLength="100"
            className="ial-ring-progress"
            strokeDasharray={`${value} 100`}
            transform="rotate(-90 56 56)"
          />
        )}
        <text x="56" y="57" textAnchor="middle" dominantBaseline="middle">
          {value == null ? "—" : value + "%"}
        </text>
      </svg>
      <strong>岗位匹配度</strong>
      <small>{value === null ? "证据不足" : `${measured} / ${total} 个已评估维度`}</small>
    </div>
  );
}

export type TrendPoint = { id: number; title: string; date: Date; score: number };
export function scoreTimeline(rows: ExamsData["history"]) {
  return rows
    .flatMap((row): TrendPoint[] => {
      if (
        row.status !== "COMPLETED" ||
        !row.completed_at ||
        row.score == null ||
        !Number.isFinite(row.score) ||
        row.score < 0 ||
        row.score > 100
      )
        return [];
      const date = new Date(row.completed_at);
      return Number.isNaN(date.getTime())
        ? []
        : [{ id: row.id, title: row.title, date, score: row.score }];
    })
    .sort((a, b) => a.date.getTime() - b.date.getTime())
    .slice(-8);
}

export function ExamTrend({ points }: { points: TrendPoint[] }) {
  const chartWidth = 420;
  const start = points[0].date.getTime();
  const elapsed = points[points.length - 1].date.getTime() - start;
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" });
  const x = (point: TrendPoint) =>
    elapsed ? 31 + ((chartWidth - 45) * (point.date.getTime() - start)) / elapsed : 31;
  const y = (value: number) => 122 - value * 1.08;
  return (
    <svg
      viewBox={`0 0 ${chartWidth} 156`}
      className="ial-trend"
      role="img"
      aria-label={
        "近期考试分数：" +
        points
          .map((point) => `${dateFormat.format(point.date)} ${point.title} ${point.score} 分`)
          .join("，")
      }
    >
      {[0, 50, 100].map((value) => (
        <g key={value}>
          <text x="2" y={y(value) + 3}>
            {value}
          </text>
          <line x1="31" x2="406" y1={y(value)} y2={y(value)} className="ial-radar-grid" />
        </g>
      ))}
      {points.length > 1 && (
        <polyline
          points={points.map((point) => `${x(point)},${y(point.score)}`).join(" ")}
          className="ial-trend-line"
        />
      )}
      {points.map((point) => (
        <g key={point.id}>
          <title>
            {dateFormat.format(point.date)} · {point.title} · {point.score} 分
          </title>
          <circle cx={x(point)} cy={y(point.score)} r="3.6" className="ial-radar-dot" />
        </g>
      ))}
      <text x="31" y="147">
        {dateFormat.format(points[0].date)}
      </text>
      {elapsed > 0 && (
        <text x="406" y="147" textAnchor="end">
          {dateFormat.format(points[points.length - 1].date)}
        </text>
      )}
    </svg>
  );
}
