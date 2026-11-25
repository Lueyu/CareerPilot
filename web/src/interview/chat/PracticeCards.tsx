import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import StudyMarkdown from "../../components/StudyMarkdown";
import { Card, ReadCard } from "./ui";
import { errorText } from "./model";
import { sessionPath } from "./useConversation";

interface Criterion {
  criterion_id: string;
  score: number;
  answer_quotes?: string[];
  reasoning_summary?: string;
  why_not_higher?: string;
  missing_points?: string[];
}
interface Submission {
  submission_id: number;
  status: string;
  payload: { text_answer: string };
  feedback?: { criterion_scores?: Criterion[]; scope_note?: string; next_action?: string } | null;
  can_retry?: boolean;
  error?: { message?: string } | null;
}
interface Practice {
  id: number;
  question: string;
  skill_name?: string | null;
  status: string;
  submissions: Submission[];
  retest_exam_id?: number | null;
}
const pendingStatuses = new Set([
  "QUEUED",
  "RUNNING",
  "RETRY_WAIT",
  "SCORING",
  "SCORING_RETRY_WAIT",
]);

function WrittenFeedback({ submission }: { submission: Submission }) {
  const feedback = submission.feedback;
  const criteria = feedback?.criterion_scores ?? [];
  return (
    <div className="ia-chat-feedback">
      <h4>作答反馈</h4>
      {criteria.map((row) => {
        const quotes = row.answer_quotes?.filter(
          (quote) => quote.trim() && submission.payload.text_answer.includes(quote),
        );
        return (
          <section key={row.criterion_id}>
            {row.reasoning_summary && <p>{row.reasoning_summary}</p>}
            {quotes?.map((quote, index) => (
              <blockquote key={index}>{quote}</blockquote>
            ))}
            {row.why_not_higher && <p>{row.why_not_higher}</p>}
            {Boolean(row.missing_points?.length) && (
              <ul>
                {row.missing_points?.map((point, index) => (
                  <li key={index}>{point}</li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
      {!criteria.length && <p>评分已完成，当前反馈没有提供逐项理由。</p>}
      {feedback?.next_action === "REVISE" && (
        <p>建议根据缺失项改写一遍，再提交修订，便于对照前后差异。</p>
      )}
      {feedback?.next_action === "NEW_CONTEXT_RETEST" && (
        <p>可以用新场景复测，检查换一道题后能否独立应用同一机制。</p>
      )}
      {feedback?.scope_note && <small>{feedback.scope_note}</small>}
    </div>
  );
}

export function PracticeCard({
  sessionId,
  focus,
  kind = "quiz",
  disabled,
}: {
  sessionId: number;
  focus?: number;
  kind?: "quiz" | "explain";
  disabled: boolean;
}) {
  const [item, setItem] = useState<Practice>();
  const [answer, setAnswer] = useState("");
  const [help, setHelp] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const inFlight = useRef(false);
  const keys = useRef(new Map<string, string>());
  const base = sessionPath(sessionId) + "/practices";
  const sub = item?.submissions[item.submissions.length - 1];
  const pending = !!sub && pendingStatuses.has(sub.status);
  const path = item ? "/study/practices/" + item.id : "";

  const load = useCallback(() => {
    let active = true;
    void api
      .get<{ items: (Practice & { source?: { focus_message_id?: number }; kind: string })[] }>(base)
      .then((data) => {
        if (!active) return;
        setItem(
          data.items.find(
            (value) => value.kind === kind && value.source?.focus_message_id === focus,
          ),
        );
        setLoaded(true);
        setError("");
      })
      .catch((err) => {
        if (active) setError(errorText(err));
      });
    return () => {
      active = false;
    };
  }, [base, focus, kind]);
  useEffect(load, [load]);
  useEffect(() => {
    if (!pending || !path) return;
    let active = true;
    const timer = setInterval(() => {
      void api
        .get<Practice>(path)
        .then((value) => {
          if (active) setItem(value);
        })
        .catch((err) => {
          if (active) setError(errorText(err));
        });
    }, 1600);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [pending, path]);

  async function perform(operation: string, action: (key: string) => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const key = keys.current.get(operation) ?? crypto.randomUUID();
    keys.current.set(operation, key);
    try {
      await action(key);
      keys.current.delete(operation);
    } catch (err) {
      setError(errorText(err));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <div className="ia-chat-output">
      <p>
        {kind === "quiz"
          ? "先用自己的话回答，再看评分理由。练习会记录提示和参考材料的使用情况。"
          : "试着把刚学到的机制讲给别人。写清输入、关键步骤及适用条件，评分会指出哪里还需要补证据。"}
      </p>
      <Card
        title={kind === "quiz" ? "随堂测验" : "解释练习"}
        detail={item?.skill_name ?? undefined}
      >
        {error && <p role="alert">{error}</p>}
        {!loaded && !error && <p role="status">正在读取练习…</p>}
        {!loaded && error && <button onClick={() => void load()}>重新读取</button>}
        {!item && loaded && (
          <button
            className="ia-chat-primary"
            disabled={disabled || busy || !focus}
            onClick={() =>
              void perform("start", async (key) => {
                setItem(
                  await api.post<Practice>(base, {
                    kind,
                    focus_message_id: focus,
                    request_key: key,
                  }),
                );
              })
            }
          >
            开始{kind === "quiz" ? "测验" : "练习"}
          </button>
        )}
        {!focus && !item && <p>完成一轮知识讲解后，可以从那条回复开始练习。</p>}
        {item && (
          <>
            <p className="ia-chat-question">{item.question}</p>
            {item.submissions.map((entry) => (
              <details key={entry.submission_id} open>
                <summary>
                  第 {item.submissions.indexOf(entry) + 1} 次作答
                  {pendingStatuses.has(entry.status) ? " · 正在评分" : ""}
                </summary>
                <blockquote>{entry.payload.text_answer}</blockquote>
                {entry.feedback && <WrittenFeedback submission={entry} />}
                {entry.error && <p role="alert">{entry.error.message || "这次评分未成功。"}</p>}
                {entry.status === "NEEDS_REVIEW" && <p>这次评分需要复核，暂时没有可确认的反馈。</p>}
                {entry.can_retry && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void perform("retry:" + entry.submission_id, async () => {
                        await api.post("/learning/submissions/" + entry.submission_id + "/retry");
                        setItem(await api.get<Practice>(path));
                      })
                    }
                  >
                    重试评分
                  </button>
                )}
              </details>
            ))}
            {item.status === "SKIPPED" ? (
              <p>已跳过这次练习，可以从另一条讲解开启练习。</p>
            ) : (
              <>
                <form
                  className="ia-chat-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const text = answer.trim();
                    if (!text || disabled || pending) return;
                    void perform("answer:" + text, async (key) => {
                      const result = await api.post<Practice>(path + "/answers", {
                        text_answer: text,
                        request_key: key,
                        parent_submission_id: sub?.submission_id,
                      });
                      setItem(result);
                      setAnswer("");
                    });
                  }}
                >
                  <label>
                    {sub ? "修订作答" : "我的作答"}
                    <textarea
                      value={answer}
                      maxLength={60000}
                      onChange={(event) => setAnswer(event.target.value)}
                      rows={3}
                    />
                  </label>
                  <div className="ia-chat-actions">
                    <button
                      className="ia-chat-primary"
                      disabled={busy || disabled || pending || !answer.trim()}
                    >
                      提交{sub ? "修订" : "作答"}
                    </button>
                    {(["hint", "reference"] as const).map((type) => (
                      <button
                        key={type}
                        type="button"
                        disabled={busy || disabled}
                        onClick={() =>
                          void perform(type, async (key) => {
                            const result = await api.post<{ content: string }>(
                              path + "/assistance",
                              { kind: type, request_key: key },
                            );
                            setHelp(result.content);
                          })
                        }
                      >
                        {type === "hint" ? "看提示" : "看参考"}
                      </button>
                    ))}
                  </div>
                </form>
                {help && (
                  <div className="ia-chat-prose">
                    <StudyMarkdown text={help} allowLinks={false} />
                  </div>
                )}
                {sub?.status === "COMPLETED" && !item.retest_exam_id && (
                  <button
                    disabled={busy || disabled}
                    onClick={() =>
                      void perform("retest", async () => {
                        await api.post(path + "/retest");
                        setItem(await api.get<Practice>(path));
                      })
                    }
                  >
                    生成独立复测
                  </button>
                )}
              </>
            )}
            {item.retest_exam_id && (
              <p>
                <Link to={"/chat/exam/" + item.retest_exam_id + "?session=" + sessionId}>
                  打开独立复测 →
                </Link>
              </p>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

interface ReviewCard {
  id: number;
  question: string;
  knowledge_point: string;
  next_review_at?: string | null;
}

function Flashcard({ item }: { item: ReviewCard }) {
  const [answer, setAnswer] = useState<string>();
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef(crypto.randomUUID());

  async function review(rating?: "again" | "hard" | "good") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (!rating) {
        const data = await api.post<{ answer: string }>("/study/cards/" + item.id + "/reveal");
        setAnswer(data.answer);
      } else {
        const data = await api.post<ReviewCard>("/study/cards/" + item.id + "/review", {
          rating,
          request_id: request.current,
        });
        setResult(
          data.next_review_at
            ? "下一次复习为 " + new Date(data.next_review_at).toLocaleDateString() + "。"
            : "已保存本次自评。",
        );
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="ia-chat-flash">
      <small>{item.knowledge_point}</small>
      <p>{item.question}</p>
      {answer === undefined ? (
        <button disabled={busy} onClick={() => void review()}>
          翻看答案
        </button>
      ) : (
        <>
          <div className="ia-chat-prose">
            <StudyMarkdown text={answer} allowLinks={false} />
          </div>
          {result ? (
            <p role="status">{result}</p>
          ) : (
            <div className="ia-chat-actions" aria-label="复习自评">
              <button disabled={busy} onClick={() => void review("again")}>
                忘记了
              </button>
              <button disabled={busy} onClick={() => void review("hard")}>
                有困难
              </button>
              <button disabled={busy} onClick={() => void review("good")}>
                记住了
              </button>
            </div>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </article>
  );
}

export function Flashcards() {
  return (
    <ReadCard<{ items: ReviewCard[] }>
      title="今日闪卡"
      path="/study/cards?status=DUE"
      render={({ items }) => (
        <>
          <p>
            {items.length
              ? "先尝试回忆再翻面。按实际回忆情况自评，下次复习时间会据此调整。"
              : "当前没有到期闪卡。已有学习资料可在资料库查看。"}
          </p>
          {items.slice(0, 5).map((item) => (
            <Flashcard key={item.id} item={item} />
          ))}
          <Link to="/library/materials">打开学习资料</Link>
        </>
      )}
    />
  );
}
