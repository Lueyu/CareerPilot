import type { Artifact } from "../../components/StudyArtifacts";

export const roles = [
  { id: "interviewer", name: "面试官", detail: "模拟面试和作答评估" },
  { id: "exam", name: "考官", detail: "组卷和正式考试" },
  { id: "coach", name: "教练", detail: "讲解、学习计划和练习" },
  { id: "analyst", name: "分析师", detail: "能力证据和岗位差距" },
  { id: "resume", name: "简历顾问", detail: "岗位匹配和项目表达" },
  { id: "collect", name: "素材管家", detail: "检索题库和导入面经" },
] as const;

export const workflows = [
  {
    id: "mock",
    name: "完整模拟面试",
    role: "interviewer",
    prompt:
      "先给我完整模拟面试的大纲，说明每个环节考察什么。准备好后从自我介绍开始，一次只问一个问题。",
  },
  {
    id: "prep",
    name: "岗位备战计划",
    role: "coach",
    prompt:
      "开始岗位备战计划。先确认我的目标岗位与 JD，解释你需要哪些信息以及怎样从要求推导学习优先级。",
  },
  {
    id: "digest",
    name: "面经深度消化",
    role: "collect",
    prompt: "开始面经深度消化。先引导我提供材料，说明如何提取考点、练习并沉淀错题。",
  },
  {
    id: "project",
    name: "项目经历打磨",
    role: "resume",
    prompt: "开始项目经历打磨。先请我选择项目，说明如何用 STAR 组织我本人有依据的项目经历。",
  },
  {
    id: "review",
    name: "错题本智能复习",
    role: "coach",
    prompt: "开始错题复习。请先问我想复习哪次报告里的失分项，并说明怎样选择能验证掌握的题。",
  },
  {
    id: "boost",
    name: "能力突破训练",
    role: "analyst",
    prompt: "开始能力突破训练。请说明怎样从独立作答定位薄弱项、安排专项练习和重评估。",
  },
] as const;

export type Role = (typeof roles)[number]["id"];
export type Workflow = (typeof workflows)[number]["id"];
export type Intent =
  | "exam"
  | "plan"
  | "practice"
  | "quiz"
  | "benchmark"
  | "jd"
  | "import"
  | "questions"
  | "flashcard"
  | "history";
export type WorkflowOperation =
  | { operation: "start"; workflow: Workflow }
  | { operation: "pause" | "resume" | "skip" | "advance" }
  | { operation: "change"; step?: number; label: string };

export interface WorkflowRun {
  id: Workflow;
  revision: number;
  current: number;
  status: "running" | "paused" | "completed";
  steps: string[];
  skipped: number[];
}

export interface ChatSource {
  kind: string;
  id: number | string;
  label: string;
  text?: string;
  provenance?: string;
}

export interface Message {
  id: number;
  role: "user" | "assistant";
  agent_role?: Role | null;
  request_id: string;
  content: string;
  status: "completed" | "failed" | "interrupted" | "generating";
  attempt: number;
  error?: string | null;
  sources?: ChatSource[];
  action?: string;
  blocks?: Artifact[];
}

export interface Session {
  id: number;
  title: string;
  status: "active" | "ended";
  context: { interview_workflow?: WorkflowRun; project_id?: number; topic?: string };
  messages: Message[];
}

export const changeEvent = "study-history-changed";

export function positiveId(value?: string | null): number | undefined {
  if (!value || !/^[1-9][0-9]*$/.test(value)) return undefined;
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : undefined;
}

export function chatUrl(sessionId?: number, report?: number) {
  return "/chat" + (sessionId ? "/" + sessionId : "") + (report ? "?report=" + report : "");
}

export function examConfigUrl(sessionId?: number, topic?: string) {
  const query = new URLSearchParams();
  if (sessionId) query.set("session", String(sessionId));
  if (topic) query.set("topic", topic);
  return "/chat/exam" + (query.size ? "?" + query : "");
}

const intentMatchers: readonly [Intent, RegExp, Role][] = [
  [
    "exam",
    /(?:组卷|组.{0,8}试卷|组.{0,8}考试|开始.{0,6}考试|出.{0,6}试卷|(?:create|start|configure).{0,12}\bexam\b)/i,
    "exam",
  ],
  [
    "benchmark",
    /(?:能力差距|岗位差距|岗位对标|能力对标|能力快照|competency benchmark|skill gap)/i,
    "analyst",
  ],
  [
    "jd",
    /(?:粘贴|导入|保存|设置).{0,12}(?:\bJD\b|职位描述|目标岗位)|(?:import|paste|save).{0,12}\bJD\b/i,
    "resume",
  ],
  [
    "import",
    /(?:导入|上传).{0,12}(?:面经|资料|材料|文件)|import.{0,8}(?:notes|materials)/i,
    "collect",
  ],
  ["questions", /题库|\bquestion bank\b/i, "collect"],
  ["history", /(?:考试记录|考试趋势|最近.{0,8}考试|考试历史|exam history)/i, "analyst"],
  ["plan", /学习计划|每日安排|今天学什么|study plan/i, "coach"],
  ["flashcard", /闪卡|复习卡|flashcards?/i, "coach"],
  ["quiz", /小测|测验|quiz/i, "coach"],
  [
    "practice",
    /(?:开始|安排|来|做|练).{0,8}(?:练习|道题)|(?:start|give me).{0,8}practice/i,
    "coach",
  ],
];

const roleMatchers: readonly [Role, RegExp][] = [
  ["interviewer", /模拟面试|扮演面试官|面试我|mock interview/i],
  ["resume", /简历|STAR|梳理.{0,8}项目|\bJD\b|resume/i],
  ["analyst", /分析.{0,10}(?:成绩|趋势|表现)|薄弱项|趋势分析/i],
  ["collect", /面经|素材/i],
];

export type ParsedPrompt =
  | { kind: "select"; role: Role; intent: Intent | null }
  | { kind: "message"; role: Role; intent: Intent | null; content: string };

export function parsePrompt(raw: string, selected: Role | null): ParsedPrompt {
  const command = /^\/([a-z]+)(?:\s+|$)/i.exec(raw.trim());
  const manual = roles.find((role) => role.id === command?.[1].toLowerCase())?.id;
  const content = (manual ? raw.trim().slice(command?.[0].length) : raw).trim();
  if (!content && manual) {
    return { kind: "select", role: manual, intent: manual === "exam" ? "exam" : null };
  }
  const match = intentMatchers.find(([, pattern]) => pattern.test(content));
  const role =
    manual ??
    selected ??
    match?.[2] ??
    roleMatchers.find(([, pattern]) => pattern.test(content))?.[0] ??
    "coach";
  return { kind: "message", content, role, intent: match?.[0] ?? null };
}

export function requestedWorkflow(raw: string): Workflow | undefined {
  return workflows.find(
    (workflow) =>
      raw.trim() === workflow.name ||
      new RegExp(
        "^(?:请|帮我)?(?:开始|启动|继续做|运行)\\s*" + workflow.name + "[。！!\\s]*$",
      ).test(raw.trim()),
  )?.id;
}

export function workflowCommand(
  raw: string,
): Exclude<WorkflowOperation, { operation: "start" }> | undefined {
  const text = raw.trim().replace(/[。！？!?]+$/, "");
  if (/^(?:暂停|先暂停)(?:技能|流程|训练|一下)?$/.test(text)) return { operation: "pause" };
  if (/^(?:继续|恢复)(?:技能|流程|训练)?$/.test(text)) return { operation: "resume" };
  if (/^跳过(?:这|本|当前)?(?:一步|一题|步骤|题)?$/.test(text)) return { operation: "skip" };
  if (/^(?:这步完成|下一步|完成本步)$/.test(text)) return { operation: "advance" };
  const change = /^(?:把|将)(.{1,60}?)(?:换成|改为|改成)(.{1,120})$/.exec(text);
  if (change) return { operation: "change", label: change[2] };
}

export function questionSearch(content: string) {
  const topic =
    /(?:从|在)?题库(?:里|中)?\s*(?:帮我)?(?:找|挑|抽|筛选?|查)(?:\s*\d+\s*道)?\s*(.+?)(?:的?(?:几道)?题[。！!？?]?)?$/.exec(
      content,
    )?.[1];
  return topic?.trim() ?? "";
}

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export function examTopic(request?: string) {
  if (!request) return undefined;
  const title =
    /(?:关于|围绕|针对|聚焦|主题[：:])\s*[「“"]?([^」”"，。？！\n]{1,60}?)(?=的?(?:模拟)?(?:考试|试卷|专项)|[」”"，。？！\n]|$)/
      .exec(request)?.[1]
      .trim();
  return title || undefined;
}

export function materialText(request?: string) {
  if (!request) return undefined;
  const separator = /[：:\n]/.exec(request);
  return separator ? request.slice(separator.index + 1).trim() || undefined : undefined;
}
