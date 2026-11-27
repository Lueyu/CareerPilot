import type { Locale } from "../../../i18n";

// 面试 AI 助手 · 工具层（仅在显式 Demo 工作区渲染）
// 本文件只提供固定示例数据与意图识别，不产生任何后端调用、不写入任何记录。

export type ToolIntent =
  "search" | "parse" | "sandbox" | "mindmap" | "calendar" | "translate" | "diff" | "skill";

export type ToolCardKind = ToolIntent;

// 顺序即优先级。只匹配明确要求演示工具的说法，避免把真实提问吞掉。
// 覆盖「搜/查/找 + 一下」「搜索 X」「查一下 X」等常见组合。
const intentMatchers: readonly [ToolIntent, RegExp][] = [
  ["translate", /翻译|润色|译成|翻成|改写.{0,8}(?:表达|说法)|polish/i],
  ["mindmap", /脑图|思维导图|知识结构图|画.{0,8}(?:导图|结构图)|mind\s?map/i],
  ["diff", /版本对比|\bdiff\b|对比.{0,8}(?:新旧|两个)?版本|历史版本/i],
  [
    "sandbox",
    /沙箱|运行(?:这段?|一下)?(?:代码|程序)|执行代码|跑一下(?:这个|这段)?代码|在线(?:编程|执行)|code\s?sandbox/i,
  ],
  [
    "parse",
    /解析.{0,6}(?:简历|文件|pdf|文档)|(?:简历|文件|pdf|文档).{0,6}解析|上传.{0,10}(?:简历|文件)|导入.{0,10}(?:简历|面经文件)/i,
  ],
  ["calendar", /学习提醒|设置提醒|添加提醒|提醒我|日历|日程|复习提醒|reminder/i],
  ["skill", /技能进度|技能参数|工作记忆|当前技能|skill\s?progress/i],
  [
    "search",
    /(?:搜|查|找)(?:索)?(?:一)?下|联网(?:搜索|查)|搜索|查(?:一)?查|web\s?search|\bsearch\b/i,
  ],
];

export function detectToolIntent(content: string): ToolIntent | null {
  const text = content.trim();
  if (!text) return null;
  return intentMatchers.find(([, pattern]) => pattern.test(text))?.[0] ?? null;
}

/** 演示消息流里的本地回合。id 为负数，避免与真实消息 id 冲突。 */
export type MockTurn = {
  id: number;
  role: "user" | "assistant";
  text?: string;
  card?: ToolCardKind;
  raw?: string;
};

export type DiffLine = { kind: "norm" | "del" | "ins"; text: string };

export interface AgentMemory {
  targetRole: string | null;
  weakPoints: string[];
  currentSkill: string | null;
  examCount: number;
}

export interface Reminder {
  id: number;
  title: string;
  time: string;
  type: "study" | "review" | "interview";
  done: boolean;
}

export interface MindMapNode {
  label: string;
  children?: MindMapNode[];
  hint?: string;
}

export interface SkillStep {
  label: string;
  status: "done" | "current" | "pending";
  meta: string;
}

export interface DemoSample {
  memory: AgentMemory;
  searchKeyword: string;
  searchResults: { title: string; summary: string }[];
  parsedFileName: string;
  parsedProjects: { name: string; stack: string[]; description: string }[];
  parsedSkills: string[];
  sandboxLanguage: string;
  sandboxCode: string;
  sandboxCases: { input: string; expect: string; actual: string; pass: boolean }[];
  sandboxAnalysis: string;
  mindMap: MindMapNode;
  mindMapTopic: string;
  reminders: Reminder[];
  translateZh: string;
  translateEn: string;
  polishBefore: { text: string; kind: "plain" | "del" }[];
  polishAfter: { text: string; kind: "plain" | "ins" }[];
  diffScope: string;
  diffOld: DiffLine[];
  diffNew: DiffLine[];
  skillSteps: SkillStep[];
  levels: readonly string[];
  focusOptions: readonly string[];
  weekdays: readonly string[];
}

const sampleCode = [
  "public int[] twoSum(int[] nums, int target) {",
  "    Map<Integer, Integer> seen = new HashMap<>();",
  "    for (int i = 0; i < nums.length; i++) {",
  "        int need = target - nums[i];",
  "        if (seen.containsKey(need))",
  "            return new int[]{seen.get(need), i};",
  "        seen.put(nums[i], i);",
  "    }",
  "    throw new IllegalArgumentException();",
  "}",
].join("\n");

const sampleCases = [
  { input: "nums = [2,7,11,15], target = 9", expect: "[0,1]", actual: "[0,1]", pass: true },
  { input: "nums = [3,2,4], target = 6", expect: "[1,2]", actual: "[1,2]", pass: true },
  { input: "nums = [3,3], target = 6", expect: "[0,1]", actual: "[0,1]", pass: true },
];

const zh: DemoSample = {
  memory: {
    targetRole: "示例岗位 · 后端研发",
    weakPoints: ["示例薄弱项 A", "示例薄弱项 B", "示例薄弱项 C"],
    currentSkill: "示例技能 · 第 2/5 步",
    examCount: 3,
  },
  searchKeyword: "Redis 持久化",
  searchResults: [1, 2, 3, 4, 5].map((n) => ({
    title: `示例面经条目 ${n}（非真实来源）`,
    summary: "示例摘要文本，仅展示版式，不代表真实来源或日期。",
  })),
  parsedFileName: "示例简历.pdf（未上传）",
  parsedProjects: [
    {
      name: "示例项目 A",
      stack: ["Java", "Spring Cloud", "MySQL"],
      description: "示例描述文本，仅展示版式，不代表任何人的真实经历。",
    },
    {
      name: "示例项目 B",
      stack: ["Redis", "Kafka", "Flink"],
      description: "示例描述文本，仅展示版式，不代表任何人的真实经历。",
    },
  ],
  parsedSkills: ["示例标签 1", "示例标签 2", "示例标签 3", "示例标签 4"],
  sandboxLanguage: "Java（示例）",
  sandboxCode: sampleCode,
  sandboxCases: sampleCases,
  sandboxAnalysis: "示例解析文本：这段代码用于展示沙箱版式，不会被执行，也不产生测试结果。",
  mindMap: {
    label: "示例主题",
    children: [
      {
        label: "示例分支 A",
        hint: "+3",
        children: [
          { label: "示例知识点 A1" },
          { label: "示例知识点 A2" },
          { label: "示例知识点 A3" },
        ],
      },
      {
        label: "示例分支 B",
        hint: "+2",
        children: [{ label: "示例知识点 B1" }, { label: "示例知识点 B2" }],
      },
      { label: "示例分支 C", children: [{ label: "示例知识点 C1" }] },
    ],
  },
  mindMapTopic: "示例知识结构",
  reminders: [
    { id: 1, title: "示例提醒：复习某个知识点", time: "示例时间", type: "review", done: false },
    { id: 2, title: "示例提醒：完成一次练习", time: "示例时间", type: "study", done: false },
    { id: 3, title: "示例提醒：已完成", time: "示例时间", type: "interview", done: true },
  ],
  translateZh: "示例中文原句，仅用于展示双栏版式。",
  translateEn: "Sample English sentence, shown only to demonstrate the two-column layout.",
  polishBefore: [
    { text: "示例原句：", kind: "plain" },
    { text: "被删除的示例片段", kind: "del" },
    { text: "，其后是示例结尾。", kind: "plain" },
  ],
  polishAfter: [
    { text: "示例润色：", kind: "plain" },
    { text: "新增的示例片段", kind: "ins" },
    { text: "，其后是示例结尾。", kind: "plain" },
  ],
  diffScope: "项目描述 · v2 → v3（示例）",
  diffOld: [
    { kind: "norm", text: "示例旧版本第 1 行" },
    { kind: "del", text: "示例被删除的第 2 行" },
    { kind: "norm", text: "示例旧版本第 3 行" },
    { kind: "del", text: "示例被删除的第 4 行" },
  ],
  diffNew: [
    { kind: "norm", text: "示例新版本第 1 行" },
    { kind: "ins", text: "示例新增的第 2 行" },
    { kind: "norm", text: "示例新版本第 3 行" },
    { kind: "ins", text: "示例新增的第 4 行" },
  ],
  skillSteps: [
    { label: "示例步骤 1 · 已完成", status: "done", meta: "示例时间" },
    { label: "示例步骤 2 · 已完成", status: "done", meta: "示例时间" },
    { label: "示例步骤 3 · 当前", status: "current", meta: "示例状态" },
    { label: "示例步骤 4 · 待开始", status: "pending", meta: "示例状态" },
    { label: "示例步骤 5 · 待开始", status: "pending", meta: "示例状态" },
  ],
  levels: ["基础", "进阶", "挑战"] as const,
  focusOptions: ["消息不丢失", "一致性优先", "吞吐优先"] as const,
  weekdays: ["一", "二", "三", "四", "五", "六", "日"] as const,
};

const en: DemoSample = {
  memory: {
    targetRole: "Sample role · Backend engineer",
    weakPoints: ["Sample gap A", "Sample gap B", "Sample gap C"],
    currentSkill: "Sample skill · step 2/5",
    examCount: 3,
  },
  searchKeyword: "Redis persistence",
  searchResults: [1, 2, 3, 4, 5].map((n) => ({
    title: `Sample interview note ${n} (not a real source)`,
    summary: "Sample summary text for layout only; no real source or date.",
  })),
  parsedFileName: "sample-resume.pdf (not uploaded)",
  parsedProjects: [
    {
      name: "Sample project A",
      stack: ["Java", "Spring Cloud", "MySQL"],
      description: "Sample description for layout only; not anyone's real experience.",
    },
    {
      name: "Sample project B",
      stack: ["Redis", "Kafka", "Flink"],
      description: "Sample description for layout only; not anyone's real experience.",
    },
  ],
  parsedSkills: ["Sample tag 1", "Sample tag 2", "Sample tag 3", "Sample tag 4"],
  sandboxLanguage: "Java (sample)",
  sandboxCode: sampleCode,
  sandboxCases: sampleCases,
  sandboxAnalysis:
    "Sample analysis: this code demonstrates the sandbox layout and is never executed.",
  mindMap: {
    label: "Sample topic",
    children: [
      {
        label: "Sample branch A",
        hint: "+3",
        children: [
          { label: "Sample point A1" },
          { label: "Sample point A2" },
          { label: "Sample point A3" },
        ],
      },
      {
        label: "Sample branch B",
        hint: "+2",
        children: [{ label: "Sample point B1" }, { label: "Sample point B2" }],
      },
      { label: "Sample branch C", children: [{ label: "Sample point C1" }] },
    ],
  },
  mindMapTopic: "Sample knowledge structure",
  reminders: [
    {
      id: 1,
      title: "Sample reminder: review a topic",
      time: "Sample time",
      type: "review",
      done: false,
    },
    {
      id: 2,
      title: "Sample reminder: finish an exercise",
      time: "Sample time",
      type: "study",
      done: false,
    },
    { id: 3, title: "Sample reminder: done", time: "Sample time", type: "interview", done: true },
  ],
  translateZh: "示例中文原句，仅用于展示双栏版式。",
  translateEn: "Sample English sentence, shown only to demonstrate the two-column layout.",
  polishBefore: [
    { text: "Sample original: ", kind: "plain" },
    { text: "removed sample phrase", kind: "del" },
    { text: ", followed by a sample ending.", kind: "plain" },
  ],
  polishAfter: [
    { text: "Sample polished: ", kind: "plain" },
    { text: "added sample phrase", kind: "ins" },
    { text: ", followed by a sample ending.", kind: "plain" },
  ],
  diffScope: "Project description · v2 → v3 (sample)",
  diffOld: [
    { kind: "norm", text: "Sample old line 1" },
    { kind: "del", text: "Sample removed line 2" },
    { kind: "norm", text: "Sample old line 3" },
    { kind: "del", text: "Sample removed line 4" },
  ],
  diffNew: [
    { kind: "norm", text: "Sample new line 1" },
    { kind: "ins", text: "Sample added line 2" },
    { kind: "norm", text: "Sample new line 3" },
    { kind: "ins", text: "Sample added line 4" },
  ],
  skillSteps: [
    { label: "Sample step 1 · done", status: "done", meta: "Sample time" },
    { label: "Sample step 2 · done", status: "done", meta: "Sample time" },
    { label: "Sample step 3 · current", status: "current", meta: "Sample status" },
    { label: "Sample step 4 · pending", status: "pending", meta: "Sample status" },
    { label: "Sample step 5 · pending", status: "pending", meta: "Sample status" },
  ],
  levels: ["Basic", "Intermediate", "Advanced"] as const,
  focusOptions: ["No message loss", "Consistency first", "Throughput first"] as const,
  weekdays: ["M", "T", "W", "T", "F", "S", "S"] as const,
};

export function demoSample(locale: Locale): DemoSample {
  return locale === "en" ? en : zh;
}

export function emptyDemoMemory(): AgentMemory {
  return { targetRole: null, weakPoints: [], currentSkill: null, examCount: 0 };
}
