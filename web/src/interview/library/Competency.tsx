import { useState } from "react";
import { api, getWorkspace, type BenchmarkData, type ExamHistoryPage } from "../../api/client";
import {
  ButtonLink,
  chatLink,
  dateText,
  examLink,
  PageTitle,
  PanelTitle,
  QueryState,
  type Items,
  useAction,
  useRead,
} from "./ui";
import {
  ExamTrend,
  MatchRing,
  Radar,
  matchPercent,
  percent,
  scoreTimeline,
  type Domain,
} from "./charts";

type Standard = BenchmarkData["standard"];
type BenchmarkRow = Omit<BenchmarkData["rows"][number], "skill_key" | "skill"> & {
  skill: { key: string; name: string };
};
type Benchmark = { standard: Standard | null; rows: BenchmarkRow[] };
type Job = {
  id: number;
  title: string;
  company?: string | null;
  role?: string | null;
  status: string;
  created_at?: string | null;
};
type SavedJob = { title: string; applied: { version: string | null } };

function isLevel(value: number | null): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 5;
}

function JobForm({
  standard,
  latest,
  onClose,
  onSaved,
}: {
  standard?: Standard | null;
  latest?: Job;
  onClose: () => void;
  onSaved: (job: SavedJob) => void;
}) {
  const [title, setTitle] = useState(latest?.title ?? "");
  const [role, setRole] = useState(latest?.role || standard?.role || "");
  const [company, setCompany] = useState(latest?.company ?? "");
  const [body, setBody] = useState("");
  const action = useAction();
  return (
    <form
      className="ial-form ial-inset"
      onSubmit={(event) => {
        event.preventDefault();
        if (!title.trim() || body.trim().length < 30) return;
        void action.run(async () => {
          const result = await api.post<SavedJob>("/benchmark/jd", {
            title: title.trim(),
            role: role.trim() || undefined,
            company: company.trim() || undefined,
            text: body.trim(),
            apply: true,
          });
          onSaved(result);
        });
      }}
    >
      <h3>{latest ? "更新岗位 JD" : "设置目标岗位"}</h3>
      <div className="ial-fields">
        <label>
          JD 标题
          <input
            value={title}
            required
            maxLength={120}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          岗位名称
          <input value={role} maxLength={120} onChange={(event) => setRole(event.target.value)} />
        </label>
        <label>
          公司（可选）
          <input
            value={company}
            maxLength={120}
            onChange={(event) => setCompany(event.target.value)}
          />
        </label>
      </div>
      <label>
        岗位描述
        <textarea
          aria-label="岗位描述"
          value={body}
          required
          minLength={30}
          maxLength={20000}
          rows={6}
          onChange={(event) => setBody(event.target.value)}
        />
      </label>
      <small>
        {latest
          ? "更新会基于粘贴的原文生成新版岗位标准。请重新粘贴完整 JD（至少 30 字）。"
          : "粘贴至少 30 字岗位描述，用于更新岗位要求。"}
      </small>
      <QueryState error={action.error} />
      <div className="ial-actions">
        <button
          className="ial-button ial-primary"
          disabled={action.busy || !title.trim() || body.trim().length < 30}
        >
          保存并更新对标
        </button>
        <button type="button" className="ial-button" disabled={action.busy} onClick={onClose}>
          取消
        </button>
      </div>
    </form>
  );
}

function GapList({ domains }: { domains: Domain[] }) {
  if (!domains.length) return <QueryState empty="暂无岗位要求可比较。" />;
  return (
    <ul className="ial-gap-list">
      {domains.map((domain) => {
        const current = domain.current === null ? null : percent(domain.current);
        const target = percent(domain.target);
        const gap = current === null ? null : target - current;
        return (
          <li key={domain.key}>
            <span className="ial-gap-label">{domain.name}</span>
            <div
              className="ial-gap-track"
              role="img"
              aria-label={`${domain.name}，能力 ${current ?? "未知"}，岗位要求 ${target}，证据 ${domain.evidence} 条`}
            >
              {gap != null && (
                <span className="ial-gap-fill" style={{ width: Math.max(0, gap) + "%" }} />
              )}
            </div>
            <small>{gap == null ? "未评估" : gap <= 0 ? "已达标" : "差 " + gap + " 分"}</small>
          </li>
        );
      })}
    </ul>
  );
}

function WorkspaceCompetency() {
  const benchmark = useRead<Benchmark>("/benchmark");
  const jobs = useRead<Items<Job>>("/benchmark/jds");
  const history = useRead<ExamHistoryPage>("/exams/history");
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState<SavedJob>();
  const standard = benchmark.data?.standard;
  const latest = jobs.data?.items.find((job) => job.status === "APPLIED") ?? jobs.data?.items[0];
  const domains: Domain[] = (benchmark.data?.rows ?? []).flatMap((row) =>
    isLevel(row.target_level)
      ? [
          {
            key: row.skill.key,
            name: row.skill.name,
            target: row.target_level,
            weight: row.weight_pct,
            current:
              isLevel(row.current_level) && row.evidence_coverage > 0 && row.status !== "UNKNOWN"
                ? row.current_level
                : null,
            evidence: row.evidence_coverage,
            status: row.status,
            next: row.next_step,
          },
        ]
      : [],
  );
  const measured = domains.filter((domain) => domain.current !== null).length;
  const gaps = domains
    .filter((domain) => domain.current !== null && domain.target > domain.current)
    .sort(
      (a, b) => (b.target - (b.current ?? 0)) * b.weight - (a.target - (a.current ?? 0)) * a.weight,
    );
  const gapsSet = new Set(gaps.map((domain) => domain.key));
  const recommendations = [
    ...gaps,
    ...domains.filter((domain) => domain.status === "NEEDS_VALIDATION" && !gapsSet.has(domain.key)),
  ].slice(0, 3);
  const fallbackUnknown = domains.find((domain) => domain.current == null);
  const points = scoreTimeline(history.data?.history ?? []);
  const savedActive = saved?.applied.version && saved.applied.version === standard?.version;

  return (
    <div className="ial-page">
      <PageTitle title="能力对标" intro="查看你与目标岗位之间的差距。" />
      <section className="ial-panel ial-job">
        <PanelTitle
          icon="projects"
          title={standard?.role || "目标岗位"}
          detail={standard?.is_seed ? "种子岗位标准" : standard ? "已应用岗位标准" : undefined}
        />
        <QueryState
          {...benchmark}
          retry={benchmark.reload}
          empty={benchmark.data && !standard ? "暂无岗位标准。" : undefined}
        />
        {standard && (
          <p>{[standard.level, standard.note, standard.version].filter(Boolean).join(" · ")}</p>
        )}
        {latest && (
          <p className="ial-muted">
            最近保存的 JD · {latest.title}
            {latest.company ? " · " + latest.company : ""}
            {latest.created_at ? " · " + dateText(latest.created_at) : ""}
          </p>
        )}
        {jobs.error && <QueryState error={"JD 记录读取失败。" + jobs.error} retry={jobs.reload} />}
        {saved && (
          <p role="status">
            {savedActive ? "已应用新版岗位标准。" : "JD 已保存。以当前显示的岗位标准版本为准。"}
          </p>
        )}
        <div className="ial-actions">
          {!editing && (
            <button className="ial-button ial-primary" onClick={() => setEditing(true)}>
              {latest ? "编辑或粘贴 JD" : "设置目标 JD"}
            </button>
          )}
          <ButtonLink to={chatLink("prep", standard?.role || latest?.title)}>
            在聊天中制定备战计划
          </ButtonLink>
        </div>
        {editing && (
          <JobForm
            standard={standard}
            latest={latest}
            onClose={() => setEditing(false)}
            onSaved={(value) => {
              setSaved(value);
              setEditing(false);
              benchmark.reload();
              jobs.reload();
            }}
          />
        )}
      </section>
      <div className="ial-bench-grid">
        <section className="ial-panel">
          <PanelTitle icon="projects" title="能力对比" detail="0–100 分" />
          {domains.length ? (
            <>
              <div className="ial-chart-pair">
                <Radar domains={domains} />
                <MatchRing
                  value={matchPercent(domains)}
                  measured={measured}
                  total={domains.length}
                />
              </div>
              <p className="ial-legend">
                <span>你的能力</span>
                <span>岗位要求</span>按有证据维度的岗位权重加权，单项达标计满。
              </p>
            </>
          ) : (
            <QueryState empty="岗位要求和能力证据就绪后显示能力图。" />
          )}
        </section>
        <section className="ial-panel">
          <PanelTitle icon="chart" title="差距分布" detail="距离岗位要求" />
          <GapList domains={domains} />
          {gaps[0] && (
            <div className="ial-footer">
              <p>
                优先补齐「{gaps[0].name}」。能力 {percent(gaps[0].current ?? 0)} 分，要求{" "}
                {percent(gaps[0].target)} 分。
              </p>
              <ButtonLink to={examLink(gaps[0].name)}>打开专项练习</ButtonLink>
            </div>
          )}
        </section>
        <section className="ial-panel">
          <PanelTitle icon="chart" title="历史趋势" detail="最近完成的考试" />
          <QueryState
            {...history}
            retry={history.reload}
            empty={history.data && !points.length ? "暂无已完成且有分数的考试。" : undefined}
          />
          {!!points.length && (
            <>
              <ExamTrend points={points} />
              <p className="ial-muted">实线连接已完成的考试得分。不同试卷的分数可比性有限。</p>
            </>
          )}
          <p className="ial-muted">当前暂无同一岗位标准的历史匹配度数据。</p>
        </section>
        <section className="ial-panel">
          <PanelTitle icon="sparkles" title="下一步训练" />
          {recommendations.length ? (
            <ul className="ial-list">
              {recommendations.map((domain) => (
                <li className="ial-recommend" key={domain.key}>
                  <strong>{domain.name}</strong>
                  <p>{domain.next || "先复习相关知识，再用独立新题检验。"}</p>
                  <ButtonLink to={chatLink("boost", domain.name)}>在聊天中学习</ButtonLink>
                </li>
              ))}
            </ul>
          ) : fallbackUnknown ? (
            <div className="ial-recommend">
              <strong>先评估「{fallbackUnknown.name}」</strong>
              <p>这一领域还缺少可以用来估计水平的证据。</p>
              <ButtonLink to={examLink(fallbackUnknown.name)}>开始独立作答</ButtonLink>
            </div>
          ) : (
            <QueryState
              empty={measured ? "已有证据的维度暂未显示岗位差距。" : "累积能力证据后显示训练建议。"}
            />
          )}
        </section>
      </div>
    </div>
  );
}

export default function Competency() {
  return <WorkspaceCompetency key={getWorkspace()} />;
}
