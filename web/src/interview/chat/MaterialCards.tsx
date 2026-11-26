import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, workspaceStorageKey } from "../../api/client";
import { errorText, positiveId } from "./model";
import { sessionPath } from "./useConversation";
import { Card } from "./ui";

interface JdResult {
  id: number;
  title: string;
  applied?: { version?: string | null };
}

export function JdCard({ initial, onSaved }: { initial?: string; onSaved: () => void }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState(initial ?? "");
  const [result, setResult] = useState<JdResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  return (
    <div className="ia-chat-output">
      <p>
        把 JD 中的职责和要求保存到工作区，后续就有具体岗位要求可供核对。启用新标准会更新岗位对标。
      </p>
      <Card title="导入目标岗位">
        <form
          className="ia-chat-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy || result || text.trim().length < 30) return;
            const apply = new FormData(event.currentTarget).get("apply") === "on";
            setBusy(true);
            setError("");
            void api
              .post<JdResult>("/benchmark/jd", {
                text: text.trim(),
                title: title.trim() || undefined,
                apply,
              })
              .then((value) => {
                setResult(value);
                onSaved();
                window.dispatchEvent(new Event("interview-context-changed"));
              })
              .catch((err) => setError(errorText(err)))
              .finally(() => setBusy(false));
          }}
        >
          <label>
            岗位名称
            <input
              value={title}
              maxLength={160}
              onChange={(event) => setTitle(event.target.value)}
              disabled={busy || !!result}
              placeholder="例如 AI Agent 工程师"
            />
          </label>
          <label>
            粘贴 JD
            <textarea
              value={text}
              maxLength={20000}
              onChange={(event) => setText(event.target.value)}
              rows={4}
              disabled={busy || !!result}
              placeholder="至少 30 字，包含岗位职责与要求"
            />
          </label>
          <label className="ia-chat-check">
            <input name="apply" type="checkbox" defaultChecked disabled={busy || !!result} />
            启用此 JD 生成的岗位对标标准
          </label>
          <button
            className="ia-chat-primary"
            disabled={busy || !!result || text.trim().length < 30}
          >
            {busy ? "正在保存…" : "保存 JD"}
          </button>
        </form>
        {result && (
          <p role="status">
            已保存「{result.title}」
            {result.applied?.version
              ? "，当前对标版本为 " +
                result.applied.version +
                "。可以对照现有的独立作答证据查看差距。"
              : "。可在分析中引用这份岗位资料。"}
          </p>
        )}
        {error && <p role="alert">{error}</p>}
      </Card>
    </div>
  );
}

interface ImportResult {
  id: number;
  status: string;
  items?: { id: number; status: string; title: string }[];
  meta?: { progress?: { note?: string; step?: number; total?: number }; error?: string };
}

export function MaterialCard({ initial, sessionId }: { initial?: string; sessionId?: number }) {
  const key = workspaceStorageKey("ia-chat-import:" + (sessionId ?? "new"));
  const [existing, setExisting] = useState(() => {
    try {
      return positiveId(localStorage.getItem(key));
    } catch {
      return undefined;
    }
  });
  const [result, setResult] = useState<ImportResult>();
  const [text, setText] = useState(initial ?? "");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = !result || ["UPLOADED", "PARSING"].includes(result.status);
  useEffect(() => {
    if (!existing || !pending) return;
    let active = true;
    async function load() {
      try {
        const data = await api.get<ImportResult>("/bank/imports/" + existing);
        if (active) {
          setResult(data);
          setError("");
        }
      } catch (err) {
        if (active) setError(errorText(err));
      }
    }
    void load();
    const timer = setInterval(() => void load(), 2200);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [existing, pending]);

  function prepare(nextFiles: File[]) {
    if (nextFiles.length > 12 || nextFiles.some((file) => file.size > 12 * 1024 * 1024)) {
      setFiles([]);
      setError("一次可上传最多 12 个文件，每个不超过 12 MB。");
      return;
    }
    setFiles(nextFiles);
    setError("");
  }

  return (
    <div className="ia-chat-output">
      <p>可以粘贴面经或上传文档。系统会提取题目草稿，审核后才进入题库。</p>
      <Card title="导入面经与材料" icon="paperclip">
        <form
          className="ia-chat-form"
          onSubmit={(event) => {
            event.preventDefault();
            if ((!text.trim() && !files.length) || busy || (existing && pending)) return;
            const formElement = event.currentTarget;
            const form = new FormData();
            form.set("pasted_text", text.trim());
            for (const file of files) form.append("files", file);
            setBusy(true);
            setError("");
            void api
              .upload<ImportResult>("/bank/imports/upload", form)
              .then((data) => {
                setResult(data);
                setExisting(data.id);
                try {
                  localStorage.setItem(key, String(data.id));
                } catch {
                  /* the current import is still visible */
                }
                setFiles([]);
                setText("");
                formElement.reset();
              })
              .catch((err) => setError(errorText(err)))
              .finally(() => setBusy(false));
          }}
        >
          <label>
            选择文件
            <input
              type="file"
              multiple
              accept=".pdf,.docx,.md,.txt,.jpg,.jpeg,.png"
              disabled={busy || !!(existing && pending)}
              onChange={(event) => prepare(Array.from(event.target.files ?? []))}
            />
          </label>
          {files.length > 0 && <small>{files.map((file) => file.name).join("、")}</small>}
          <label>
            粘贴面经
            <textarea
              value={text}
              rows={3}
              maxLength={60000}
              onChange={(event) => setText(event.target.value)}
              disabled={busy || !!(existing && pending)}
            />
          </label>
          <button
            className="ia-chat-primary"
            disabled={busy || !!(existing && pending) || (!text.trim() && !files.length)}
          >
            {busy ? "正在上传…" : "开始导入"}
          </button>
        </form>
        {existing && pending && (
          <p role="status">{result?.meta?.progress?.note ?? "正在读取导入状态…"}</p>
        )}
        {existing && error && (
          <button
            onClick={() => {
              setExisting(undefined);
              setResult(undefined);
              try {
                localStorage.removeItem(key);
              } catch {
                /* the current form remains usable */
              }
            }}
          >
            清除当前关联
          </button>
        )}
        {error && <p role="alert">{error}</p>}
        {result?.status === "FAILED" && (
          <p role="alert">
            {result.meta?.progress?.note ||
              result.meta?.error ||
              "导入失败，请检查材料格式后重试。"}
          </p>
        )}
        {result && ["REVIEW", "APPROVED"].includes(result.status) && (
          <>
            <p>
              {result.status === "APPROVED"
                ? "这批材料已完成审核。"
                : "已提取 " +
                  (result.items?.length ?? 0) +
                  " 项草稿，打开资料库可以核对出处并决定是否入库。"}
            </p>
            {Boolean(result.items?.length) && (
              <ul>
                {result.items?.slice(0, 4).map((item) => (
                  <li key={item.id}>{item.title}</li>
                ))}
              </ul>
            )}
            <Link to={"/library/notes?batch=" + result.id}>在资料库查看导入结果 →</Link>
          </>
        )}
      </Card>
    </div>
  );
}

interface NoteJob {
  id: number;
  status: string;
  note_id?: number;
  conflict?: boolean;
  error?: string;
}
export function SaveNote({ sessionId, messageId }: { sessionId: number; messageId: number }) {
  const [job, setJob] = useState<NoteJob>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = job && ["RUNNING", "QUEUED", "PENDING"].includes(job.status);
  const requestId = useRef("interview-message-" + messageId);
  const jobId = job?.id;
  useEffect(() => {
    if (!jobId || !pending) return;
    let active = true;
    const timer = setInterval(
      () =>
        void api
          .get<NoteJob>("/study/note-jobs/" + jobId)
          .then((value) => {
            if (active) {
              setJob(value);
              setError("");
            }
          })
          .catch((err) => {
            if (active) setError(errorText(err));
          }),
      1800,
    );
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [jobId, pending]);

  async function save() {
    if (busy || pending) return;
    setBusy(true);
    setError("");
    try {
      const value =
        job?.status === "FAILED"
          ? await api.post<NoteJob>("/study/note-jobs/" + job.id + "/retry")
          : await api.post<NoteJob>(sessionPath(sessionId) + "/note-jobs", {
              focus_message_id: messageId,
              request_id: requestId.current,
            });
      setJob(value);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="ia-chat-note">
      {job?.status === "SUCCEEDED" ? (
        <Link to={"/library/materials" + (job.note_id ? "?note=" + job.note_id : "")}>
          {job.conflict ? "查看笔记更新草稿" : "查看已保存的学习笔记"}
        </Link>
      ) : (
        <button disabled={busy || !!pending} onClick={() => void save()}>
          {pending || busy
            ? "正在整理笔记…"
            : job?.status === "FAILED"
              ? "重试整理笔记"
              : "保存为学习笔记"}
        </button>
      )}
      {(error || job?.error) && <span role="alert">{error || job?.error}</span>}
    </span>
  );
}
