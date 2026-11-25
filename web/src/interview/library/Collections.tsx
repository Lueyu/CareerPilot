import { useState } from "react";
import { api, type ExamHistoryPage, type ExamsData } from "../../api/client";
import StudyMarkdown from "../../components/StudyMarkdown";
import { useT } from "../../i18n";
import {
  PROFILE_FIELDS,
  type ProjectProfile,
  type ProjectRecord,
} from "../../lib/questionWorkshop";
import {
  ButtonLink,
  chatLink,
  dateText,
  matches,
  PageTitle,
  QueryState,
  reveal,
  Row,
  Search,
  type Items,
  useAction,
  useRead,
} from "./ui";

function ProjectForm({
  project,
  onClose,
  onSaved,
}: {
  project: ProjectRecord | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const initial = project?.profile ?? project?.meta?.profile ?? {};
  const [name, setName] = useState(project?.name ?? "");
  const [profile, setProfile] = useState<ProjectProfile>(initial);
  const action = useAction();
  const changes = Object.fromEntries(
    PROFILE_FIELDS.flatMap(([field]) => {
      const value = (profile[field] ?? "").trim();
      return value === (initial[field] ?? "") ? [] : [[field, value]];
    }),
  );

  return (
    <form
      className="ial-form ial-inset"
      ref={reveal}
      onSubmit={(event) => {
        event.preventDefault();
        if (!name.trim()) return;
        void action.run(async () => {
          if (project) await api.patch(`/projects/${project.id}/profile`, { profile: changes });
          else await api.post("/projects", { name: name.trim(), kind: "manual", profile });
          onSaved();
        });
      }}
    >
      <h3>{project ? "编辑 " + project.name : "新增项目"}</h3>
      {!project && (
        <label>
          项目名称
          <input
            required
            maxLength={120}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
      )}
      <div className="ial-fields">
        {PROFILE_FIELDS.map(([field, label, hint]) => (
          <label key={field}>
            {t(label)}
            <textarea
              aria-label={t(label)}
              rows={3}
              maxLength={5000}
              placeholder={t(hint)}
              value={profile[field] ?? ""}
              onChange={(event) =>
                setProfile((value) => ({ ...value, [field]: event.target.value }))
              }
            />
          </label>
        ))}
      </div>
      <QueryState error={action.error} />
      <div className="ial-actions">
        <button
          className="ial-button ial-primary"
          disabled={action.busy || !name.trim() || (!!project && !Object.keys(changes).length)}
        >
          保存项目
        </button>
        <button type="button" className="ial-button" disabled={action.busy} onClick={onClose}>
          取消
        </button>
      </div>
    </form>
  );
}

export function Projects() {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("recent");
  const [editor, setEditor] = useState<ProjectRecord | null>();
  const [removing, setRemoving] = useState<ProjectRecord>();
  const action = useAction();
  const resource = useRead<Items<ProjectRecord>>("/projects");
  const projects = (resource.data?.items ?? []).filter((item) =>
    matches(query, item.name, item.profile?.stack),
  );
  projects.sort((a, b) => (sort === "name" ? a.name.localeCompare(b.name, "zh-CN") : b.id - a.id));

  return (
    <>
      <PageTitle
        title="项目背景库"
        intro={resource.data ? `${resource.data.items.length} 个项目` : undefined}
      />
      <section className="ial-panel">
        <div className="ial-toolbar">
          <Search value={query} onChange={setQuery} placeholder="搜索项目" />
          <select
            aria-label="项目排序"
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="recent">最新加入</option>
            <option value="name">按项目名</option>
          </select>
          <span className="ial-spacer" />
          <button
            className="ial-button ial-primary"
            onClick={() => {
              setEditor(null);
              setRemoving(undefined);
            }}
          >
            新增项目
          </button>
        </div>
        <QueryState
          {...resource}
          retry={resource.reload}
          empty={
            resource.data && !projects.length
              ? query
                ? "没有符合条件的项目。"
                : "暂无项目背景。"
              : undefined
          }
        />
        <ul className="ial-list">
          {projects.map((project) => {
            const profile = project.profile ?? project.meta?.profile;
            return (
              <Row
                icon="projects"
                key={project.id}
                title={project.name}
                subtitle={profile?.stack || profile?.background || undefined}
                status={profile?.responsibilities ? "已补充本人贡献" : "待补本人贡献"}
              >
                <button
                  className="ial-button"
                  onClick={() => {
                    setEditor(project);
                    setRemoving(undefined);
                  }}
                >
                  查看或编辑
                </button>
                <ButtonLink to={chatLink("project", project.name)}>在聊天中打磨</ButtonLink>
                <button
                  className="ial-button"
                  onClick={() => {
                    setRemoving(project);
                    setEditor(undefined);
                  }}
                >
                  归档
                </button>
              </Row>
            );
          })}
        </ul>
        {editor !== undefined && (
          <ProjectForm
            project={editor}
            key={editor?.id ?? "new"}
            onClose={() => setEditor(undefined)}
            onSaved={() => {
              setEditor(undefined);
              resource.reload();
            }}
          />
        )}
        {removing && (
          <div className="ial-inset">
            <p>
              将「{removing.name}」从项目背景库归档？关联的项目记忆将停用，正在运行的分析将取消。
            </p>
            <QueryState error={action.error} />
            <div className="ial-actions">
              <button
                className="ial-button"
                disabled={action.busy}
                onClick={() => setRemoving(undefined)}
              >
                取消
              </button>
              <button
                className="ial-button ial-primary"
                disabled={action.busy}
                onClick={() =>
                  void action.run(async () => {
                    await api.del(`/projects/${removing.id}`);
                    setRemoving(undefined);
                    resource.reload();
                  })
                }
              >
                确认归档
              </button>
            </div>
          </div>
        )}
      </section>
    </>
  );
}

type Attempt = ExamsData["history"][number] & { created_at?: string };
const examStatus: Record<string, string> = {
  CREATED: "待开始",
  ACTIVE: "进行中",
  SUBMITTED: "已交卷",
  TRANSCRIPT_CONFIRMED: "已确认答卷",
  SCORING: "评分中",
  COMPLETED: "已完成",
  ABORTED: "已结束",
  EXPIRED: "已到期",
  SCORING_FAILED: "评分失败",
  SCORING_RETRY_WAIT: "等待重试",
  NEEDS_REVIEW: "待复核",
  FROZEN: "已交卷",
};
const resumable = new Set(["CREATED", "ACTIVE"]);
const reportable = new Set([
  "COMPLETED",
  "SCORING",
  "FROZEN",
  "SCORING_FAILED",
  "SCORING_RETRY_WAIT",
  "NEEDS_REVIEW",
]);

export function History() {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("recent");
  const [additional, setAdditional] = useState<ExamHistoryPage[]>([]);
  const source = useRead<ExamHistoryPage>("/exams/history");
  const action = useAction();
  const last = additional[additional.length - 1] ?? source.data;
  const rows: Attempt[] = [
    ...new Map(
      [source.data, ...additional]
        .flatMap((page) => page?.history ?? [])
        .map((row) => [row.id, row]),
    ).values(),
  ].filter((row) => (!status || row.status === status) && matches(query, row.title));
  if (sort === "score") rows.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));

  return (
    <>
      <PageTitle
        title="历史记录"
        intro={source.data ? `${source.data.total_count} 次` : undefined}
      />
      <section className="ial-panel">
        <div className="ial-toolbar">
          <Search value={query} onChange={setQuery} placeholder="搜索已加载记录" />
          <select
            aria-label="考试状态"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">全部状态</option>
            {Object.entries(examStatus).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="考试排序"
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="recent">从新到旧</option>
            <option value="score">已加载记录按得分</option>
          </select>
          <span className="ial-spacer" />
          <ButtonLink to="/chat/exam" primary>
            去聊天组卷
          </ButtonLink>
        </div>
        <QueryState
          {...source}
          retry={source.reload}
          empty={
            source.data && !rows.length
              ? last?.next_cursor
                ? "已加载的记录中没有匹配结果。"
                : "暂无符合条件的考试记录。"
              : undefined
          }
        />
        <ol className="ial-list">
          {rows.map((row) => (
            <Row
              icon="history"
              key={row.id}
              title={row.title}
              subtitle={[
                dateText(row.completed_at ?? row.started_at ?? row.created_at),
                examStatus[row.status] ?? row.status,
              ]
                .filter(Boolean)
                .join(" · ")}
              status={row.status === "COMPLETED" && row.score != null ? row.score + " 分" : "暂无分数"}
            >
              {reportable.has(row.status) && (
                <ButtonLink to={`/chat/exam/${row.id}/report`}>查看报告</ButtonLink>
              )}
              {resumable.has(row.status) && (
                <ButtonLink to={`/chat/exam/${row.id}`}>继续考试</ButtonLink>
              )}
            </Row>
          ))}
        </ol>
        <QueryState error={action.error} />
        {last?.next_cursor && (
          <div className="ial-footer">
            <button
              className="ial-button"
              disabled={action.busy}
              onClick={() =>
                void action.run(async () => {
                  const page = await api.get<ExamHistoryPage>(
                    `/exams/history?before_id=${last.next_cursor}`,
                  );
                  setAdditional((value) => [...value, page]);
                })
              }
            >
              加载更多
            </button>
          </div>
        )}
      </section>
    </>
  );
}

export type StudyNoteSummary = { id: number; title: string; updated_at: string };
type StudyNote = StudyNoteSummary & {
  version_id: number;
  body: string;
  sources?: { quote?: string }[];
  versions?: { id: number; conflict?: boolean }[];
};
type NoteDraft = { title: string; body: string; version: number };

function NoteEditor({
  id,
  onSaved,
  onClose,
}: {
  id: number;
  onSaved: () => void;
  onClose: () => void;
}) {
  const path = "/study/notes/" + id;
  const resource = useRead<StudyNote>(path);
  const [draft, setDraft] = useState<NoteDraft>();
  const [versionId, setVersionId] = useState<number>();
  const revision = useRead<StudyNote>(versionId ? `${path}/versions/${versionId}` : null);
  const [notice, setNotice] = useState("");
  const action = useAction();
  const note = resource.data;
  const viewing = versionId ? revision.data : note;

  return (
    <section className="ial-open" ref={reveal} aria-label="笔记详情">
      <div className="ial-toolbar">
        <h3>{note?.title ?? "笔记详情"}</h3>
        <span className="ial-spacer" />
        <button className="ial-button" onClick={onClose} disabled={action.busy}>
          关闭笔记
        </button>
      </div>
      <QueryState {...resource} retry={resource.reload} />
      {note && (
        <>
          {(note.versions?.length ?? 0) > 1 && (
            <label className="ial-version">
              查看版本
              <select
                value={versionId ?? ""}
                onChange={(event) => setVersionId(Number(event.target.value) || undefined)}
              >
                <option value="">当前版本</option>
                {note.versions
                  ?.filter((version) => version.id !== note.version_id)
                  .map((version) => (
                    <option key={version.id} value={version.id}>
                      版本 {version.id}
                      {version.conflict ? "（冲突副本）" : ""}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {versionId && <QueryState {...revision} retry={revision.reload} />}
          {viewing && (
            <div className="ial-inset ial-prose">
              <StudyMarkdown text={viewing.body} allowLinks={false} />
              {!!viewing.sources?.length && (
                <details>
                  <summary>原始摘录</summary>
                  {viewing.sources.map((source, index) => (
                    <blockquote key={index}>{source.quote}</blockquote>
                  ))}
                </details>
              )}
            </div>
          )}
          {draft ? (
            <form
              className="ial-form ial-inset"
              ref={reveal}
              onSubmit={(event) => {
                event.preventDefault();
                if (!draft.title.trim() || !draft.body.trim() || draft.version !== note.version_id)
                  return;
                void action.run(async () => {
                  try {
                    await api.patch(path, {
                      expected_version_id: draft.version,
                      title: draft.title,
                      body: draft.body,
                    });
                  } catch (error) {
                    resource.reload();
                    throw error;
                  }
                  setDraft(undefined);
                  resource.reload();
                  onSaved();
                  setNotice("笔记已保存。");
                });
              }}
            >
              <label>
                标题
                <input
                  required
                  maxLength={160}
                  value={draft.title}
                  onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                />
              </label>
              <label>
                笔记正文
                <textarea
                  aria-label="笔记正文"
                  required
                  maxLength={80000}
                  rows={10}
                  value={draft.body}
                  onChange={(event) => setDraft({ ...draft, body: event.target.value })}
                />
              </label>
              {draft.version !== note.version_id && (
                <p role="alert">已有新版本。请对照上方正文，确认后可沿用草稿保存到最新版本。</p>
              )}
              <div className="ial-actions">
                <button
                  className="ial-button ial-primary"
                  disabled={
                    action.busy ||
                    !draft.title.trim() ||
                    !draft.body.trim() ||
                    draft.version !== note.version_id
                  }
                >
                  保存笔记
                </button>
                {draft.version !== note.version_id && (
                  <button
                    className="ial-button"
                    type="button"
                    onClick={() => setDraft({ ...draft, version: note.version_id })}
                  >
                    沿用草稿
                  </button>
                )}
                <button
                  className="ial-button"
                  type="button"
                  disabled={action.busy}
                  onClick={() => setDraft(undefined)}
                >
                  取消编辑
                </button>
              </div>
            </form>
          ) : (
            !versionId && (
              <div className="ial-actions">
                <button
                  className="ial-button"
                  onClick={() => {
                    setNotice("");
                    setDraft({ title: note.title, body: note.body, version: note.version_id });
                  }}
                >
                  编辑笔记
                </button>
                <ButtonLink to={chatLink("review", note.title)}>在聊天中复习</ButtonLink>
              </div>
            )
          )}
        </>
      )}
      <QueryState error={action.error} />
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}

export function Materials() {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("recent");
  const [opened, setOpened] = useState<number>();
  const resource = useRead<Items<StudyNoteSummary>>(
    "/study/notes?" + new URLSearchParams({ q: query.trim() }),
  );
  const notes = [...(resource.data?.items ?? [])];
  if (sort === "name") notes.sort((a, b) => a.title.localeCompare(b.title, "zh-CN"));
  return (
    <>
      <PageTitle title="学习资料" intro={resource.data ? `${notes.length} 份笔记` : undefined} />
      <section className="ial-panel">
        <div className="ial-toolbar">
          <Search value={query} onChange={setQuery} placeholder="搜索笔记" />
          <select
            aria-label="笔记排序"
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="recent">最近更新</option>
            <option value="name">按笔记名称</option>
          </select>
          <span className="ial-spacer" />
          <ButtonLink to={chatLink("review", query.trim())} primary>
            开始学习
          </ButtonLink>
        </div>
        <QueryState
          {...resource}
          retry={resource.reload}
          empty={resource.data && !notes.length ? "没有符合条件的学习笔记。" : undefined}
        />
        <ul className="ial-list">
          {notes.map((note) => (
            <Row
              icon="materials"
              key={note.id}
              title={note.title}
              subtitle="复习笔记"
              status={dateText(note.updated_at)}
            >
              <button
                className="ial-button"
                aria-expanded={opened === note.id}
                onClick={() => setOpened(opened === note.id ? undefined : note.id)}
              >
                打开笔记
              </button>
            </Row>
          ))}
          <Row icon="podcasts" title="通勤播客" subtitle="听题目讲解，按上次进度继续">
            <ButtonLink to="/library/podcasts">打开播客库</ButtonLink>
          </Row>
        </ul>
        {opened !== undefined && (
          <NoteEditor
            id={opened}
            key={opened}
            onSaved={resource.reload}
            onClose={() => setOpened(undefined)}
          />
        )}
      </section>
    </>
  );
}
