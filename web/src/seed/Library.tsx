import { useEffect, useState, type FormEvent } from "react";
import { request } from "./api";

type Mode = "study" | "interview";
type PracticeQuestion = { id: number; title: string };
type Props = {
  onPractice: (question: PracticeQuestion, mode: Mode) => void;
  onCoach: () => void;
};

type Direction = { name: string; example: string; origin: "preset" | "model" };
type Role = {
  role: string;
  jd: string | null;
  selected_topic: string | null;
  directions: Direction[];
};
type Roles = { selected_role: string | null; roles: Role[] };
type Question = PracticeQuestion & {
  role: string | null;
  topic: string;
  answer: string;
  filename: string | null;
};
type QuestionPage = { questions: Question[]; next_before: number | null };
type ImportSummary = {
  id: number;
  filename: string;
  role: string;
  topic: string;
  status: "FAILED" | "REVIEW" | "APPROVED";
  error: string | null;
  suggested: boolean;
};
type CandidateEdit = { id: number; title: string; answer: string; selected: boolean };
type Candidate = CandidateEdit & {
  status: "DRAFT" | "APPROVED" | "MERGED";
  question_id: number | null;
  source_title: string | null;
  source_answer: string;
  ai_title: boolean;
  ai_answer: string | null;
};
type ImportDraft = ImportSummary & { source_text: string; items: Candidate[] };
type ImportPage = { imports: ImportSummary[]; next_before: number | null };
type Privacy = { region: string; provider: string };
type Selection = { role: string; topic: string };

const messages = {
  load: "正在加载…",
  request: "操作失败，请重试。",
};
const importStatus = { FAILED: "解析失败", REVIEW: "待审核", APPROVED: "已审核" };

function errorMessage(error: unknown) {
  return error instanceof Error && error.message ? error.message : messages.request;
}

function send<T>(path: string, method: "POST" | "PUT", body: unknown) {
  return request<T>(path, { method, body: JSON.stringify(body) });
}

function QuestionCard({
  question,
  onPractice,
}: {
  question: Question;
  onPractice: Props["onPractice"];
}) {
  const [feedback, setFeedback] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!feedback.trim()) return;
    setSending(true);
    setError("");
    setStatus("");
    try {
      await send("/feedback", "POST", { question_id: question.id, text: feedback.trim() });
      setFeedback("");
      setStatus("反馈已发送。");
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setSending(false);
    }
  }

  return (
    <article className="card stack">
      <div>
        <span className="muted">{question.topic}</span>
        <h3>{question.title}</h3>
        {question.filename && <small className="muted">来源 · {question.filename}</small>}
      </div>
      <div className="actions">
        <button type="button" className="primary" onClick={() => onPractice(question, "study")}>
          学习
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => onPractice(question, "interview")}
        >
          面试
        </button>
      </div>
      {question.answer && (
        <details>
          <summary>查看参考答案</summary>
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{question.answer}</p>
        </details>
      )}
      <details>
        <summary>有问题</summary>
        <form className="stack" onSubmit={submit}>
          <label>
            反馈内容
            <textarea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              maxLength={1000}
              required
            />
          </label>
          <small className="muted">反馈会附上这道题和你填写的文字。</small>
          <button type="submit" className="secondary" disabled={sending}>
            {sending ? "发送中…" : "发送反馈"}
          </button>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {status && <p role="status">{status}</p>}
        </form>
      </details>
    </article>
  );
}

function QuestionList({
  role,
  topic,
  revision,
  onPractice,
}: {
  role: string;
  topic: string | null;
  revision: number;
  onPractice: Props["onPractice"];
}) {
  const [page, setPage] = useState<QuestionPage>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);

  function path(before?: number) {
    const params = new URLSearchParams({ role });
    if (topic) params.set("topic", topic);
    if (before) params.set("before", String(before));
    return "/library/questions?" + params;
  }

  useEffect(() => {
    let active = true;
    const params = new URLSearchParams({ role });
    if (topic) params.set("topic", topic);
    request<QuestionPage>("/library/questions?" + params).then(
      (result) => {
        if (active) {
          setPage(result);
          setError("");
        }
      },
      (failure: unknown) => {
        if (active) setError(errorMessage(failure));
      },
    );
    return () => {
      active = false;
    };
  }, [role, topic, revision, retry]);

  async function more(before: number) {
    setLoading(true);
    setError("");
    try {
      const result = await request<QuestionPage>(path(before));
      setPage((previous) => ({
        questions: [...(previous?.questions ?? []), ...result.questions],
        next_before: result.next_before,
      }));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {error && (
        <div role="alert" className="error">
          {error}{" "}
          <button
            type="button"
            className="secondary"
            onClick={() => setRetry((count) => count + 1)}
          >
            重试
          </button>
        </div>
      )}
      {page ? (
        page.questions.length === 0 ? (
          <p className="muted">这里还没有题。试做例题、生成起步题，或导入你自己的面经。</p>
        ) : (
          <div className="stack">
            {page.questions.map((question) => (
              <QuestionCard key={question.id} question={question} onPractice={onPractice} />
            ))}
            {page.next_before !== null && (
              <button
                type="button"
                className="secondary"
                disabled={loading}
                onClick={() => {
                  if (page.next_before !== null) void more(page.next_before);
                }}
              >
                加载更多题目
              </button>
            )}
          </div>
        )
      ) : (
        !error && <p role="status">{messages.load}</p>
      )}
    </>
  );
}

function Imports({ selection, onSaved }: { selection: Selection | null; onSaved: () => void }) {
  const [history, setHistory] = useState<ImportPage>();
  const [draft, setDraft] = useState<ImportDraft>();
  const [privacy, setPrivacy] = useState<Privacy>();
  const [privacyError, setPrivacyError] = useState("");
  const [privacyRetry, setPrivacyRetry] = useState(0);
  const [historyRetry, setHistoryRetry] = useState(0);
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    let active = true;
    request<Privacy>("/privacy").then(
      (result) => {
        if (active) {
          setPrivacy(result);
          setPrivacyError("");
        }
      },
      (failure: unknown) => {
        if (active) setPrivacyError(errorMessage(failure));
      },
    );
    return () => {
      active = false;
    };
  }, [privacyRetry]);

  useEffect(() => {
    let active = true;
    request<ImportPage>("/library/imports").then(
      (result) => {
        if (active) setHistory(result);
      },
      (failure: unknown) => {
        if (active) setError(errorMessage(failure));
      },
    );
    return () => {
      active = false;
    };
  }, [historyRetry]);

  async function operate(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    setStatus("");
    try {
      await action();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy("");
    }
  }

  function show(result: ImportDraft) {
    setDraft(result);
    setHistory((previous) => {
      const entries = previous?.imports ?? [];
      return {
        imports: entries.some((entry) => entry.id === result.id)
          ? entries.map((entry) => (entry.id === result.id ? result : entry))
          : [result, ...entries],
        next_before: previous?.next_before ?? null,
      };
    });
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !selection || !privacy) return;
    const form = event.currentTarget;
    const body = new FormData();
    body.set("file", file);
    body.set("role", selection.role);
    body.set("topic", selection.topic);
    void operate("正在解析文件…", async () => {
      const result = await request<ImportDraft>("/library/imports", { method: "POST", body });
      show(result);
      if (result.status === "FAILED") return;
      form.reset();
      setFile(undefined);
      setStatus("已提取正文，可以审核候选题。");
    });
  }

  function update(id: number, change: Partial<CandidateEdit>) {
    setDraft(
      (previous) =>
        previous && {
          ...previous,
          items: previous.items.map((item) => (item.id === id ? { ...item, ...change } : item)),
        },
    );
  }

  function save(approve: boolean) {
    if (!draft) return;
    const items = draft.items
      .filter((item) => item.status === "DRAFT")
      .map(({ id, title, answer, selected }) => ({
        id,
        title,
        answer,
        selected,
      }));
    void operate(approve ? "正在保存题目…" : "正在保存草稿…", async () => {
      const result = await send<ImportDraft>(
        "/library/imports/" + draft.id + (approve ? "/approve" : "/draft"),
        approve ? "POST" : "PUT",
        { items },
      );
      show(result);
      if (approve) onSaved();
      setStatus(approve ? "已保存勾选题目。其他候选题仍可继续审核。" : "草稿已保存。");
    });
  }

  function suggest(batch: ImportDraft) {
    const items = batch.items
      .filter((item) => item.status === "DRAFT")
      .map(({ id, title, answer, selected }) => ({
        id,
        title,
        answer,
        selected,
      }));
    void operate("正在识别题目…", async () => {
      if (items.length) {
        await send("/library/imports/" + batch.id + "/draft", "PUT", { items });
      }
      show(await send<ImportDraft>("/library/imports/" + batch.id + "/suggest", "POST", {}));
      setStatus("识别完成。请核对原文以及标出的 AI 补充内容。");
    });
  }

  return (
    <section className="panel stack" aria-labelledby="library-import-heading">
      <div>
        <p className="eyebrow">私有题库</p>
        <h2 id="library-import-heading">导入面经</h2>
        <p className="muted">支持文本型 PDF、DOCX 和 Markdown。每次上传一份，文件上限 6 MB。</p>
      </div>
      {privacy ? (
        <p className="muted">
          文件在 {privacy.region} 临时解析，原文件随即清除。系统保留提取的正文和草稿。 只有点击「AI
          识别题目」才会把正文发给 {privacy.provider}。可在账户设置删除账号和资料。
        </p>
      ) : privacyError ? (
        <div className="error" role="alert">
          暂时无法确认资料处理信息。
          <button
            type="button"
            className="secondary"
            onClick={() => setPrivacyRetry((count) => count + 1)}
          >
            重试
          </button>
        </div>
      ) : (
        <p role="status">正在读取资料处理信息…</p>
      )}
      {selection ? (
        <form className="stack" onSubmit={upload}>
          <p>
            归入「{selection.role}」的「{selection.topic}」
          </p>
          <label>
            选择面经文件
            <input
              type="file"
              accept=".pdf,.docx,.md,.markdown"
              disabled={!privacy || !!busy}
              required
              onChange={(event) => setFile(event.target.files?.[0])}
            />
          </label>
          <div className="actions">
            <button type="submit" className="primary" disabled={!privacy || !file || !!busy}>
              上传并预览
            </button>
          </div>
        </form>
      ) : (
        <p className="muted">先选一个练习方向，就可以上传面经。</p>
      )}
      {busy && <p role="status">{busy}</p>}
      {error && (
        <div className="error" role="alert">
          {error}
          {!history && (
            <button
              type="button"
              className="secondary"
              onClick={() => setHistoryRetry((count) => count + 1)}
            >
              重试加载
            </button>
          )}
        </div>
      )}
      {status && <p role="status">{status}</p>}

      {draft && (
        <div className="card stack">
          <div className="row">
            <h3>{draft.filename}</h3>
            <button
              type="button"
              className="secondary"
              disabled={!!busy}
              onClick={() => setDraft(undefined)}
            >
              关闭预览
            </button>
          </div>
          <p className="muted">
            {draft.role} · {draft.topic} · {importStatus[draft.status]}
          </p>
          {draft.error && (
            <p role="alert" className="error">
              {draft.error}
            </p>
          )}
          {!!draft.source_text && (
            <details>
              <summary>查看提取的原文</summary>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  maxHeight: "18rem",
                  overflowY: "auto",
                }}
              >
                {draft.source_text}
              </pre>
            </details>
          )}
          {draft.status === "REVIEW" && !draft.suggested && (
            <div className="actions">
              <button
                type="button"
                className="secondary"
                disabled={!privacy || !!busy}
                onClick={() => suggest(draft)}
              >
                AI 识别题目
              </button>
              {!draft.items.length && (
                <small className="muted">
                  未识别到标准问答。可以查看正文，或让 AI 从正文识别题目。
                </small>
              )}
            </div>
          )}
          {!!draft.items.length && (
            <form
              className="stack"
              onSubmit={(event) => {
                event.preventDefault();
                save(true);
              }}
            >
              {draft.items.map((item, index) => (
                <fieldset
                  key={item.id}
                  className="card stack"
                  disabled={item.status !== "DRAFT" || !!busy}
                >
                  <legend>
                    候选题 {index + 1}
                    {item.status === "APPROVED" ? " · 已入库" : ""}
                    {item.status === "MERGED" ? " · 题库已有，保留原答案" : ""}
                  </legend>
                  <label className="row">
                    <input
                      type="checkbox"
                      checked={item.selected}
                      onChange={(event) => update(item.id, { selected: event.target.checked })}
                    />
                    保存这道题
                  </label>
                  <label>
                    题目
                    <input
                      value={item.title}
                      required
                      minLength={2}
                      maxLength={240}
                      onChange={(event) => update(item.id, { title: event.target.value })}
                    />
                  </label>
                  <label>
                    参考答案
                    <textarea
                      value={item.answer}
                      maxLength={4000}
                      rows={4}
                      onChange={(event) => update(item.id, { answer: event.target.value })}
                    />
                  </label>
                  {item.ai_title && (
                    <small className="muted">题目由 AI 整理，请对照原文核查。</small>
                  )}
                  {item.ai_answer && (
                    <details>
                      <summary>AI 补充的参考思路</summary>
                      <p>{item.ai_answer}</p>
                    </details>
                  )}
                  {(item.source_title || item.source_answer) && (
                    <details>
                      <summary>原文题目与答案</summary>
                      {item.source_title && <p>{item.source_title}</p>}
                      <p style={{ whiteSpace: "pre-wrap" }}>
                        {item.source_answer || "原文未提供答案。"}
                      </p>
                    </details>
                  )}
                </fieldset>
              ))}
              {draft.status === "REVIEW" && (
                <div className="actions">
                  <button
                    type="button"
                    className="secondary"
                    disabled={!!busy}
                    onClick={() => save(false)}
                  >
                    保存草稿
                  </button>
                  <button
                    type="submit"
                    className="primary"
                    disabled={
                      !!busy ||
                      !draft.items.some((item) => item.status === "DRAFT" && item.selected)
                    }
                  >
                    保存勾选题目
                  </button>
                </div>
              )}
            </form>
          )}
        </div>
      )}
      {!!history?.imports.length && (
        <div className="stack">
          <h3>导入记录</h3>
          {history.imports.map((entry) => (
            <div className="card row" key={entry.id}>
              <div>
                <strong>{entry.filename}</strong>
                <p className="muted">
                  {entry.role} · {entry.topic} · {importStatus[entry.status]}
                </p>
              </div>
              <button
                type="button"
                className="secondary"
                disabled={!!busy}
                onClick={() =>
                  void operate("正在打开记录…", async () =>
                    show(await request<ImportDraft>("/library/imports/" + entry.id)),
                  )
                }
              >
                打开记录
              </button>
            </div>
          ))}
          {history.next_before !== null && (
            <button
              type="button"
              className="secondary"
              disabled={!!busy}
              onClick={() =>
                void operate(messages.load, async () => {
                  const result = await request<ImportPage>(
                    "/library/imports?before=" + history.next_before,
                  );
                  setHistory({
                    imports: [...history.imports, ...result.imports],
                    next_before: result.next_before,
                  });
                })
              }
            >
              加载更多记录
            </button>
          )}
        </div>
      )}
    </section>
  );
}

export default function Library({ onPractice, onCoach }: Props) {
  const [roles, setRoles] = useState<Roles>();
  const [roleInput, setRoleInput] = useState("");
  const [jd, setJd] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [retry, setRetry] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [revision, setRevision] = useState(0);
  const active = roles?.roles.find((role) => role.role === roles.selected_role);
  const direction = active?.directions.find((item) => item.name === active.selected_topic);
  const selection = active && direction ? { role: active.role, topic: direction.name } : null;

  useEffect(() => {
    let mounted = true;
    request<Roles>("/library/roles").then(
      (result) => {
        if (!mounted) return;
        const selected = result.roles.find((role) => role.role === result.selected_role);
        setRoles(result);
        setRoleInput(selected?.role ?? "");
        setJd(selected?.jd ?? "");
        setError("");
      },
      (failure: unknown) => {
        if (mounted) setError(errorMessage(failure));
      },
    );
    return () => {
      mounted = false;
    };
  }, [retry]);

  function applyRole(role: Role, resetInputs = true) {
    setRoles((previous) => {
      const entries = previous?.roles ?? [];
      return {
        selected_role: role.role,
        roles: entries.some((entry) => entry.role === role.role)
          ? entries.map((entry) => (entry.role === role.role ? role : entry))
          : [...entries, role],
      };
    });
    if (resetInputs) {
      setRoleInput(role.role);
      setJd(role.jd ?? "");
    }
  }

  async function operate(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    setStatus("");
    try {
      await action();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy("");
    }
  }

  function saveRole(event: FormEvent) {
    event.preventDefault();
    const role = roleInput.trim();
    if (!role) return;
    void operate("正在保存岗位…", async () => {
      applyRole(await send<Role>("/library/role", "PUT", { role, jd: jd.trim() }));
      setShowAll(false);
      setStatus("岗位已保存。");
    });
  }

  return (
    <div className="stack">
      <section className="panel stack" aria-labelledby="library-role-heading">
        <div>
          <p className="eyebrow">练习计划</p>
          <h2 id="library-role-heading">按目标岗位练习</h2>
          <p className="muted">选一个岗位，再挑今天想练的方向。</p>
        </div>
        {!roles &&
          (error ? (
            <button
              type="button"
              className="secondary"
              onClick={() => setRetry((count) => count + 1)}
            >
              重新加载岗位
            </button>
          ) : (
            <p role="status">{messages.load}</p>
          ))}

        {roles && (
          <>
            {!!roles.roles.length && (
              <label>
                已保存的岗位
                <select
                  value={roles.selected_role ?? ""}
                  disabled={!!busy}
                  onChange={(event) => {
                    const role = event.target.value;
                    if (!role) return;
                    void operate("正在切换岗位…", async () => {
                      applyRole(await send<Role>("/library/role", "PUT", { role }));
                      setShowAll(false);
                    });
                  }}
                >
                  <option value="" disabled>
                    选择岗位
                  </option>
                  {roles.roles.map((role) => (
                    <option key={role.role}>{role.role}</option>
                  ))}
                </select>
              </label>
            )}
            <form className="stack" onSubmit={saveRole}>
              <label>
                目标岗位
                <input
                  value={roleInput}
                  onChange={(event) => {
                    setRoleInput(event.target.value);
                    setJd(
                      roles.roles.find((role) => role.role === event.target.value.trim())?.jd ?? "",
                    );
                  }}
                  placeholder="例如 产品运营、后端开发、建筑设计"
                  maxLength={120}
                  required
                />
              </label>
              <label>
                招聘要求（选填）
                <textarea
                  value={jd}
                  onChange={(event) => setJd(event.target.value)}
                  rows={3}
                  maxLength={6000}
                  placeholder="贴入岗位职责或你想重点准备的要求"
                />
              </label>
              <div className="actions">
                <button type="submit" className="primary" disabled={!!busy || !roleInput.trim()}>
                  保存岗位
                </button>
                {active && (
                  <button type="button" className="secondary" disabled={!!busy} onClick={onCoach}>
                    打开学习助手
                  </button>
                )}
              </div>
            </form>
          </>
        )}
        {active && (
          <>
            <div>
              <h3>练习方向</h3>
              <p className="muted">可以直接使用预设。点击 AI 推荐会提交已保存的岗位和招聘要求。</p>
            </div>
            <div className="grid">
              {active.directions.map((item) => (
                <div key={item.name} className="card stack">
                  <div>
                    <strong>{item.name}</strong>
                    {" · "}
                    <span className="muted">{item.origin === "preset" ? "预设" : "AI 推荐"}</span>
                  </div>
                  <p>{item.example}</p>
                  <button
                    type="button"
                    className={item.name === active.selected_topic ? "primary" : "secondary"}
                    aria-pressed={item.name === active.selected_topic}
                    disabled={!!busy}
                    onClick={() =>
                      void operate("正在选择方向…", async () => {
                        applyRole(
                          await send<Role>("/library/direction", "PUT", {
                            role: active.role,
                            topic: item.name,
                          }),
                          false,
                        );
                        setShowAll(false);
                      })
                    }
                  >
                    {item.name === active.selected_topic ? "已选方向" : "选择方向"}
                  </button>
                </div>
              ))}
            </div>
            <div className="actions">
              <button
                type="button"
                className="secondary"
                disabled={
                  !!busy || roleInput.trim() !== active.role || jd.trim() !== (active.jd ?? "")
                }
                onClick={() =>
                  void operate("正在推荐练习方向…", async () => {
                    applyRole(
                      await send<Role>("/library/directions", "POST", { role: active.role }),
                    );
                    setStatus("已更新推荐方向。");
                  })
                }
              >
                AI 推荐方向
              </button>
              {(roleInput.trim() !== active.role || jd.trim() !== (active.jd ?? "")) && (
                <small className="muted">保存修改后可以获取 AI 推荐。</small>
              )}
            </div>
          </>
        )}
        {busy && <p role="status">{busy}</p>}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {status && <p role="status">{status}</p>}
        {selection && (
          <div className="actions">
            <button
              type="button"
              className="primary"
              disabled={!!busy}
              onClick={() =>
                void operate("正在打开例题…", async () => {
                  const question = await send<Question>("/library/example", "POST", selection);
                  setRevision((count) => count + 1);
                  onPractice(question, "study");
                })
              }
            >
              练习例题
            </button>
            <button
              type="button"
              className="secondary"
              disabled={!!busy}
              onClick={() =>
                void operate("正在生成起步题…", async () => {
                  await send<{ questions: Question[] }>("/library/starter", "POST", selection);
                  setRevision((count) => count + 1);
                  setStatus("起步题已保存到我的题库。");
                })
              }
            >
              生成起步题
            </button>
          </div>
        )}
      </section>
      {active && (
        <section className="panel stack" aria-labelledby="library-questions-heading">
          <div className="row">
            <div>
              <h2 id="library-questions-heading">我的题库</h2>
              <p className="muted">
                {active.role} · {showAll || !direction ? "全部方向" : direction.name}
              </p>
            </div>
            {direction && (
              <button
                type="button"
                className="secondary"
                onClick={() => setShowAll((value) => !value)}
              >
                {showAll ? "只看当前方向" : "查看全部方向"}
              </button>
            )}
          </div>
          <QuestionList
            key={JSON.stringify([active.role, showAll ? null : direction?.name])}
            role={active.role}
            topic={showAll ? null : (direction?.name ?? null)}
            revision={revision}
            onPractice={onPractice}
          />
        </section>
      )}
      <Imports selection={selection} onSaved={() => setRevision((count) => count + 1)} />
    </div>
  );
}
