import { Link, Navigate, useParams } from "react-router-dom";
import { getWorkspace, type ExamHistoryPage } from "../../api/client";
import type { PodcastLibrary } from "../../api/podcasts";
import Questions from "./Questions";
import { History, Materials, Projects, type StudyNoteSummary } from "./Collections";
import InterviewNotes, { type PendingImports, type InterviewSet } from "./InterviewNotes";
import Podcasts from "./Podcasts";
import { ButtonLink, Icon, PageTitle, type Items, useRead } from "./ui";
import { duration, readyEpisodes } from "../../podcasts/library/model";

const sections = [
  {
    id: "questions",
    title: "我的题库",
    description: "题目、标签、难度和收藏",
    unit: "题",
    Component: Questions,
  },
  {
    id: "projects",
    title: "项目背景库",
    description: "结构化项目事实与本人贡献",
    unit: "个项目",
    Component: Projects,
  },
  {
    id: "notes",
    title: "面经素材库",
    description: "导入面经、审核候选题和查看面试题集",
    unit: "条可见记录",
    Component: InterviewNotes,
  },
  {
    id: "history",
    title: "历史记录",
    description: "历次考试、模拟和评分报告",
    unit: "次",
    Component: History,
  },
  {
    id: "materials",
    title: "学习资料",
    description: "复习笔记和通勤播客",
    unit: "份笔记",
    Component: Materials,
  },
  {
    id: "podcasts",
    title: "通勤播客",
    description: "题库讲解与播放记录",
    unit: "集",
    Component: Podcasts,
  },
] as const;

function LibraryHome() {
  const bank = useRead<{ total: number }>("/bank?limit=1");
  const projects = useRead<Items<{ id: number }>>("/projects");
  const pending = useRead<PendingImports>("/bank/imports/pending");
  const sets = useRead<Items<InterviewSet>>("/bank/interview-sets");
  const history = useRead<ExamHistoryPage>("/exams/history");
  const materials = useRead<Items<StudyNoteSummary>>("/study/notes");
  const podcasts = useRead<PodcastLibrary>("/podcasts");
  const counts = {
    questions: bank.error ? undefined : bank.data?.total,
    projects: projects.error ? undefined : projects.data?.items.length,
    notes:
      pending.data && sets.data && !pending.error && !sets.error
        ? pending.data.imports.length + sets.data.items.length
        : undefined,
    history: history.error ? undefined : history.data?.total_count,
    materials: materials.error ? undefined : materials.data?.items.length,
  };
  const ready = readyEpisodes(podcasts.data?.episodes ?? []);
  const resumed = ready.find(
    (episode) =>
      episode.position_seconds > 0 && episode.position_seconds < episode.duration_seconds,
  );
  const episode = resumed ?? ready[0];

  return (
    <>
      <PageTitle title="资料库" intro="管理题目、项目背景、面经、考试记录与学习资料。">
        <ButtonLink to="/library/notes?import=1">
          <Icon name="paperclip" />
          导入面经
        </ButtonLink>
      </PageTitle>
      <div className="ial-library-grid">
        {sections.slice(0, 5).map(
          ({ id, title, description, unit }) =>
            id !== "podcasts" && (
              <Link
                key={id}
                to={`/library/${id}`}
                className={`ial-panel ial-destination ial-destination-${id}`}
              >
                <span className="ial-destination-top">
                  <Icon name={id} tile />
                  <Icon name="arrow" />
                </span>
                <h2>{title}</h2>
                <p>{description}</p>
                <span className="ial-count">
                  <strong>{counts[id] ?? "—"}</strong>
                  <small>{counts[id] == null ? "数量暂不可用" : unit}</small>
                </span>
              </Link>
            ),
        )}
      </div>
      <section className="ial-panel ial-library-podcast">
        <header>
          <h2>
            <Icon name="podcasts" tile />
            通勤播客
          </h2>
          <small>{resumed ? "上次听到 " + duration(resumed.position_seconds) : ""}</small>
        </header>
        {episode && !podcasts.error ? (
          <div className="ial-podcast-home-row">
            <Icon name="podcasts" tile />
            <div>
              <strong>{episode.title}</strong>
              <small>
                {[episode.topic, duration(episode.duration_seconds)].filter(Boolean).join(" · ")}
              </small>
            </div>
            <ButtonLink to={`/library/podcasts?episode=${episode.id}`}>
              {resumed ? "继续听" : "播放这集"}
            </ButtonLink>
          </div>
        ) : (
          <div className="ial-toolbar">
            <small>
              {podcasts.error
                ? "节目库暂不可用"
                : podcasts.loading
                  ? "正在读取节目…"
                  : "暂无可播放节目"}
            </small>
            <ButtonLink to="/library/podcasts">打开播客库</ButtonLink>
          </div>
        )}
      </section>
    </>
  );
}

export default function Library() {
  const { section } = useParams();
  const selected = sections.find((item) => item.id === section);
  if (section && !selected) return <Navigate to="/library" replace />;
  return (
    <div className="ial-page" key={getWorkspace() + ":" + (section ?? "")}>
      {selected ? (
        <>
          <Link className="ial-back" to="/library">
            返回资料库 <Icon name="chevron" />
          </Link>
          <selected.Component />
        </>
      ) : (
        <LibraryHome />
      )}
    </div>
  );
}
