import { t } from '../i18n';
// ============================================================
// CareerPilot 演示数据（DEMO）
// 全部为示例数据，非真实能力评估或行业统计。
// 后续接入后端后，此文件将被 API 数据替换。
// ============================================================

export interface DomainSkill {
  key: string;
  name: string;
  weight: number; // %
  current: number; // 0-5
  target: number; // 0-5
  confidence: number; // 0-1
  evidence: number;
  status: 'UNKNOWN' | 'NEEDS_VALIDATION' | 'BELOW_TARGET' | 'MEETS_TARGET' | 'ABOVE_TARGET';
  trend?: 'up' | 'down' | 'flat';
  next?: string;
}

export const DOMAINS: DomainSkill[] = [
  { key: 'agent', name: 'Agent 架构与规划', weight: 18, current: 3.2, target: 4.5, confidence: 0.62, evidence: 5, status: 'BELOW_TARGET', trend: 'up', next: '完成一次多智能体任务编排实践' },
  { key: 'tools', name: '工具调用 / MCP', weight: 14, current: 3.5, target: 4.0, confidence: 0.7, evidence: 4, status: 'BELOW_TARGET', trend: 'up', next: '补充 MCP 协议细节证据' },
  { key: 'memory', name: '状态管理与 Memory', weight: 12, current: 2.8, target: 4.0, confidence: 0.55, evidence: 3, status: 'NEEDS_VALIDATION', trend: 'flat', next: '复测记忆读写一致性' },
  { key: 'rag', name: 'RAG / 上下文工程', weight: 10, current: 3.6, target: 4.0, confidence: 0.74, evidence: 4, status: 'BELOW_TARGET', trend: 'up', next: '补充检索评估证据' },
  { key: 'eval', name: 'Agent Evaluation', weight: 16, current: 2.7, target: 5.0, confidence: 0.71, evidence: 6, status: 'BELOW_TARGET', trend: 'down', next: '完成独立诊断第 2 轮' },
  { key: 'backend', name: '后端与系统设计', weight: 12, current: 3.0, target: 4.5, confidence: 0.58, evidence: 3, status: 'BELOW_TARGET', trend: 'up', next: '补一次系统设计练习（P6 核心）' },
  { key: 'reliability', name: '可靠性与安全', weight: 12, current: 2.6, target: 4.0, confidence: 0.5, evidence: 2, status: 'NEEDS_VALIDATION', trend: 'flat', next: '为 Squabble 补充安全审计证据' },
  { key: 'deploy', name: '部署与可观测性', weight: 6, current: 3.6, target: 3.5, confidence: 0.79, evidence: 4, status: 'ABOVE_TARGET', trend: 'up', next: '无差距，可作为佐证输出' },
];

export const STATUS_META: Record<DomainSkill['status'], { label: string; cls: string; dot: string }> = {
  UNKNOWN: { label: 'UNKNOWN', cls: 'chip-slate', dot: 'dot-gray' },
  NEEDS_VALIDATION: { get label() { return t('progress.demoStatusUnknown'); }, cls: 'chip-amber', dot: 'dot-amber' },
  BELOW_TARGET: { get label() { return t('progress.demoStatusBelow'); }, cls: 'chip-red', dot: 'dot-red' },
  MEETS_TARGET: { get label() { return t('progress.demoStatusAt'); }, cls: 'chip-green', dot: 'dot-green' },
  ABOVE_TARGET: { get label() { return t('progress.demoStatusAbove'); }, cls: 'chip-purple', dot: 'dot-purple' },
};

export const WS = {
  name: '求职作战室',
  kind: '个人工作空间',
  role: 'AI Agent 应用开发工程师',
  level: 'P6',
  salary: '65-75 万',
  currentSalary: '约 50 万',
  gap: 1.2,
  updated: '2026-11-15',
  evidence: 27,
  jdCount: 3,
  benchmark: 'v1.1',
};

export const STATS = [
  { get label() { return t('progress.demoEvidenceCoverage'); }, value: 27, delta: '+3', dir: 'up', get foot() { return t('progress.demoAddedThisWeek'); }, ic: '🧾' },
  { get label() { return t('progress.demoUnverifiedCompetencies'); }, value: 9, get delta() { return t('progress.demoLowConfidenceRecords'); }, dir: 'flat', get foot() { return t('progress.demoConfidenceThreshold'); }, ic: '🔎' },
  { get label() { return t('progress.demoTodayTasks'); }, value: 3, get delta() { return t('progress.demoOneRetest'); }, dir: 'flat', get foot() { return t('progress.demoEstimatedTime'); }, ic: '📌' },
  { get label() { return t('progress.demoPendingImports'); }, value: 2, get delta() { return t('progress.demoWaitingForConfirmation'); }, dir: 'flat', get foot() { return t('progress.demoImportSource'); }, ic: '📥' },
];

export interface Task {
  id: string;
  type: '理解' | '实践' | '复测';
  title: string;
  why: string;
  domain: string;
  minutes: number;
  criteria: string;
  due?: string;
}
export const TODAY_TASKS: Task[] = [
  {
    id: 't1',
    type: '理解',
    title: '为什么 ReAct 循环需要显式状态记录',
    why: 'Agent Evaluation 证据不足',
    domain: 'Agent Evaluation',
    minutes: 20,
    criteria: '能用自己的话解释状态丢失导致的任务失败案例',
  },
  {
    id: 't2',
    type: '实践',
    title: '让工具安全地重试',
    why: '工具调用 / MCP 证据不足',
    domain: '工具调用 / MCP',
    minutes: 40,
    criteria: '在沙箱中完成一次带重试退避的工具调用并记录日志',
  },
  {
    id: 't3',
    type: '复测',
    title: 'RAG 检索评估指标速测',
    why: '1 天后复测',
    domain: 'RAG / 上下文工程',
    minutes: 10,
    criteria: 'Recall@k 与 MRR 概念速答正确率 ≥ 80%',
  },
];

export const RETEST_PLAN = [
  { day: '1 天后', tasks: ['RAG 检索评估指标速测'], date: '11-16' },
  { day: '3 天后', tasks: ['ReAct 显式状态记录 · 复述'], date: '11-18' },
  { day: '7 天后', tasks: ['工具重试策略 · 小考'], date: '11-22' },
];

export const TIMELINE = [
  { date: '11-15', text: '完成独立诊断，差距 <b>1.8/5</b>，新增 12 条证据', type: '诊断' },
  { date: '11-14', text: '导入面经「字节跳动 · Agent 研发」，收录 3 题', type: '面经' },
  { date: '11-12', text: '完成实践任务「MCP 工具调用边界」', type: '实践' },
  { date: '11-10', text: '更新岗位对标：对标 <b>P6</b> · 3 份 JD', type: '对标' },
];

export const PENDING_REVIEWS = [
  { id: 'IM-103', title: '字节跳动 · Agent 研发工程师面经', items: 3, time: '11-14 20:12' },
  { id: 'IM-102', title: '阿里云 · 大模型应用开发面经', items: 2, time: '11-12 09:30' },
];

export interface Evidence {
  id: string;
  type: '诊断' | '面经' | '实践' | '项目' | '复测';
  domain: string;
  summary: string;
  date: string;
  grade?: string;
  source?: string;
}
export const EVIDENCE: Evidence[] = [
  { id: 'EV-041', type: '诊断', domain: 'Agent Evaluation', summary: '独立诊断 Q2：工具重试策略回答获得 3/5，缺少退避与幂等设计', date: '11-15', grade: '3/5' },
  { id: 'EV-040', type: '诊断', domain: 'Agent 架构与规划', summary: '独立诊断 Q3：多智能体分工方案获得 4/5，任务分解清晰', date: '11-15', grade: '4/5' },
  { id: 'EV-039', type: '面经', domain: '工具调用 / MCP', summary: '面经收录：MCP 鉴权与超时处理的追问被面试官追问', date: '11-14' },
  { id: 'EV-038', type: '面经', domain: '状态管理与 Memory', summary: '面经收录：对话状态持久化方案得到正向反馈', date: '11-14' },
  { id: 'EV-037', type: '实践', domain: '工具调用 / MCP', summary: '完成 MCP 工具调用边界实践，通过验证', date: '11-12', grade: '通过' },
  { id: 'EV-036', type: '项目', domain: 'Agent Evaluation', summary: 'Squabble 项目审查：对抗评估循环实现，VERIFIED', date: '11-10' },
  { id: 'EV-035', type: '项目', domain: '可靠性与安全', summary: 'Squabble 安全审计：沙箱隔离不足，UNVERIFIED_CLAIM', date: '11-10' },
  { id: 'EV-034', type: '复测', domain: 'RAG / 上下文工程', summary: '检索评估指标复测：Recall@k 概念正确率 83%', date: '11-16', grade: '83%' },
  { id: 'EV-033', type: '面经', domain: '后端与系统设计', summary: '面经收录：任务队列与背压设计问题', date: '11-08' },
];

export const EXAMS = [
  {
    id: 'e1',
    icon: '🎯',
    get title() { return t('progress.demoDiagnosisName'); },
    get desc() { return t('progress.demoDiagnosisDescription'); },
    get meta() { return [t('progress.demoDiagnosisLength'), t('progress.demoDiagnosisFrequency')]; },
    primary: true,
  },
  {
    id: 'e2',
    icon: '🧪',
    get title() { return t('progress.demoFocusedName'); },
    get desc() { return t('progress.demoFocusedDescription'); },
    get meta() { return [t('progress.demoFocusedLength'), t('progress.demoFocusedDomain')]; },
  },
  {
    id: 'e3',
    icon: '📚',
    get title() { return t('progress.demoInterviewName'); },
    get desc() { return t('progress.demoInterviewDescription'); },
    get meta() { return [t('progress.demoInterviewLength'), t('progress.demoInterviewBankSize')]; },
  },
  {
    id: 'e4',
    icon: '💼',
    get title() { return t('progress.demoProjectName'); },
    get desc() { return t('progress.demoProjectDescription'); },
    get meta() { return [t('progress.demoProjectLength'), t('progress.demoProjectCount')]; },
  },
];

export const EXAM_HISTORY = [
  { name: '独立诊断', type: '诊断', count: 3, status: '已完成', score: 72, time: '11-15 14:20', report: true },
  { name: '专项考试 · Agent Evaluation v2', type: '专项', count: 8, status: '已完成', score: 68, time: '11-08 10:05', report: true },
  { name: '面经考试 · RAG 专题', type: '面经', count: 5, status: '已完成', score: 81, time: '11-02 16:40', report: true },
  { name: '独立诊断', type: '诊断', count: 3, status: '已完成', score: 55, time: '10-25 09:15', report: true },
  { name: '专项考试 · 工具调用', type: '专项', count: 8, status: '进行中', score: null, time: '10-18 20:30', report: false },
];

// —— 独立诊断（示例题目，DEMO）——
export interface QuizQuestion {
  id: string;
  type: 'mcq' | 'text';
  stem: string;
  context?: string;
  options?: string[];
  answer?: number;
  rubric?: { level: number; desc: string }[];
}
export const QUIZ: QuizQuestion[] = [
  {
    id: 'Q1',
    type: 'mcq',
    stem: '在 ReAct 循环中，Agent 完成一次工具调用后，最关键的下一步是什么？',
    context: '考察：Agent 架构与规划 / 状态管理',
    options: [
      'A. 立即发起下一次工具调用',
      'B. 将工具返回结果写入显式状态（observation）再决定下一步',
      'C. 结束循环并直接输出最终答案',
      'D. 清空上下文以节省 token',
    ],
    answer: 1,
  },
  {
    id: 'Q2',
    type: 'text',
    stem: '当工具调用因网络抖动失败时，请设计一个安全的重试策略。你会考虑哪些因素？',
    context: '考察：工具调用 / MCP / 可靠性',
    rubric: [
      { level: 5, desc: '包含退避、幂等、重试上限、超时与人工介入' },
      { level: 3, desc: '提到重试与幂等，但缺少退避或上限' },
      { level: 1, desc: '仅提到"再试一次"' },
    ],
  },
  {
    id: 'Q3',
    type: 'text',
    stem: '一个多智能体系统需要 3 个 Agent 协作完成数据分析任务。请给出任务分解与协作方案。',
    context: '考察：Agent 架构与规划',
    rubric: [
      { level: 5, desc: '职责划分清晰、通信协议明确、有失败回退' },
      { level: 3, desc: '有分工但缺少通信与失败处理' },
      { level: 1, desc: '无明确分工' },
    ],
  },
];

export const QUIZ_REPORT = {
  overall: 'Agent Evaluation 2.7/5',
  evidence: 27,
  confidence: 0.71,
  scores: [
    { q: 'Q1', title: 'ReAct 循环的下一步', type: 'MCQ', score: 5, max: 5, correct: true, note: '回答正确。能识别 observation 写入是循环的关键步骤。', evidence: ['EV-040'], next: '可挑战更复杂的多步规划题' },
    { q: 'Q2', title: '工具调用安全重试', type: '文本', score: 3, max: 5, correct: false, note: '提到了重试与幂等，但缺少指数退避、重试上限与人工介入设计。', evidence: ['EV-041'], next: '实践任务「让工具安全地重试」已加入今日任务' },
    { q: 'Q3', title: '多智能体任务分解', type: '文本', score: 4, max: 5, correct: false, note: '分工清晰、通信协议明确，失败回退机制描述不足。', evidence: ['EV-040'], next: '建议补充一次「编排失败恢复」专项练习' },
  ],
  summary: '整体表现中等偏上：架构与规划较好，工具调用的可靠性设计是当前主要短板。',
  next: '今日任务已为你生成 3 项：2 项理解/实践 + 1 项复测。',
};

// —— 项目与职业资产 ——
export const PROJECTS = [
  {
    id: 'squabble',
    name: 'Squabble',
    sub: 'AI 对抗编程平台',
    github: 'github.com/lueyu/squabble',
    scanned: '2026-11-10 14:02',
    matrix: [
      { key: 'agent', label: 'Agent 架构与规划', level: 3, weight: 18 },
      { key: 'eval', label: 'Agent Evaluation', level: 3, weight: 16 },
      { key: 'backend', label: '后端与系统设计', level: 3, weight: 12 },
      { key: 'reliability', label: '可靠性与安全', level: 2, weight: 12 },
    ],
    summary: '一个让 AI Agent 之间进行对抗编程的平台：Agent 撰写并提交代码，相互评审、修复与攻防。',
    stack: ['TypeScript', 'FastAPI', 'PostgreSQL', 'OpenAI API'],
  },
  {
    id: 'site',
    name: 'lueyu.github.io',
    sub: '个人网站 / 职业展示',
    github: 'github.com/lueyu/lueyu.github.io',
    scanned: '2026-11-10 14:05',
    matrix: [
      { key: 'deploy', label: '部署与可观测性', level: 4, weight: 6 },
      { key: 'rag', label: 'RAG / 上下文工程', level: 3, weight: 10 },
    ],
    summary: '个人职业展示站点，集成简历与作品集，近期加入基于 RAG 的问答机器人。',
    stack: ['Vite', 'React', 'GitHub Actions'],
  },
];

export const PROJECT_FINDINGS = [
  { level: 'VERIFIED', domain: 'Agent Evaluation', title: '对抗评估循环', detail: '实现 Agent 提交 → 互评 → 修复 → 重评的闭环，评估脚本在 tests/eval_loop.py', file: 'tests/eval_loop.py', evidence: 'EV-036' },
  { level: 'VERIFIED', domain: '后端与系统设计', title: '任务队列与背压', detail: '使用 PostgreSQL LISTEN/NOTIFY + 工作队列，具备背压控制', file: 'src/queue.py', evidence: 'EV-037' },
  { level: 'HIGH', domain: 'Agent 架构与规划', title: '多智能体任务编排', detail: '主控 Agent 对子 Agent 进行调度，包含超时与降级', file: 'src/orchestrator.py', evidence: 'EV-038' },
  { level: 'UNVERIFIED_CLAIM', domain: '可靠性与安全', title: '沙箱隔离', detail: 'README 声称代码运行在沙箱中，但未发现隔离实现', file: 'README.md', evidence: 'EV-035' },
  { level: 'UNVERIFIED_CLAIM', domain: '部署与可观测性', title: '高可用部署', detail: '声称 99.9% 可用性，缺少 SLO 定义与监控面板', file: 'deploy/', evidence: 'EV-039' },
];

export const PIPELINE_STEPS = [
  '检测项目变更',
  '提取证据',
  '映射能力',
  '对比现有展示',
  '生成内容草稿',
  '风险分级',
  '创建 PR',
  '发布',
];

export const DRAFT_CONTENT = {
  title: 'Squabble · 项目介绍（草稿）',
  risk: '低',
  riskReason: '全部为已 VERIFIED 证据，无未验证声明进入展示。',
  changed: ['新增：对抗评估循环描述', '新增：任务队列与背压设计', '修正：沙箱隔离描述（降级为"计划中"）'],
  build: '构建通过 · 部署预览通过',
};

export const PRS = [
  { id: '#28', title: '更新 Squabble 项目介绍', repo: 'lueyu.github.io', time: '11-15 10:20', state: '等待合并' },
  { id: '#26', title: '新增 RAG 问答机器人展示', repo: 'lueyu.github.io', time: '11-02 16:05', state: '已合并' },
];

// —— 记忆中心 ——
export interface MemoryItem {
  id: string;
  type: 'CareerGoal' | 'Preference' | 'KnowledgeGapSignal' | 'ProjectDecision' | 'Feedback';
  text: string;
  status: 'ACTIVE' | 'SUPERSEDED';
  date: string;
  source: string;
}
export const MEMORIES: MemoryItem[] = [
  { id: 'M-021', type: 'CareerGoal', text: '目标岗位：AI Agent 应用开发工程师（对标 P6），目标薪资 65-75 万，偏好应用层而非框架层开发', status: 'ACTIVE', date: '11-10', source: '岗位对标' },
  { id: 'M-020', type: 'ProjectDecision', text: 'Squabble 采用"对抗评估闭环"作为核心差异化，放弃纯评分器路线', status: 'ACTIVE', date: '11-08', source: '项目审查' },
  { id: 'M-019', type: 'KnowledgeGapSignal', text: '对工具调用可靠性设计（重试/幂等）理解不足，需要专项练习', status: 'ACTIVE', date: '11-15', source: '独立诊断' },
  { id: 'M-018', type: 'Feedback', text: '面试官反馈：多智能体分工回答结构清晰，但缺少失败恢复细节', status: 'ACTIVE', date: '11-14', source: '面经导入' },
  { id: 'M-017', type: 'Preference', text: '学习偏好：先实践后理论，示例优先于抽象概念', status: 'ACTIVE', date: '11-02', source: '对话导入' },
  { id: 'M-016', type: 'CareerGoal', text: '早期目标：全栈 AI 应用工程师（已被 M-021 取代）', status: 'SUPERSEDED', date: '09-28', source: '对话导入' },
];

export const CANDIDATE_MEMORIES = [
  { text: '更希望做 2C 产品，对纯 infra 岗位兴趣一般', source: '对话 · 11-15', type: 'Preference' },
  { text: 'Squabble 下一步计划支持自定义评估规则（JSON 配置）', source: '对话 · 11-15', type: 'ProjectDecision' },
  { text: '「进程内队列 vs 消息队列」理解模糊，可安排复测', source: '对话 · 11-15', type: 'KnowledgeGapSignal' },
];

// —— 系统观测台 ——
export const OBS_STATS = [
  { label: '执行次数', value: '1,284', ic: '⚡', foot: '近 30 天' },
  { label: '成功率', value: '99.2%', ic: '✅', foot: '近 30 天', tone: 'green' },
  { label: '平均延迟', value: '1.8s', ic: '⏱️', foot: '近 30 天' },
  { label: '成本', value: '¥42.5', ic: '💰', foot: '近 30 天' },
  { label: '失败', value: '9', ic: '⚠️', foot: '近 30 天', tone: 'amber' },
];

export const TRACES = [
  {
    id: 'TR-1042',
    name: '面经导入流水线',
    status: '成功',
    time: '11-15 10:21:07',
    cost: '¥0.42',
    spans: [
      { name: '解析文档', status: '成功', dur: '0.8s', cost: '¥0.05' },
      { name: '提取实体（公司/职级/日期）', status: '成功', dur: '1.1s', cost: '¥0.12' },
      { name: '技能映射', status: '成功', dur: '0.9s', cost: '¥0.11' },
      { name: '去重与变体合并', status: '成功', dur: '0.4s', cost: '¥0.06' },
      { name: '生成答案草稿', status: '成功', dur: '1.6s', cost: '¥0.08' },
    ],
  },
  {
    id: 'TR-1041',
    name: '项目变更检测（squabble）',
    status: '成功',
    time: '11-15 09:58:12',
    cost: '¥0.18',
    spans: [
      { name: 'git 变更检测', status: '成功', dur: '0.3s', cost: '¥0.02' },
      { name: '证据提取', status: '成功', dur: '0.7s', cost: '¥0.09' },
      { name: '能力映射', status: '成功', dur: '0.5s', cost: '¥0.07' },
    ],
  },
  {
    id: 'TR-1038',
    name: '复测任务生成',
    status: '失败',
    time: '11-14 18:02:44',
    cost: '¥0.00',
    spans: [
      { name: '读取技能状态', status: '成功', dur: '0.2s', cost: '¥0.01' },
      { name: 'LLM 生成题目', status: '失败', dur: '0.0s', cost: '¥0.00', fail: '上游超时（网关 504）' },
    ],
  },
];

export const DECISIONS = [
  {
    id: 'DC-204',
    title: '为今日任务选择「工具安全重试」实践题',
    time: '11-15 08:30',
    status: '已执行',
    evidence: ['EV-041', 'EV-035'],
    chosen: '安排 t2「让工具安全地重试」为今日实践任务',
    alternatives: [
      { text: '安排「MCP 鉴权」理论题', why: '证据覆盖已足够，边际收益低' },
      { text: '不安排实践，仅推送文章', why: '缺少行为证据，无法提升置信度' },
    ],
  },
  {
    id: 'DC-203',
    title: '面经 IM-103 是否进入题库',
    time: '11-14 20:30',
    status: '已执行',
    evidence: ['EV-039'],
    chosen: '收录 3 题并生成草稿答案',
    alternatives: [
      { text: '仅保存原文不收录', why: '与现有题库重复度较高' },
    ],
  },
];

export const ENGINES = [
  { key: 'job', name: 'Job Intelligence', desc: '岗位对标与差距分析', health: '健康', last: '11-15 09:00', stats: { runs: '312', ok: '99.4%' } },
  { key: 'memory', name: 'Memory Engine', desc: '记忆写入与检索', health: '健康', last: '11-15 10:22', stats: { runs: '2,041', ok: '99.8%' } },
  { key: 'project', name: 'Project Analyzer', desc: '项目证据提取', health: '健康', last: '11-15 09:58', stats: { runs: '86', ok: '97.7%' } },
  { key: 'competency', name: 'Competency Engine', desc: '能力估计与置信度', health: '健康', last: '11-15 10:23', stats: { runs: '1,128', ok: '99.5%' } },
  { key: 'learning', name: 'Learning Planner', desc: '任务生成与复测安排', health: '降级', last: '11-14 18:02', stats: { runs: '204', ok: '96.1%' } },
];

// —— 进化实验室 ——
export const EVOS = [
  {
    id: 'EVO-018',
    name: '考试选题策略 v3',
    desc: '按证据缺口权重选题，替代随机选题',
    baseline: { label: '基线 v2', mastery: 74.1, completion: 82.0, retest: 41.0, cost: '¥0.53/次' },
    candidate: { label: '候选 v3', mastery: 82.3, completion: 87.5, retest: 58.0, cost: '¥0.61/次' },
    deltas: { mastery: '+8.2%', completion: '+5.5%', retest: '+17.0%', cost: '+15%' },
    status: '评估中',
    sample: 23,
    started: '11-08',
    duration: '7 天',
    verdict: { regression: '无显著回归', recommendation: '建议批准：掌握度与复测完成率显著提升，成本增幅可接受' },
  },
  {
    id: 'EVO-016',
    name: '证据置信度阈值 0.5→0.6',
    desc: '提高"已验证"门槛，减少低置信断言',
    baseline: { label: '基线', mastery: null, completion: null, retest: null, cost: null },
    candidate: { label: '候选', mastery: null, completion: null, retest: null, cost: null },
    deltas: {},
    status: '已回滚',
    sample: 18,
    started: '10-20',
    duration: '5 天',
    verdict: { regression: '证据收录量下降 22%，任务生成稀疏', recommendation: '回滚至 0.5' },
  },
];

export const SETTINGS_RULES = [
  { id: 'r1', get name() { return t('progress.demoPublishLowRisk'); }, get desc() { return t('progress.demoPublishLowRiskDescription'); }, on: true },
  { id: 'r2', get name() { return t('progress.demoPullRequestOnly'); }, get desc() { return t('progress.demoPullRequestOnlyDescription'); }, on: true },
  { id: 'r3', get name() { return t('progress.demoHighRiskApproval'); }, get desc() { return t('progress.demoHighRiskApprovalDescription'); }, on: true },
  { id: 'r4', get name() { return t('progress.demoPublishBuild'); }, get desc() { return t('progress.demoPublishBuildDescription'); }, on: true },
  { id: 'r5', get name() { return t('progress.demoAutomaticRetests'); }, get desc() { return t('progress.demoAutomaticRetestsDescription'); }, on: false },
];

export const NOTIFICATIONS = [
  { ic: '📥', title: '面经解析完成', sub: 'IM-103 提取出 3 道题，等待你审核', time: '10 分钟前', kind: 'info' },
  { ic: '🔁', title: '复测提醒', sub: '「RAG 检索评估指标速测」今天到期', time: '1 小时前', kind: 'info' },
  { ic: '🔀', title: 'PR 等待合并', sub: 'lueyu.github.io #28 更新 Squabble 介绍', time: '2 小时前', kind: 'info' },
  { ic: '⚠️', title: '自动化失败告警', sub: 'TR-1038 复测生成超时（上游 504）', time: '昨天', kind: 'warn' },
];

export const QUICK_ACTIONS = [
  { ic: '📥', get label() { return t('progress.demoImportAction'); }, get desc() { return t('progress.demoImportActionDescription'); }, to: '/bank?import=1' },
  { ic: '🎯', get label() { return t('progress.demoDiagnosisAction'); }, get desc() { return t('progress.demoDiagnosisActionDescription'); }, to: '/exam/diagnosis' },
  { ic: '🛠️', get label() { return t('progress.demoPracticeAction'); }, get desc() { return t('progress.demoPracticeActionDescription'); }, to: '/learning' },
  { ic: '🔁', get label() { return t('progress.demoRetestAction'); }, get desc() { return t('progress.demoRetestActionDescription'); }, to: '/learning' },
  { ic: '📋', get label() { return t('progress.demoTaskAction'); }, get desc() { return t('progress.demoTaskActionDescription'); }, to: '/learning' },
  { ic: '🔍', get label() { return t('progress.demoScanAction'); }, get desc() { return t('progress.demoScanActionDescription'); }, to: '/assets' },
];

export const WORKSPACES = [
  { name: '求职作战室', kind: '个人工作空间', active: true },
  { name: '个人空间', kind: '个人工作空间', active: false },
];
