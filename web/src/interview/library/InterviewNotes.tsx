import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, type ImportReview } from "../../api/client";
import {
  chatLink,
  ButtonLink,
  Icon,
  matches,
  PageTitle,
  QueryState,
  reveal,
  Reference,
  Row,
  Search,
  type Items,
  useAction,
  useRead,
} from "./ui";

type ReviewItem = ImportReview["items"][number] & {
  has_reviewable_answer: boolean;
  review_revision: number;
  review_state?: "idle" | "running" | "failed";
  review_error?: string | null;
};
type Batch = Omit<ImportReview, "items" | "meta"> & {
  items: ReviewItem[];
  meta?: ImportReview["meta"] & { pasted_text?: string | null };
};
export type PendingImports = { imports: Batch[] };
export type InterviewSet = {
  id: string;
  import_id: number;
  title: string;
  project_name: string;
  questions?: { id?: number; item_id: number; title: string }[];
};
type ReviewKind = "approve" | "revise" | "reject";
type ReviewOperation = {
  action: ReviewKind;
  feedback?: string;
  revision: number;
  request_id: string;
};
const statuses: Record<string, string> = {
  UPLOADED: "已接收",
  PARSING: "解析中",
  REVIEW: "待审核",
  FAILED: "解析失败",
  APPROVED: "已完成",
  DRAFT: "待审核",
  MERGED: "已合并",
  REJECTED: "已驳回",
};

function ImportForm({
  onClose,
  onQueued,
}: {
  onClose: () => void;
  onQueued: (id: number) => void;
}) {
  const [body, setBody] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const action = useAction();
  return (
    <form
      className="ial-form ial-inset"
      ref={reveal}
      onSubmit={(event) => {
        event.preventDefault();
        if ((!body.trim() && !files.length) || files.length > 12) return;
        void action.run(async () => {
          const form = new FormData();
          form.set("pasted_text", body.trim());
          files.forEach((file) => form.append("files", file));
          const result = await api.upload<ImportReview>("/bank/imports/upload", form);
          onQueued(result.id);
        });
      }}
    >
      <h3>导入面经</h3>
      <label>
        面经原文
        <textarea
          rows={6}
          maxLength={60000}
          value={body}
          onChange={(event) => setBody(event.target.value)}
          placeholder="粘贴笔试或面试原文"
        />
      </label>
      <label>
        上传文件
        <input
          type="file"
          multiple
          accept=".pdf,.docx,.md,.txt,.jpg,.jpeg,.png,.webp"
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />
      </label>
      <small>最多 12 个文件。提取的题目经审核后入库。</small>
      {files.length > 12 && <p role="alert">请选择不超过 12 个文件。</p>}
      <QueryState error={action.error} />
      <div className="ial-actions">
        <button
          className="ial-button ial-primary"
          disabled={action.busy || (!body.trim() && !files.length) || files.length > 12}
        >
          提交解析
        </button>
        <button type="button" className="ial-button" disabled={action.busy} onClick={onClose}>
          取消
        </button>
      </div>
    </form>
  );
}

function ReviewRow({
  item,
  batchId,
  onChanged,
}: {
  item: ReviewItem;
  batchId: number;
  onChanged: () => void;
}) {
  const [feedbackKind, setFeedbackKind] = useState<"revise" | "reject">();
  const [feedback, setFeedback] = useState("");
  const [retry, setRetry] = useState<ReviewOperation>();
  const [notice, setNotice] = useState("");
  const action = useAction();
  const running = item.review_state === "running";
  const reviewable = item.status === "DRAFT" && Number.isInteger(item.review_revision);

  function submit(kind: ReviewKind) {
    if (action.busy || (running && !retry)) return;
    const operation: ReviewOperation = retry ?? {
      action: kind,
      revision: item.review_revision,
      request_id: crypto.randomUUID(),
      ...(kind !== "approve" && feedback.trim() ? { feedback: feedback.trim() } : {}),
    };
    void action.run(async () => {
      try {
        const result = await api.post<{ outcome: string; item: ReviewItem }>(
          `/bank/imports/${batchId}/items/${item.id}/review`,
          operation,
        );
        setRetry(undefined);
        setFeedbackKind(undefined);
        setFeedback("");
        setNotice(
          (
            {
              approved: "已入库",
              merged: "已合并重复题",
              rejected: "已驳回",
              revised: "已重新生成讲解",
              running: "正在重新生成讲解",
              failed: "讲解未更新",
            } as Record<string, string>
          )[result.outcome] ?? "",
        );
      } catch (error) {
        if (!(error instanceof ApiError) || error.status >= 500) setRetry(operation);
        else setRetry(undefined);
        throw error;
      } finally {
        onChanged();
      }
    });
  }

  return (
    <li className="ial-review-item">
      <details>
        <summary>
          <strong>{item.title}</strong>
          <small>{running ? "改写中" : (statuses[item.status] ?? item.status)}</small>
        </summary>
        <div className="ial-inset">
          <p>
            {item.suggested_skill_key}
            {item.is_duplicate_of ? " · 与题库中已有题目重复" : ""}
          </p>
          {item.draft_answer ? <Reference html={item.draft_answer} /> : <p>暂无参考讲解。</p>}
          {item.review_error && <p role="alert">{item.review_error}</p>}
          {reviewable && !item.has_reviewable_answer && !item.is_duplicate_of && (
            <small>补齐可用讲解和评分要点后可以审核入库。</small>
          )}
          <QueryState error={action.error} />
          {notice && <p role="status">{notice}</p>}
          {retry ? (
            <div className="ial-actions">
              <button
                className="ial-button"
                disabled={action.busy}
                onClick={() => submit(retry.action)}
              >
                确认上次操作结果
              </button>
            </div>
          ) : (
            reviewable && (
              <>
                {feedbackKind ? (
                  <form
                    className="ial-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      submit(feedbackKind);
                    }}
                  >
                    <label>
                      {feedbackKind === "revise" ? "改写意见" : "驳回原因（可选）"}
                      <textarea
                        rows={3}
                        maxLength={1000}
                        value={feedback}
                        onChange={(event) => setFeedback(event.target.value)}
                      />
                    </label>
                    <div className="ial-actions">
                      <button
                        className="ial-button ial-primary"
                        disabled={
                          action.busy || running || (feedbackKind === "revise" && !feedback.trim())
                        }
                      >
                        {feedbackKind === "revise" ? "提交改写" : "确认驳回"}
                      </button>
                      <button
                        type="button"
                        className="ial-button"
                        disabled={action.busy}
                        onClick={() => setFeedbackKind(undefined)}
                      >
                        取消
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="ial-actions">
                    <button
                      className="ial-button ial-primary"
                      disabled={
                        action.busy ||
                        running ||
                        (!item.has_reviewable_answer && !item.is_duplicate_of)
                      }
                      onClick={() => submit("approve")}
                    >
                      审核入库
                    </button>
                    <button
                      className="ial-button"
                      disabled={action.busy || running}
                      onClick={() => setFeedbackKind("revise")}
                    >
                      改写讲解
                    </button>
                    <button
                      className="ial-button"
                      disabled={action.busy || running}
                      onClick={() => setFeedbackKind("reject")}
                    >
                      驳回
                    </button>
                  </div>
                )}
              </>
            )
          )}
        </div>
      </details>
    </li>
  );
}

export default function InterviewNotes() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("recent");
  const [selectedSet, setSelectedSet] = useState<InterviewSet>();
  const importing = params.get("import") === "1";
  const rawBatchId = params.get("batch");
  const batchId =
    rawBatchId && /^[1-9]\d{0,9}$/.test(rawBatchId) && Number(rawBatchId) <= 2147483647
      ? Number(rawBatchId)
      : undefined;
  const pending = useRead<PendingImports>("/bank/imports/pending");
  const sets = useRead<Items<InterviewSet>>("/bank/interview-sets");
  const current = useRead<Batch>(batchId ? "/bank/imports/" + batchId : null, 2500);
  const imports = (pending.data?.imports ?? []).filter((item) =>
    matches(
      query,
      item.short_code,
      item.meta?.company,
      ...item.items.map((question) => question.title),
    ),
  );
  const interviewSets = (sets.data?.items ?? []).filter((item) =>
    matches(query, item.title, item.project_name),
  );
  if (sort === "oldest") {
    imports.reverse();
    interviewSets.reverse();
  }
  const rows = filter === "sets" ? [] : imports;
  const papers = filter === "pending" ? [] : interviewSets;
  const activeBatch = current.data;
  function openBatch(id: number) {
    setParams({ batch: String(id) }, { replace: true });
  }

  return (
    <>
      <PageTitle title="面经素材库" intro="查看待审核的面经和已保存的项目面试题集。" />
      <section className="ial-panel">
        <div className="ial-toolbar">
          <Search value={query} onChange={setQuery} placeholder="搜索面经与题集" />
          <select
            aria-label="面经类型"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="all">全部类型</option>
            <option value="pending">待审核</option>
            <option value="sets">面试题集</option>
          </select>
          <select
            aria-label="面经排序"
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="recent">从新到旧</option>
            <option value="oldest">从旧到新</option>
          </select>
          <span className="ial-spacer" />
          <button
            className="ial-button ial-primary"
            onClick={() => {
              setSelectedSet(undefined);
              setParams({ import: "1" }, { replace: true });
            }}
          >
            <Icon name="paperclip" />
            导入面经
          </button>
        </div>
        {importing && (
          <ImportForm onClose={() => setParams({}, { replace: true })} onQueued={openBatch} />
        )}
        <QueryState {...pending} retry={pending.reload} />
        <QueryState {...sets} retry={sets.reload} />
        {pending.data && sets.data && !rows.length && !papers.length && (
          <QueryState empty="暂无符合条件的面经或题集。" />
        )}
        <ul className="ial-list">
          {rows
            .filter((batch) => batch.id !== activeBatch?.id || activeBatch.status === "REVIEW")
            .map((batch) => (
              <Row
                icon="notes"
                key={batch.id}
                title={
                  batch.meta?.company
                    ? batch.meta.company + " · " + batch.short_code
                    : batch.short_code
                }
                subtitle={
                  batch.items.filter((item) => item.status === "DRAFT").length + " 道待审题"
                }
                status="待审核"
              >
                <button
                  className="ial-button"
                  onClick={() => {
                    setSelectedSet(undefined);
                    openBatch(batch.id);
                  }}
                >
                  打开审核
                </button>
              </Row>
            ))}
          {papers.map((paper) => (
            <Row
              icon="notes"
              key={paper.id}
              title={paper.title}
              subtitle={paper.project_name}
              status="面试题集"
            >
              <button
                className="ial-button"
                onClick={() => {
                  setSelectedSet(paper);
                  setParams({}, { replace: true });
                }}
              >
                查看题集
              </button>
            </Row>
          ))}
        </ul>
        {batchId && (
          <section className="ial-open" ref={reveal} aria-label="面经审核">
            <div className="ial-toolbar">
              <h3>导入批次 {activeBatch?.short_code}</h3>
              <span className="ial-spacer" />
              <button
                className="ial-button"
                onClick={() => {
                  setParams({}, { replace: true });
                  pending.reload();
                }}
              >
                关闭审核
              </button>
            </div>
            <QueryState {...current} retry={current.reload} />
            {activeBatch && (
              <>
                <p role="status">
                  {statuses[activeBatch.status] ?? activeBatch.status}
                  {activeBatch.meta?.progress?.note ? " · " + activeBatch.meta.progress.note : ""}
                </p>
                {activeBatch.meta?.error && <QueryState error={activeBatch.meta.error} />}
                {!!activeBatch.meta?.files?.length && (
                  <ul>
                    {activeBatch.meta.files.map((file, index) => (
                      <li key={index}>
                        {file.name} ·{" "}
                        {file.error ?? file.warning ?? (file.ok ? "读取成功" : "未能读取")}
                      </li>
                    ))}
                  </ul>
                )}
                {activeBatch.meta?.pasted_text && (
                  <details className="ial-inset">
                    <summary>查看原文</summary>
                    <p className="ial-prewrap">{activeBatch.meta.pasted_text}</p>
                  </details>
                )}
                <ul className="ial-list">
                  {activeBatch.items.map((item) => (
                    <ReviewRow
                      key={item.id}
                      item={item}
                      batchId={activeBatch.id}
                      onChanged={() => {
                        current.reload();
                        pending.reload();
                      }}
                    />
                  ))}
                </ul>
                {activeBatch.status === "REVIEW" && !activeBatch.items.length && (
                  <QueryState empty="未提取到可审核的题目。" />
                )}
              </>
            )}
          </section>
        )}
        {selectedSet && (
          <section className="ial-open" ref={reveal} aria-label="面试题集">
            <div className="ial-toolbar">
              <h3>{selectedSet.title}</h3>
              <span className="ial-spacer" />
              <button className="ial-button" onClick={() => setSelectedSet(undefined)}>
                关闭题集
              </button>
            </div>
            <ol>
              {selectedSet.questions?.map((question) => (
                <li key={question.id ?? question.item_id}>{question.title}</li>
              ))}
            </ol>
            <ButtonLink to={chatLink("digest", selectedSet.title)}>在聊天中消化考点</ButtonLink>
          </section>
        )}
      </section>
    </>
  );
}
