import { useState } from "react";
import { workspaceStorageKey } from "../../api/client";
import { useLocale } from "../../i18n";
import StudyMarkdown from "../../components/StudyMarkdown";
import type { Message, MockPurpose, WorkflowOperation, WorkflowRun } from "./model";
import { mockInterviewProgress } from "./mockInterview";
import { Card, Icon } from "./ui";

const copy = {
  "zh-CN": {
    title: "跟着面试官，完成一场真实练习",
    description:
      "一次只问一个问题。先独立回答，收到点评后再进入下一环节。原答和点评自动保存，随时可以暂停后回来。",
    stages: ["准备", "自我介绍", "技术", "系统设计", "行为", "复盘"],
    start: "开始面试准备",
    ask: "请面试官提问",
    answer: "我的回答",
    submit: "提交回答",
    finish: "结束面试，查看复盘",
    prepTask:
      "请输入你的目标岗位和希望练习的方向。例如：AI Agent 工程师，重点练习项目表达和系统设计。",
    generateReport: "生成复盘报告",
    report: "正在根据本次已保存的回答和点评生成复盘报告…",
    reportReady: "复盘报告已完整保存，可以结束本次面试。",
    pause: "暂停面试",
    resume: "继续面试",
    skip: "跳过本环节（会记录）",
    skipped: "已跳过",
    done: "已完成",
    current: "当前",
    later: "待开始",
    nextLabels: [
      "进入准备",
      "进入自我介绍",
      "进入技术问答",
      "进入系统设计",
      "进入行为问题",
      "进入复盘",
    ],
    paused: "面试已暂停。原答和进度已保存，继续后仍在这一环节。",
    completed: "本次面试已结束。完整点评和复盘报告保存在上方对话中。",
    prepare: "面试官会给你一个具体任务。准备环节先说明目标岗位和希望练习的方向。",
    question: "面试官正在准备本环节的问题，请稍候…",
    feedback: "回答已保存，面试官正在点评。点评完成后才能进入下一环节。",
    ready: "本环节的回答和点评已保存。读完点评后，进入下一环节。",
    failed: "回复未完成，进度没有推进。你的回答已保留，请重试原回复。",
    retry: "重试本环节回复",
    task: "请针对面试官的问题写下你自己的回答。可以说明不确定的部分，不需要先查参考答案。",
    saved: "草稿仅保存在这台设备；提交后原答保存在对话中。",
    evidence: "这是模拟练习，点评不会自动计入能力评分。跳过的环节不会算作已完成作答。",
    reportLink: "查看最终复盘报告",
    progress: "面试环节",
    finished: "已完成环节",
    viewFeedback: "查看本环节点评",
  },
  en: {
    title: "Complete a real practice interview with your interviewer",
    description:
      "One question at a time. Answer independently, read the feedback, then continue. Answers and feedback are saved automatically. Pause and return whenever you need.",
    stages: ["Preparation", "Introduction", "Technical", "System design", "Behavioral", "Review"],
    start: "Start interview preparation",
    ask: "Ask the stage question",
    answer: "My answer",
    submit: "Submit answer",
    finish: "Finish interview and view review",
    prepTask:
      "Enter your target role and practice goals. For example: AI Agent engineer, focusing on project communication and system design.",
    generateReport: "Generate interview report",
    report: "Generating the report from your saved answers and feedback…",
    reportReady: "The final report is fully saved. You can finish this interview.",
    pause: "Pause interview",
    resume: "Resume interview",
    skip: "Skip this stage (recorded)",
    skipped: "Skipped",
    done: "Answered",
    current: "Current",
    later: "Upcoming",
    nextLabels: [
      "Enter preparation",
      "Enter introduction",
      "Enter technical questions",
      "Enter system design",
      "Enter behavioral questions",
      "Enter review",
    ],
    paused: "Interview paused. Your answers and progress are saved. Resume at this stage.",
    completed:
      "Interview finished. Your feedback and final report are saved in the conversation above.",
    prepare:
      "The interviewer will give you one concrete task. Preparation starts with your target role and practice goals.",
    question: "The interviewer is preparing this stage's question…",
    feedback: "Answer saved. Waiting for completed feedback before you can continue.",
    ready: "Your answer and feedback are saved. Read the feedback, then continue.",
    failed:
      "The reply did not finish. Progress has not advanced and your answer is kept. Retry the original reply.",
    retry: "Retry stage reply",
    task: "Write your own answer to the interviewer's question. Include any uncertainty; you do not need to look up a reference answer.",
    saved: "Drafts stay on this device. Submitted answers are saved in the conversation.",
    evidence:
      "This is practice. Feedback does not automatically update competency scores. Skipped stages do not count as answered.",
    reportLink: "View final interview report",
    progress: "Interview stages",
    finished: "Completed stages",
    viewFeedback: "View stage feedback",
  },
};

export default function MockInterviewGuide({
  run,
  messages,
  sessionId,
  busy,
  start,
  send,
  update,
  retry,
}: {
  run?: WorkflowRun;
  messages: Message[];
  sessionId?: number;
  busy: boolean;
  start: () => void;
  send: (purpose: MockPurpose, content: string) => Promise<boolean>;
  update: (operation: WorkflowOperation) => Promise<boolean>;
  retry: (message: Message) => void;
}) {
  const c = copy[useLocale()];
  const draftKey = workspaceStorageKey(
    `mock-draft:${sessionId}:${run?.run_id}:${run?.current}:${run?.step_revision}`,
  );
  const [draft, setDraft] = useState(() => {
    try {
      return localStorage.getItem(draftKey) ?? "";
    } catch {
      return "";
    }
  });
  function putDraft(value: string) {
    setDraft(value);
    try {
      if (value) localStorage.setItem(draftKey, value);
      else localStorage.removeItem(draftKey);
    } catch {
      /* Browser storage may be unavailable. */
    }
  }
  const state = run ? mockInterviewProgress(run, messages) : undefined;
  const paused = run?.status === "paused";
  const completed = run?.status === "completed";
  const count = run?.completed_steps?.length ?? 0;
  const finalReport = completed
    ? [...messages]
        .reverse()
        .find(
          (m) =>
            m.role === "assistant" &&
            m.workflow &&
            m.workflow.run_id === run.run_id &&
            m.workflow.step === run.steps.length - 1 &&
            m.workflow.purpose === "mock_report" &&
            m.status === "completed",
        )
    : undefined;

  return (
    <div className="ia-chat-output ia-mock-guide" aria-label={c.progress}>
      <Card
        title={run ? c.stages[Math.min(run.current, 5)] : c.title}
        detail={run ? `${c.finished} ${count} / 6` : undefined}
      >
        {!run && <p>{c.description}</p>}
        <div className="ia-mock-layout">
          <ol className="ia-chat-steps ia-mock-rail" aria-label={c.progress}>
            {c.stages.map((label, index) => (
              <li key={label} aria-current={run?.current === index ? "step" : undefined}>
                <span className="ia-chat-step-mark">
                  {run?.completed_steps?.includes(index) ? <Icon name="check" /> : index + 1}
                </span>
                <span>{label}</span>
                {run && (
                  <small>
                    {run.skipped.includes(index)
                      ? c.skipped
                      : run.completed_steps?.includes(index)
                        ? c.done
                        : index === run.current
                          ? c.current
                          : c.later}
                  </small>
                )}
              </li>
            ))}
          </ol>
          <div className="ia-mock-task">
            {!run ? (
              <button className="ia-chat-primary" disabled={busy} onClick={start}>
                {c.start}
              </button>
            ) : (
              <>
                <p role="status" aria-live="polite">
                  {completed
                    ? c.completed
                    : paused
                      ? c.paused
                      : run.current === 5 && state?.phase === "ready"
                        ? c.reportReady
                        : state
                          ? c[state.phase]
                          : c.prepare}
                </p>
                {!completed && !paused && state?.phase === "prepare" && (
                  <button
                    className="ia-chat-primary"
                    disabled={busy}
                    onClick={() =>
                      void send(
                        run.current === 5 ? "mock_report" : "mock_question",
                        run.current === 5 ? c.generateReport : c.ask,
                      )
                    }
                  >
                    {run.current === 5 ? c.generateReport : c.ask}
                  </button>
                )}
                {!completed && !paused && state?.phase === "answer" && (
                  <>
                    {state.question && (
                      <StudyMarkdown text={state.question.content} allowLinks={false} />
                    )}
                    <p>{run.current === 0 ? c.prepTask : c.task}</p>
                    <form
                      className="ia-chat-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        if (draft.trim() && !busy)
                          void send("mock_answer", draft.trim()).then((ok) => {
                            if (ok) putDraft("");
                          });
                      }}
                    >
                      <label>
                        {c.answer}
                        <textarea
                          rows={5}
                          maxLength={8000}
                          value={draft}
                          disabled={busy}
                          onChange={(event) => putDraft(event.target.value)}
                        />
                      </label>
                      <small>{c.saved}</small>
                      <button className="ia-chat-primary" disabled={busy || !draft.trim()}>
                        {c.submit}
                      </button>
                    </form>
                  </>
                )}
                {!completed && !paused && state?.phase === "ready" && (
                  <div className="ia-chat-actions">
                    <a href={`#study-message-${state.report?.id ?? state.feedback?.id}`}>
                      {state.report ? c.reportLink : c.viewFeedback}
                    </a>
                    <button
                      className="ia-chat-primary"
                      disabled={busy}
                      onClick={() => void update({ operation: "advance" })}
                    >
                      {run.current === 5 ? c.finish : c.nextLabels[run.current + 1]}
                    </button>
                  </div>
                )}
                {!completed && !paused && state?.phase === "failed" && state.reply && (
                  <button
                    className="ia-chat-primary"
                    disabled={busy}
                    onClick={() => state.reply && retry(state.reply)}
                  >
                    {c.retry}
                  </button>
                )}
                {!completed && (
                  <div className="ia-chat-actions">
                    <button
                      disabled={busy}
                      onClick={() => void update({ operation: paused ? "resume" : "pause" })}
                    >
                      {paused ? c.resume : c.pause}
                    </button>
                    {!paused && run.current < 5 && (
                      <button
                        disabled={
                          busy || state?.phase === "question" || state?.phase === "feedback"
                        }
                        onClick={() => void update({ operation: "skip" })}
                      >
                        {c.skip}
                      </button>
                    )}
                  </div>
                )}
                {finalReport && <a href={`#study-message-${finalReport.id}`}>{c.reportLink}</a>}
              </>
            )}
          </div>
        </div>
        <small>{c.evidence}</small>
      </Card>
    </div>
  );
}
