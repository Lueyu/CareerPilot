import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useNavigationType,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { api, getWorkspace, workspaceStorageKey } from "../../api/client";
import StudyMarkdown from "../../components/StudyMarkdown";
import StudyArtifact from "../../components/StudyArtifacts";
import InterviewBrief from "../InterviewBrief";
import Composer from "./Composer";
import {
  BenchmarkCard,
  HistoryCard,
  LibraryInventoryCard,
  PlanCard,
  ProjectCard,
  QuestionSearchCard,
  ReportCard,
} from "./DataCards";
import { JdCard, MaterialCard, SaveNote } from "./MaterialCards";
import { MemoryPanel } from "./MemoryPanel";
import { Flashcards, PracticeCard } from "./PracticeCards";
import WorkflowCard from "./WorkflowCard";
import { Card, Icon } from "./ui";
import {
  chatUrl,
  errorText,
  examConfigUrl,
  examTopic,
  materialText,
  parsePrompt,
  positiveId,
  requestedWorkflow,
  roles,
  workflowCommand,
  workflows,
  type Intent,
  type Message,
  type Role,
  type Session,
  type Workflow,
  type WorkflowOperation,
  type WorkflowRun,
} from "./model";
import { notifyHistory, sessionPath, sessionsPath, useConversation } from "./useConversation";
import "./chat.css";

export { MemoryPanel } from "./MemoryPanel";

type ExamConfig = (options: { sessionId?: number; topic?: string }) => ReactNode;
type Props = { renderExamConfig?: ExamConfig };
type Panel = { intent: Intent | "project"; topic?: string; focus?: number };
type Question = { id: number; title: string };

const starters: { label: string; intent: Intent }[] = [
  { label: "看看我的岗位差距", intent: "benchmark" },
  { label: "安排今天的学习", intent: "plan" },
  { label: "准备一场正式考试", intent: "exam" },
  { label: "从题库找题", intent: "questions" },
];

const roleActions: Record<Role, Panel["intent"]> = {
  interviewer: "practice",
  exam: "exam",
  coach: "plan",
  analyst: "benchmark",
  resume: "project",
  collect: "import",
};

function restore(key: string) {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function remember(key: string, value: string) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* Private windows may prevent storage. */
  }
}

function studyAction(role: Role, content: string, questionId?: number) {
  if (questionId) return "critique";
  if (role === "interviewer") return "interview";
  if (role === "resume" && /\bJD\b|岗位|职位/i.test(content)) return "jd";
  if (role === "resume") return "project";
  return "explain";
}

function AnswerQuestion({
  question,
  submit,
  disabled,
}: {
  question: Question;
  submit: (answer: string) => Promise<boolean>;
  disabled: boolean;
}) {
  const [answer, setAnswer] = useState("");
  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!answer.trim() || disabled) return;
    if (await submit(answer.trim())) setAnswer("");
  }
  return (
    <div className="ia-chat-output">
      <p>
        先独立作答，再请面试官按题目要求分析你的原话。这轮反馈用于学习，正式能力证据来自独立考试。
      </p>
      <Card title="题库题练习">
        <p className="ia-chat-question">{question.title}</p>
        <form className="ia-chat-form" onSubmit={(event) => void onSubmit(event)}>
          <label>
            我的作答
            <textarea
              value={answer}
              rows={4}
              maxLength={6000}
              onChange={(event) => setAnswer(event.target.value)}
            />
          </label>
          <button className="ia-chat-primary" disabled={disabled || !answer.trim()}>
            提交给面试官
          </button>
        </form>
      </Card>
    </div>
  );
}

function Conversation({
  sessionId,
  invalidId,
  renderExamConfig,
}: Props & { sessionId?: number; invalidId: boolean }) {
  const navigate = useNavigate();
  const { key: locationKey } = useLocation();
  const navigationType = useNavigationType();
  const [params, setParams] = useSearchParams();
  const {
    session,
    generating,
    messages,
    error: readError,
    refresh,
    applyWorkflow,
  } = useConversation(sessionId);
  const draftKey = workspaceStorageKey("ia-chat-draft:" + (sessionId ?? "new"));
  const [input, setInput] = useState(() => restore(draftKey));
  const [selectedRole, setLocalRole] = useState<Role | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [palette, setPalette] = useState(params.get("palette") === "1");
  const [memory, setMemory] = useState(params.get("memory") === "1");
  const [panel, setPanel] = useState<Panel>();
  const [question, setQuestion] = useState<Question>();
  const [replaceWorkflow, setReplaceWorkflow] = useState<Workflow>();
  const [benchmarkRevision, setBenchmarkRevision] = useState(0);
  const [hiddenReports, setHiddenReports] = useState<number[]>([]);
  const active = useRef(true);
  const inFlight = useRef(false);
  const createdSession = useRef<number>();
  const pendingSend = useRef<{ signature: string; requestId: string }>();
  const scroll = useRef<HTMLDivElement>(null);
  const stickToEnd = useRef(true);
  const run = session?.context.interview_workflow;
  const latest = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && message.status === "completed");
  const report = positiveId(params.get("report"));
  const reports = [
    ...new Set([...(report ? [report] : []), ...(session?.exam_reports ?? [])]),
  ].filter((id) => !hiddenReports.includes(id));
  const externalTopic = params.get("topic")?.trim().slice(0, 500);
  const externalSkill = params.get("skill");
  const linkedWorkflow = workflows.find(
    (item) =>
      item.id === externalSkill ||
      (item.id === "project" && externalSkill === "project-polish") ||
      (item.id === "review" && externalSkill === "spaced-review"),
  );
  const externalQuestion = positiveId(params.get("question"));
  const queryPanel: Panel | undefined = params.get("kind") === "jd" ? { intent: "jd" } : undefined;
  const visiblePanel = panel ?? queryPanel;
  const unavailable = invalidId || (!!sessionId && (!session || session.status !== "active"));

  useEffect(() => {
    active.current = true;
    const openMemory = () => setMemory(true);
    window.addEventListener("interview-memory-open", openMemory);
    return () => {
      active.current = false;
      window.removeEventListener("interview-memory-open", openMemory);
    };
  }, []);
  const explicitNew = !sessionId && params.get("new") === "1";
  useEffect(() => {
    if (!explicitNew || navigationType !== "PUSH") return;
    setInput("");
    remember(draftKey, "");
    setLocalRole(null);
    setPanel(undefined);
    setQuestion(undefined);
    setPalette(false);
    setError("");
    createdSession.current = undefined;
    pendingSend.current = undefined;
  }, [draftKey, explicitNew, locationKey, navigationType]);
  const queryMemory = params.get("memory") === "1";
  const queryPalette = params.get("palette") === "1";
  useEffect(() => {
    if (queryMemory) setMemory(true);
  }, [queryMemory]);
  useEffect(() => {
    if (queryPalette) setPalette(true);
  }, [queryPalette]);
  useEffect(() => {
    if (!externalQuestion) return;
    let current = true;
    void api
      .get<Question>(sessionsPath + "/question/" + externalQuestion)
      .then((found) => {
        if (current) setQuestion(found);
      })
      .catch((err) => {
        if (current) setError(errorText(err));
      });
    return () => {
      current = false;
    };
  }, [externalQuestion]);

  const lastReplyText = messages[messages.length - 1]?.content;
  useEffect(() => {
    const element = scroll.current;
    if (element && stickToEnd.current) element.scrollTop = element.scrollHeight;
  }, [
    messages.length,
    lastReplyText,
    visiblePanel?.intent,
    visiblePanel?.focus,
    run?.revision,
    question?.id,
    report,
  ]);

  function removeQuery(...keys: string[]) {
    if (!keys.some((key) => params.has(key))) return;
    const next = new URLSearchParams(params);
    keys.forEach((key) => next.delete(key));
    setParams(next, { replace: true });
  }

  function put(value: string) {
    setInput(value);
    remember(draftKey, value);
  }

  async function perform(operation: () => Promise<void>) {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
      return true;
    } catch (err) {
      if (active.current) setError(errorText(err));
      return false;
    } finally {
      inFlight.current = false;
      if (active.current) setBusy(false);
    }
  }

  async function ensureSession(topic: string) {
    const id = sessionId ?? createdSession.current;
    if (id) return id;
    const created = await api.post<Session>(sessionsPath, {
      source_type: "free",
      topic: topic.slice(0, 8000),
      title: topic.replace(/\s+/g, " ").slice(0, 80),
    });
    createdSession.current = created.id;
    notifyHistory();
    return created.id;
  }

  async function moveTo(id: number) {
    notifyHistory();
    if (!active.current) return;
    if (id === sessionId) await refresh().catch(() => {});
    else navigate(chatUrl(id, report), { replace: true });
  }

  async function sendTo(
    id: number,
    content: string,
    role: Role,
    details: { projectId?: number; questionId?: number } = {},
  ) {
    const body = {
      content,
      agent_role: role,
      action: studyAction(role, content, details.questionId),
      mode: "auto",
      project_id: details.projectId,
      agent_question_id: details.questionId,
    };
    const signature = JSON.stringify(body);
    const requestId =
      pendingSend.current?.signature === signature
        ? pendingSend.current.requestId
        : crypto.randomUUID();
    pendingSend.current = { signature, requestId };
    await api.post(sessionPath(id) + "/messages", { ...body, request_id: requestId });
    pendingSend.current = undefined;
  }

  async function sendMessage(
    content: string,
    role: Role,
    options: {
      projectId?: number;
      questionId?: number;
      fromComposer?: boolean;
    } = {},
  ) {
    if (!content.trim() || unavailable || generating) return false;
    return perform(async () => {
      const id = await ensureSession(content);
      await sendTo(id, content, role, options);
      if (options.fromComposer && active.current) put("");
      stickToEnd.current = true;
      await moveTo(id);
    });
  }

  async function updateWorkflow(operation: WorkflowOperation) {
    if (unavailable) return false;
    return perform(async () => {
      const id =
        sessionId ??
        (await ensureSession(
          workflows.find(
            (item) => operation.operation === "start" && operation.workflow === item.id,
          )?.name ?? "技能对话",
        ));
      try {
        const workflow = await api.patch<WorkflowRun>(sessionPath(id) + "/interview-workflow", {
          ...operation,
          expected_revision: run?.revision ?? 0,
        });
        if (id === sessionId && active.current) applyWorkflow(workflow);
      } catch (err) {
        await refresh().catch(() => {});
        throw err;
      }
      await moveTo(id);
    });
  }

  async function start(workflow: Workflow, topic?: string, force = false, fromComposer = false) {
    const definition = workflows.find((item) => item.id === workflow);
    if (!definition || unavailable || generating) return;
    if (run && run.status !== "completed" && !force) {
      setReplaceWorkflow(workflow);
      return;
    }
    return perform(async () => {
      const id = await ensureSession(definition.name + (topic ? " · " + topic : ""));
      await api.patch<WorkflowRun>(sessionPath(id) + "/interview-workflow", {
        operation: "start",
        workflow,
        expected_revision: run?.revision ?? 0,
      });
      try {
        if (active.current && fromComposer) put("");
        if (active.current)
          await sendTo(id, definition.prompt + (topic ? "\n主题：" + topic : ""), definition.role);
      } finally {
        stickToEnd.current = true;
        await moveTo(id);
      }
    });
  }

  function open(intent: Panel["intent"], topic?: string, focus?: number) {
    stickToEnd.current = true;
    setPanel({ intent, topic, focus });
  }

  function showRole(role: Role | null) {
    setLocalRole(role);
    if (role) open(roleActions[role]);
  }

  async function send() {
    if (!input.trim() || unavailable || generating || busy) return;
    const raw = input.trim();
    const workflow = requestedWorkflow(raw);
    if (workflow) {
      await start(workflow, externalTopic, false, true);
      return;
    }
    const command = run && workflowCommand(raw);
    if (command) {
      if (command.operation === "change") {
        const original = /^(?:把|将)(.+?)(?:换成|改为|改成)/.exec(raw)?.[1].trim();
        const index = run.steps.findIndex(
          (step, at) => at >= run.current && original?.startsWith(step),
        );
        if (index >= 0) command.step = index;
        else if (original && !/^(?:当前|这一?步|这道题)/.test(original)) {
          setError("没找到「" + original + "」。请在技能卡里选择要修改的步骤。");
          return;
        }
      }
      if (await updateWorkflow(command)) put("");
      return;
    }
    const parsed = parsePrompt(raw, selectedRole);
    if (parsed.kind === "select") {
      showRole(parsed.role);
      put("");
      setPalette(false);
      removeQuery("palette");
      return;
    }
    if (await sendMessage(parsed.content, parsed.role, { fromComposer: true })) {
      setPalette(false);
      if (parsed.intent === visiblePanel?.intent) setPanel(undefined);
    }
  }

  async function changeReply(message: Message, operation: "interrupt" | "retry") {
    if (!sessionId) return;
    await perform(async () => {
      await api.post(
        sessionPath(sessionId) +
          "/messages/" +
          message.id +
          "/" +
          operation +
          (operation === "interrupt" ? "?expected_attempt=" + message.attempt : ""),
      );
      await moveTo(sessionId);
    });
  }

  function pickQuestion(title: string, id?: number) {
    if (id) {
      setQuestion({ id, title });
      stickToEnd.current = true;
    } else {
      put("请带我练习「" + title + "」。先说明这项练习考察什么，再给我一道题。");
    }
  }

  function renderPanel(activePanel: Panel, reply?: Message) {
    const { intent, topic, focus } = activePanel;
    switch (intent) {
      case "exam": {
        const subject = examTopic(topic) ?? externalTopic;
        return (
          <div className="ia-chat-output">
            <p>先确认考试范围、题量和时间。正式提交后，逐题评分会回到对话，方便接着问失分原因。</p>
            {renderExamConfig ? (
              renderExamConfig({ sessionId, topic: subject })
            ) : (
              <Card title="正式考试配置">
                <Link to={examConfigUrl(sessionId, subject)}>配置试卷 →</Link>
              </Card>
            )}
          </div>
        );
      }
      case "benchmark":
        return <BenchmarkCard key={benchmarkRevision} />;
      case "history":
        return <HistoryCard sessionId={sessionId} />;
      case "plan":
        return <PlanCard practice={pickQuestion} />;
      case "questions":
        return <QuestionSearchCard content={topic ?? ""} select={pickQuestion} />;
      case "jd":
        return (
          <JdCard
            initial={materialText(topic)}
            onSaved={() => setBenchmarkRevision((current) => current + 1)}
          />
        );
      case "import":
        return <MaterialCard initial={materialText(topic)} sessionId={sessionId} />;
      case "flashcard":
        return <Flashcards />;
      case "project":
        return (
          <ProjectCard
            choose={(id, title) =>
              void sendMessage(
                "请结合「" +
                  title +
                  "」的项目资料，帮我用 STAR 梳理本人贡献。明确还需要我补充哪些事实。",
                "resume",
                { projectId: id },
              )
            }
          />
        );
      case "practice":
      case "quiz":
        return sessionId ? (
          <PracticeCard
            key={focus ?? reply?.id ?? latest?.id ?? "pending"}
            sessionId={sessionId}
            focus={focus ?? reply?.id ?? latest?.id}
            kind={intent === "quiz" ? "quiz" : "explain"}
            disabled={unavailable || !!generating}
          />
        ) : (
          <Card title={intent === "quiz" ? "随堂测验" : "解释练习"}>
            <p>先在聊天里讲清想练的知识点。收到讲解后，可以基于那条回复开始练习。</p>
          </Card>
        );
    }
  }

  const lastUserMessage = messages[messages.length - 2];
  const lastAssistantMessage = messages[messages.length - 1];
  const workflowBesideBenchmark =
    !!run &&
    lastAssistantMessage?.role === "assistant" &&
    lastAssistantMessage.status !== "generating" &&
    !!lastUserMessage?.agent_role &&
    parsePrompt(lastUserMessage.content, lastUserMessage.agent_role).intent === "benchmark";
  const workflowContent = run && (
    <>
      <WorkflowCard key={run.id} run={run} busy={busy || unavailable} update={updateWorkflow} />
      {run.status === "running" && (
        <div className="ia-chat-actions">
          <button
            disabled={busy || !!generating || unavailable}
            onClick={() => {
              const definition = workflows.find((item) => item.id === run.id);
              if (!definition) return;
              void sendMessage(
                "继续「" +
                  definition.name +
                  "」的「" +
                  run.steps[run.current] +
                  "」。解释这一步的目的，并一次只给一个可执行的任务。",
                definition.role,
              );
            }}
          >
            和助手完成当前步骤
          </button>
          {run.id === "prep" && (
            <button
              onClick={() =>
                open(run.current === 0 ? "jd" : run.current === 1 ? "benchmark" : "plan")
              }
            >
              打开本步资料
            </button>
          )}
          {run.id === "digest" && (
            <button onClick={() => open(run.current < 2 ? "import" : "exam")}>打开本步工具</button>
          )}
          {run.id === "project" && <button onClick={() => open("project")}>选择项目</button>}
          {run.id === "review" && <button onClick={() => open("flashcard")}>打开今日闪卡</button>}
          {run.id === "boost" && (
            <button onClick={() => open(run.current < 2 ? "benchmark" : "practice")}>
              打开专项工具
            </button>
          )}
        </div>
      )}
    </>
  );

  return (
    <section className="ia-chat" aria-label="聊天工作台">
      <div
        className="ia-chat-scroll"
        ref={scroll}
        onScroll={(event) => {
          const element = event.currentTarget;
          stickToEnd.current = element.scrollHeight - element.clientHeight - element.scrollTop < 96;
        }}
      >
        <div className="ia-chat-content">
          <header className="ia-chat-heading">
            <div>
              <h1>{session?.title || "今天想准备什么？"}</h1>
              {session ? (
                <p>面试 AI 助手</p>
              ) : (
                <p>在这里练习面试、分析岗位要求，或整理手上的面试资料。</p>
              )}
            </div>
            <button type="button" onClick={() => setPalette(true)}>
              <Icon name="slash" />
              打开角色与技能
            </button>
          </header>
          {!sessionId && !invalidId && <InterviewBrief />}
          {invalidId && (
            <p role="alert">
              此对话链接无效。 <Link to="/chat?new=1">新对话</Link>
            </p>
          )}
          {readError && (
            <div role="alert">
              <p>{readError}</p>
              <button onClick={() => void refresh().catch(() => {})}>重新读取对话</button>
            </div>
          )}
          {sessionId && !session && !readError && <p role="status">正在读取对话…</p>}
          {session?.status === "ended" && (
            <div className="ia-chat-actions">
              <p>这段对话已结束。</p>
              <button
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    await api.patch(sessionPath(session.id), { status: "active" });
                    await moveTo(session.id);
                  })
                }
              >
                继续对话
              </button>
            </div>
          )}
          {!sessionId && !invalidId && !report && !visiblePanel && !linkedWorkflow && (
            <div className="ia-chat-starters">
              {starters.map((item) => (
                <button key={item.intent} onClick={() => open(item.intent)}>
                  <Icon name="target" />
                  {item.label}
                  <Icon name="chevron" />
                </button>
              ))}
            </div>
          )}
          {linkedWorkflow && !run && (
            <Card title={linkedWorkflow.name}>
              <p>
                {linkedWorkflow.prompt}
                {externalTopic && " 这次的主题是「" + externalTopic + "」。"}
              </p>
              <button
                className="ia-chat-primary"
                disabled={busy || unavailable}
                onClick={() => void start(linkedWorkflow.id, externalTopic)}
              >
                开始技能
              </button>
            </Card>
          )}
          {replaceWorkflow && (
            <Card title="切换当前技能">
              <p>切换后，这段对话会显示新技能的进度。此前的聊天内容仍可回看。</p>
              <div className="ia-chat-actions">
                <button
                  disabled={busy}
                  onClick={() => {
                    const next = replaceWorkflow;
                    setReplaceWorkflow(undefined);
                    void start(next, externalTopic, true, requestedWorkflow(input) === next);
                  }}
                >
                  切换技能
                </button>
                <button onClick={() => setReplaceWorkflow(undefined)}>取消</button>
              </div>
            </Card>
          )}
          {messages.map((message, index) => {
            if (message.role === "user")
              return (
                <article className="ia-chat-user" key={message.id} aria-label="我的消息">
                  <p>{message.content}</p>
                  <span aria-hidden="true">我</span>
                </article>
              );
            const user =
              index > 0 && messages[index - 1].request_id === message.request_id
                ? messages[index - 1]
                : undefined;
            const intent = user?.agent_role && parsePrompt(user.content, user.agent_role).intent;
            const role = roles.find((item) => item.id === message.agent_role);
            const inventory = message.library_inventory;
            const citations = inventory
              ? message.sources?.filter(({ kind }) => kind !== "library_summary")
              : message.sources;
            const text = message.content.split(/(?:^|\n)```study-blocks\s*(?:\n|$)/, 1)[0].trim();
            return (
              <article
                id={"study-message-" + message.id}
                className="ia-chat-assistant"
                key={message.id}
                aria-label={role?.name ?? "面试 AI 助手"}
              >
                <header>
                  <span className="ia-chat-card-icon">
                    <Icon name="target" />
                  </span>
                  <strong>{role?.name ?? "面试 AI 助手"}</strong>
                  {message.status !== "generating" && (
                    <small className="ia-chat-message-ref" title="可在观测台按消息编号查询">
                      #{message.id}
                    </small>
                  )}
                </header>
                {text && (
                  <div className="ia-chat-prose">
                    {message.status === "generating" ? (
                      <p className="ia-chat-stream">{text}</p>
                    ) : (
                      <StudyMarkdown text={text} allowLinks={false} />
                    )}
                  </div>
                )}
                {inventory && (
                  <LibraryInventoryCard
                    inventory={inventory}
                    select={pickQuestion}
                    disabled={unavailable || busy || !!generating}
                  />
                )}
                {message.status === "generating" && !text && <p role="status">正在准备回复…</p>}
                {message.status === "completed" && !text && (
                  <p>
                    {message.blocks?.[0]?.summary ||
                      "这次回复缺少文字解释。可以在对话里追问依据和下一步。"}
                  </p>
                )}
                {message.status === "completed" &&
                  message.blocks?.map((block) => (
                    <StudyArtifact key={block.id} b={block} sources={message.sources ?? []} />
                  ))}
                {(message.status === "failed" || message.status === "interrupted") && (
                  <div role="status">
                    <p>
                      {message.error ||
                        (message.status === "interrupted" ? "已停止生成。" : "这次回复未能完成。")}
                    </p>
                    {index === messages.length - 1 && (
                      <button
                        disabled={busy || unavailable}
                        onClick={() => void changeReply(message, "retry")}
                      >
                        重试这条回复
                      </button>
                    )}
                  </div>
                )}
                {!!citations?.length && (
                  <details className="ia-chat-sources">
                    <summary>引用来源 · {citations.length}</summary>
                    <ul>
                      {citations.map((source, sourceIndex) => (
                        <li key={source.kind + ":" + source.id + ":" + sourceIndex}>
                          <strong>{source.label}</strong>
                          {source.text && <p>{source.text}</p>}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {intent &&
                  !(inventory && intent === "questions") &&
                  user &&
                  message.status !== "generating" &&
                  (workflowBesideBenchmark && message === lastAssistantMessage ? (
                    <div className="ia-chat-action-grid">
                      <div>{renderPanel({ intent, topic: user.content }, message)}</div>
                      <div className="ia-chat-tools">{workflowContent}</div>
                    </div>
                  ) : (
                    <div className="ia-chat-tools">
                      {renderPanel(
                        { intent, topic: user.content },
                        message.status === "completed" ? message : undefined,
                      )}
                    </div>
                  ))}
                {text && message.status === "completed" && sessionId && (
                  <div className="ia-chat-actions ia-chat-reply-actions">
                    <button onClick={() => open("practice", undefined, message.id)}>
                      用自己的话练一遍
                    </button>
                    <button onClick={() => open("quiz", undefined, message.id)}>试一道题</button>
                    <SaveNote sessionId={sessionId} messageId={message.id} />
                  </div>
                )}
              </article>
            );
          })}
          {!workflowBesideBenchmark && workflowContent}
          {question && (
            <AnswerQuestion
              key={question.id}
              question={question}
              disabled={busy || !!generating || unavailable}
              submit={async (answer) => {
                const ok = await sendMessage(
                  "请按这道题评价我的作答，指出正确处、缺失项和原因。\n\n题目：" +
                    question.title +
                    "\n\n我的作答：" +
                    answer,
                  "interviewer",
                  { questionId: question.id },
                );
                if (ok && active.current) {
                  setQuestion(undefined);
                  removeQuery("question");
                }
                return ok;
              }}
            />
          )}
          {visiblePanel && (
            <div className="ia-chat-tools">
              {renderPanel(visiblePanel)}
              <button
                className="ia-chat-dismiss"
                onClick={() => {
                  setPanel(undefined);
                  removeQuery("kind");
                }}
              >
                收起工具
              </button>
            </div>
          )}
          {reports.map((id) => (
            <ReportCard
              key={id}
              id={id}
              close={() => {
                setHiddenReports((current) => [...current, id]);
                if (id === report) removeQuery("report");
              }}
            />
          ))}
          {params.has("report") && !report && <p role="alert">考试结果链接无效。</p>}
          {error && <p role="alert">{error}</p>}
        </div>
      </div>
      <Composer
        input={input}
        put={put}
        role={selectedRole}
        selectRole={showRole}
        palette={palette}
        togglePalette={(visible) => {
          setPalette(visible);
          if (!visible) removeQuery("palette");
        }}
        start={(id) => void start(id)}
        open={open}
        disabled={busy || unavailable}
        running={!!generating}
        status={busy ? "正在保存…" : undefined}
        send={() => void send()}
        interrupt={() => {
          if (generating) void changeReply(generating, "interrupt");
        }}
      />
      {memory && (
        <MemoryPanel
          sources={latest?.sources ?? []}
          onClose={() => {
            setMemory(false);
            removeQuery("memory");
          }}
        />
      )}
    </section>
  );
}

export default function ChatWorkspace(props: Props) {
  const { sessionId: raw } = useParams();
  const sessionId = positiveId(raw);
  return (
    <Conversation
      key={getWorkspace() + ":" + (raw ?? "new")}
      {...props}
      sessionId={sessionId}
      invalidId={!!raw && !sessionId}
    />
  );
}
