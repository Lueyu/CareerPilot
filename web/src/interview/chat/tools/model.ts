// 面试 AI 助手 · 工具层 + 上下文管理（mock 数据层）
// 全部交互均为纯前端模拟，不产生真实后端调用。

export type ToolIntent =
  | "search"
  | "parse"
  | "sandbox"
  | "mindmap"
  | "calendar"
  | "translate"
  | "diff"
  | "skill";

const intentMatchers: readonly [ToolIntent, RegExp][] = [
  ["translate", /翻译|润色|译成|翻成|改写.{0,8}(?:表达|说法)|polish/i],
  ["mindmap", /脑图|思维导图|知识结构图|画.{0,8}(?:导图|结构图)/],
  ["diff", /版本对比|\bdiff\b|对比.{0,8}(?:新旧|两个)?版本|历史版本/],
  [
    "sandbox",
    /沙箱|运行(?:这段?|一下)?(?:代码|程序)|执行代码|跑一下(?:这个|这段)?代码|在线(?:编程|执行)/,
  ],
  [
    "parse",
    /解析.{0,6}(?:简历|文件|pdf|文档)|(?:简历|文件|pdf|文档).{0,6}解析|上传.{0,10}(?:简历|文件)|导入.{0,10}(?:简历|面经文件)/i,
  ],
  ["calendar", /学习提醒|设置提醒|添加提醒|提醒我|日历|日程|复习提醒/],
  ["skill", /技能进度|技能参数|工作记忆|当前技能/],
  ["search", /搜(?:一)?下|联网(?:搜索|查)|搜索一下|查(?:一)?查|找(?:一)?下.{0,12}(?:面经|资料|帖)/],
];

export function detectToolIntent(content: string): ToolIntent | null {
  const text = content.trim();
  if (!text) return null;
  return intentMatchers.find(([, pattern]) => pattern.test(text))?.[0] ?? null;
}

/** 消息流里的本地模拟回合。id 为负数，避免与真实消息 id 冲突。 */
export type MockTurn = {
  id: number;
  role: "user" | "assistant";
  text?: string;
  card?: ToolCardKind;
  /** 触发该回合的原始用户输入，供搜索卡提取关键词。 */
  raw?: string;
};

export type ToolCardKind = ToolIntent;

export type DiffLine = {
  kind: "norm" | "del" | "ins";
  text: string;
};

export interface AgentMemory {
  targetRole: string | null;
  weakPoints: string[];
  currentSkill: string | null;
  examCount: number;
}

export const DEFAULT_MEMORY: AgentMemory = {
  targetRole: "字节跳动 · 后端研发",
  weakPoints: ["分布式事务", "Redis 持久化", "消息队列丢失"],
  currentSkill: "岗位备战计划 · 第 2/5 步",
  examCount: 3,
};

export const COMPRESSION_SUMMARY =
  "【摘要】用户先确认了目标岗位为字节跳动后端研发，随后围绕 Redis 持久化（RDB/AOF 取舍、混合持久化）、主从复制延迟与消息队列消息丢失三个薄弱点进行了问答。第 9 轮要求出 3 道分布式事务练习题，答对 2 道，「消息队列丢失场景」仍需巩固。";

const SEARCH_RESULTS = [
  {
    title: "2025 字节跳动后端研发面经（三轮技术面）",
    summary:
      "一面考了 Redis 持久化与主从复制，二面围绕分布式事务的一致性方案深挖，三面是系统设计：设计一个短链接服务。",
    site: "牛客网",
    time: "3 天前",
  },
  {
    title: "字节后端 2 面复盘：被消息队列丢失消息问倒了",
    summary:
      "面试官先问 Kafka 如何保证不丢消息，追问 ISR 机制与 acks=all 的代价，最后让手写生产者重试的伪代码。",
    site: "掘金",
    time: "1 周前",
  },
  {
    title: "字节跳动校招后端面经汇总（含答案要点）",
    summary:
      "整理了 30 篇字节后端面经的高频题：MySQL 索引、Redis 缓存穿透、RPC 框架、一致性哈希与限流算法。",
    site: "CSDN",
    time: "2 周前",
  },
  {
    title: "头条-抖音后端实习面经：全程被追问项目细节",
    summary:
      "项目里写的「本地消息表」被连续追问了四层：为什么不用事务消息、补偿任务失败怎么办、幂等键怎么设计。",
    site: "知乎",
    time: "3 周前",
  },
  {
    title: "字节后端一面算法题实录（附讲解思路）",
    summary: "LRU 缓存 + 合并区间两道题，面试官要求边写边讲复杂度，写完追问并发场景下的缓存一致性。",
    site: "力扣讨论区",
    time: "1 个月前",
  },
];

export function searchResults(keyword: string) {
  return { keyword, total: SEARCH_RESULTS.length, items: SEARCH_RESULTS };
}

export const parsedResume = {
  fileName: "张三_字节后端_简历.pdf",
  projects: [
    {
      name: "电商订单系统",
      stack: ["Java", "Spring Cloud", "MySQL"],
      description:
        "负责订单履约链路，用本地消息表 + 补偿任务保证最终一致性，峰值 3k QPS。",
    },
    {
      name: "实时推荐服务",
      stack: ["Redis", "Kafka", "Flink"],
      description: "重构特征召回管道，P99 延迟从 480ms 降到 120ms，缓存命中率 92%。",
    },
  ],
  skills: ["分布式", "高并发", "微服务", "消息队列", "缓存设计"],
};

export const sandboxCase = {
  language: "Java",
  title: "两数之和 · 哈希表一次扫描",
  starterCode: [
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
  ].join("\n"),
  tests: [
    { input: "nums = [2,7,11,15], target = 9", expect: "[0,1]", actual: "[0,1]", pass: true },
    { input: "nums = [3,2,4], target = 6", expect: "[1,2]", actual: "[1,2]", pass: true },
    { input: "nums = [3,3], target = 6", expect: "[0,1]", actual: "[0,1]", pass: true },
  ],
  analysis:
    "哈希表以「值 → 下标」缓存已遍历元素，一次扫描完成两数查找，时间复杂度 O(n)、空间 O(n)。面试追问点：为什么先查再存可以覆盖重复元素用例。",
};

export interface MindMapNode {
  label: string;
  children?: MindMapNode[];
  hint?: string;
}

export const mindMap: MindMapNode = {
  label: "Redis",
  children: [
    {
      label: "持久化",
      hint: "+3",
      children: [
        { label: "RDB 快照 vs AOF 追加" },
        { label: "混合持久化与恢复流程" },
        { label: "AOF 重写触发时机" },
        { label: "fork 阻塞与 copy-on-write" },
      ],
    },
    { label: "主从复制", hint: "+2", children: [{ label: "全量 vs 增量同步" }, { label: "复制延迟的监控" }] },
    { label: "缓存三兄弟", hint: "+3", children: [{ label: "穿透：布隆过滤器" }, { label: "击穿：逻辑过期" }, { label: "雪崩：随机 TTL" }] },
    { label: "分布式锁", children: [{ label: "Redlock 的争议" }, { label: "看门狗续期" }] },
  ],
};

export interface Reminder {
  id: number;
  title: string;
  time: string;
  type: "学习" | "复习" | "面试";
  done: boolean;
}

export const initialReminders: Reminder[] = [
  { id: 1, title: "复习：Redis 持久化对比", time: "今天 20:00 · 40 分钟", type: "复习", done: false },
  { id: 2, title: "模拟面试：分布式事务专题", time: "周四 19:30", type: "面试", done: false },
  { id: 3, title: "完成 5 道消息队列练习题", time: "昨天已完成", type: "学习", done: true },
];

export const translatePair = {
  zh: "负责订单履约链路的开发，用本地消息表加补偿任务保证最终一致性，峰值流量大概三千 QPS。",
  en: "Owned the order fulfilment pipeline, ensuring eventual consistency via a local message table plus compensating tasks, sustaining ~3k QPS at peak.",
  polish: {
    before: [{ text: "负责订单系统的", kind: "plain" as const }, { text: "开发和维护", kind: "del" as const }, { text: "，保证一致性。", kind: "plain" as const }],
    after: [
      { text: "主导订单系统", kind: "plain" as const },
      { text: "履约链路建设", kind: "ins" as const },
      { text: "，通过本地消息表保证最终一致性。", kind: "plain" as const },
    ],
  },
};

export const diffVersions: { scope: string; old: DiffLine[]; new: DiffLine[] } = {
  scope: "项目描述 · v2 → v3",
  old: [
    { kind: "norm" as const, text: "负责订单系统的开发和维护，" },
    { kind: "del" as const, text: "参与了订单模块的开发，" },
    { kind: "norm" as const, text: "保证下单和履约的数据一致。" },
    { kind: "del" as const, text: "熟悉分布式相关知识。" },
  ],
  new: [
    { kind: "norm" as const, text: "主导订单系统履约链路建设，" },
    { kind: "ins" as const, text: "通过本地消息表与补偿任务保证最终一致性，" },
    { kind: "norm" as const, text: "支撑大促期间峰值 3k QPS。" },
    { kind: "ins" as const, text: "主导缓存与消息队列的稳定性专项。" },
  ],
};

export interface SkillStep {
  label: string;
  status: "done" | "current" | "pending";
  meta: string;
}

export const skillSteps: SkillStep[] = [
  { label: "确认目标岗位与 JD 要求", status: "done", meta: "已完成 · 今天 14:02" },
  { label: "推导学习优先级", status: "done", meta: "已完成 · 今天 15:20" },
  { label: "分布式事务专项练习", status: "current", meta: "当前 · 3 道练习题" },
  { label: "消息队列丢失场景复测", status: "pending", meta: "待开始" },
  { label: "岗位模拟面试验收", status: "pending", meta: "待开始" },
];

export const intentReplies: Record<ToolIntent, string> = {
  search: "我联网搜索了相关面经与资料，已按相关度排序，可以逐条保存到面经库：",
  parse: "解析完成，提取到 2 段项目经历和一组技能标签，确认后会写入项目背景库：",
  sandbox:
    "已打开代码执行沙箱，预置了一道两数之和。可以直接修改代码并运行，每个测试用例会即时比对输出：",
  mindmap: "脑图已生成，点击节点可以展开或折叠，右上角可导出图片：",
  calendar: "已把当前学习计划同步到日历，带点的日期有提醒，也可以直接添加新的提醒：",
  translate: "翻译与润色结果如下，左右两栏都可以一键复制，润色部分标出了改动：",
  diff: "这是两版项目描述的差异对比，确认没问题可以恢复到旧版本：",
  skill: "当前工作记忆如下：技能正在第 3 步，临时参数可以直接在卡片里调整，下一步开始生效：",
};
