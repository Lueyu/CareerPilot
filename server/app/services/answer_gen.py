"""面经答案生成。

目标(PRD + 用户要求): 导入的面经常常只有问题、没有答案。系统要能补上一份
**详细且通俗易懂** 的参考答案(含评分要点), 让题目进入题库后就能直接用来复习/组卷。

两条路径:
1. LLM 可用(settings.llm_provider + llm_api_key): 让模型按固定结构产出讲解;
2. 规则引擎降级(默认): 用能力域知识框架 + 关键词命中常见考点拼装讲解。

两条路径输出同一种结构, 渲染成 HTML(answer_html) + rubric(JSONB), 前端无差别展示。
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

from app.services.llm import AgentAdapter

# ---------------------------------------------------------------- 能力域框架

DOMAIN_FRAME: dict[str, dict[str, Any]] = {
    "agent": {
        "analogy": "Agent 像一个刚入职的同事：你给目标，它自己拆任务、找工具、交结果。"
                   "所以面试官关心的不是它「会不会聊」，而是它「会不会干活、干错了能不能发现」。",
        "examiner": [
            "能不能把模糊目标拆成可执行、可验证的步骤",
            "失败时是硬编码兜底，还是有规划层的恢复策略",
            "上下文与状态放在哪里，多轮之后还记不记得目标",
        ],
        "steps": [
            ("先定义可验证的完成标准", "把目标写成「输出什么、满足哪些约束、怎么判定成功」，而不是一句自然语言愿望。"),
            ("规划层与执行层分离", "规划产出结构化步骤（JSON），执行层逐步调用工具；每步有超时、重试上限与失败分类。"),
            ("每步留痕", "记录输入摘要、工具、耗时、结果状态，出问题能复盘到具体一步。"),
        ],
        "pitfalls": [
            "把「计划」和「执行」写在一个大 prompt 里，出错无法定位",
            "没有最大步数/预算上限，Agent 原地打转烧钱",
            "只测成功路径，没测工具超时、返回脏数据的情况",
        ],
        "followups": ["如果模型规划能力不够，你会怎么降低对它的依赖？", "多轮之后目标漂移，你怎么检测？"],
    },
    "tools": {
        "analogy": "工具调用像给同事配钥匙：钥匙越多越能干，但每把钥匙都可能开错门。"
                   "面试官看的是你有没有把「能用」做成「可控地能用」。",
        "examiner": [
            "工具描述是否清晰到模型不会误用（名称、参数、边界、示例）",
            "参数校验、超时、重试、幂等是否齐备",
            "MCP 这类协议解决的是接入标准化，还是能力边界问题",
        ],
        "steps": [
            ("把工具当 API 设计", "一个工具只做一件事；参数有类型和枚举；错误码语义明确，别让模型猜。"),
            ("调用前校验、调用中超时、调用后判定", "参数 schema 校验 → 超时/取消 → 用返回结构判断成功而不是「没报错就算成功」。"),
            ("危险操作加确认与幂等", "写操作要求显式确认或幂等键，避免重试导致重复下单/重复发送。"),
        ],
        "pitfalls": [
            "工具描述写成内部黑话，模型频繁传错参数",
            "把「HTTP 200」当成业务成功",
            "重试没有退避和上限，故障时把下游打挂",
        ],
        "followups": ["工具返回的内容不可信，怎么防止它污染后续推理？", "工具数量到 50 个以后，怎么让模型选对？"],
    },
    "memory": {
        "analogy": "记忆像人的工作台：桌面只放当前要用的，抽屉放常用的，仓库放归档的。"
                   "全堆桌面上会乱，全塞仓库又找不到。",
        "examiner": [
            "短期上下文、长期记忆、业务状态三者是否分开",
            "压缩/摘要会不会丢掉关键约束（数字、口径、截止时间）",
            "记忆写入是否可审计、可纠正、可删除",
        ],
        "steps": [
            ("分层", "会话窗口（短期）/ 用户画像与偏好（长期）/ 任务状态（结构化存储）分开存，各用各的读写策略。"),
            ("压缩讲策略", "按主题归档 + 保留「结论与约束」原文，丢弃过程性寒暄；压缩后再做一次关键信息校验。"),
            ("可纠正", "每条记忆带来源与置信度，用户能确认/纠正/删除，避免错误记忆长期污染。"),
        ],
        "pitfalls": [
            "把所有历史直接拼进 prompt，成本和延迟线性上涨",
            "摘要时把「预算 5 万」压成「有预算」，关键约束丢失",
            "记忆只写不读或只读不写，形同虚设",
        ],
        "followups": ["多个 Agent 共享记忆时怎么隔离？", "用户说「你记错了」，你怎么定位到那条记忆？"],
    },
    "rag": {
        "analogy": "RAG 像开卷考试：检索是翻书，生成是答题。翻错页，答得再漂亮也是错的。",
        "examiner": [
            "召回、重排、生成三段各自的指标和失败归因",
            "切分策略与业务语义是否匹配（按标题/问答对/代码块）",
            "引用与拒答：检索不到时敢不敢说不知道",
        ],
        "steps": [
            ("先把检索做对", "chunk 带上标题与来源；混合检索（向量 + 关键词）通常比单路稳；Top-K 后加 rerank。"),
            ("生成必须带引用", "每个结论绑定来源片段，无法引用就不回答，降低幻觉。"),
            ("分开评估", "检索看 recall@k / MRR，生成看忠实度与完整度，别用一个总分掩盖问题。"),
        ],
        "pitfalls": [
            "chunk 太大导致召回不准，太小导致语义断裂",
            "只看最终答案好坏，不知道是检索错还是生成错",
            "把整篇文档塞进上下文，成本高且稀释注意力",
        ],
        "followups": ["召回率上不去，你会先怀疑哪一环？", "多跳问题怎么处理？"],
    },
    "eval": {
        "analogy": "评估像体检：不是问「你感觉怎么样」，而是抽血、量血压，用指标说话。"
                   "所以评估体系的第一原则是：可复现、可归因。",
        "examiner": [
            "评估集怎么构造，是否覆盖真实分布与长尾",
            "指标是否可归因（检索错 / 生成错 / 工具错）",
            "线上怎么持续评估，而不是上线前跑一次",
        ],
        "steps": [
            ("定评测集", "从真实流量采样 + 人工标注 golden set；固定随机种子与版本，保证可比。"),
            ("分层指标", "端到端（成功率、人工偏好）+ 组件级（检索命中、工具正确率、格式合规）。"),
            ("LLM-as-judge 要校准", "用 rubric 拆维度、成对比较、定期与人工标注对齐一致性；别拿单一分数当真理。"),
        ],
        "pitfalls": [
            "评估集和训练/调 prompt 用的是同一批数据，指标虚高",
            "只测平均分，不看失败样本分布",
            "没有回归基线，改完不知道是变好还是变坏",
        ],
        "followups": ["LLM 当裁判，怎么证明它判得准？", "线上没有标注，怎么评估？"],
    },
    "backend": {
        "analogy": "后端是 Agent 的地基：模型再聪明，接口一超时就全盘皆输。",
        "examiner": [
            "并发与一致性：哪些操作必须事务，哪些可以最终一致",
            "超时/重试/降级的组合是否闭环",
            "数据模型能不能支撑后续的查询与审计",
        ],
        "steps": [
            ("先画数据流与状态机", "把一次请求经历的服务、存储、外部依赖列清楚，标出失败点。"),
            ("外部调用全部设超时", "连接超时与读超时分开；重试只对幂等操作做，带指数退避与抖动。"),
            ("可观测", "请求 ID 贯穿日志/指标/追踪，失败能一眼定位到哪一跳。"),
        ],
        "pitfalls": [
            "重试没有幂等保护，故障时产生重复数据",
            "同步串行调用外部服务，尾延迟被最慢的一环拖死",
            "只加日志不做结构化，出事只能 grep",
        ],
        "followups": ["流量涨十倍，先动哪里？", "如何设计一个可回滚的发布？"],
    },
    "reliability": {
        "analogy": "可靠性不是「不出错」，而是「出错时损失可控、能恢复」。",
        "examiner": [
            "失败分类：可重试 / 不可重试 / 需要人工",
            "幂等与去重：重复执行会不会造成副作用",
            "安全边界：prompt 注入、越权工具调用、敏感数据外泄",
        ],
        "steps": [
            ("失败分类再谈重试", "网络抖动、限流可重试；参数错误、权限不足重试无意义。"),
            ("幂等键 + 结果持久化", "同一业务键只允许成功一次，重试先查已存在结果。"),
            ("输入不可信", "工具返回、用户输入、检索内容都当不可信数据；指令与数据分离，危险动作二次确认。"),
        ],
        "pitfalls": [
            "无脑重试把偶发故障放大成雪崩",
            "把检索到的网页内容当系统指令执行",
            "错误吞掉不记录，线上问题无法复现",
        ],
        "followups": ["如何验证幂等真的生效？", "prompt 注入的检测放在哪一层？"],
    },
    "deploy": {
        "analogy": "部署与可观测性像开车时的仪表盘：不看仪表也能开，但不知道什么时候会抛锚。",
        "examiner": [
            "发布策略（灰度/回滚）与配置管理",
            "关键指标：延迟、错误率、成本、token 用量",
            "告警是否可行动，而不是噪音",
        ],
        "steps": [
            ("先定 SLI/SLO", "成功率、P95 延迟、单次成本，写清楚阈值再谈监控。"),
            ("灰度与回滚", "按流量比例放量，异常自动回滚；配置与代码分离，改配置不用重新发版。"),
            ("成本可核算", "按请求记录 token 与工具调用成本，异常上涨能定位到具体链路。"),
        ],
        "pitfalls": [
            "只监控机器指标，不监控业务成功率",
            "告警没有分级，最后没人看",
            "成本只在月底看账单，无法归因",
        ],
        "followups": ["怎么设计一次安全的模型切换？", "成本突然翻倍，你怎么排查？"],
    },
}

# ---------------------------------------------------------------- 关键词考点

# (匹配关键词, 考点内容) — 命中后与能力域框架合并, 让答案"答到点子上"
TOPIC_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "keys": ["幂等", "重复执行", "重复扣款", "重复下单", "重复发送"],
        "domain": "reliability",
        "tldr": "核心是给「同一件事」一个唯一标识，让重复执行变成无副作用。",
        "points": [
            ("幂等键", "由业务唯一标识生成（如 用户ID + 订单号 + 操作类型），不是随机 UUID。"),
            ("先查后写 + 唯一约束", "数据库唯一索引兜底，比应用层判断可靠。"),
            ("结果缓存", "第一次执行的结果持久化，重试直接返回同一结果。"),
            ("状态机", "用 PENDING/SUCCESS/FAILED 约束流转，避免「执行到一半」被再次触发。"),
        ],
        "pitfalls": ["只靠应用层 if 判断，并发下依然重复", "幂等键包含时间戳，等于没有幂等"],
    },
    {
        "keys": ["超时", "重试", "retry", "退避", "熔断", "降级"],
        "domain": "reliability",
        "tldr": "超时是常态，重试要有边界：只重试可重试的错，带退避和上限，并且保证幂等。",
        "points": [
            ("区分错误类型", "连接超时/限流/5xx 可重试；参数错误、鉴权失败重试只会浪费时间。"),
            ("指数退避 + 抖动", "避免同一时刻大量重试形成二次冲击。"),
            ("重试预算", "按请求设总时长预算，超过就降级返回，而不是无限重试。"),
            ("降级路径", "缓存兜底、跳过非关键步骤、转人工，保证主流程可用。"),
        ],
        "pitfalls": ["重试放大了副作用（重复写库）", "没有超时，线程被挂死"],
    },
    {
        "keys": ["记忆", "memory", "上下文过长", "上下文窗口", "压缩", "摘要"],
        "domain": "memory",
        "tldr": "记忆要分层：短期放对话窗口，长期放画像/偏好，任务状态放结构化存储，压缩时保留结论与约束。",
        "points": [
            ("分层存储", "会话消息 / 用户长期偏好 / 任务状态分开，读取策略不同。"),
            ("压缩保留约束", "摘要保留数字、口径、时间、否定条件，丢弃寒暄与重复。"),
            ("检索式回忆", "按当前任务检索相关历史片段，而不是全量拼接。"),
            ("可纠正", "记忆条目带来源和置信度，允许用户纠正/删除。"),
        ],
        "pitfalls": ["摘要把关键数字压丢", "记忆只增不减，成本随对话线性上涨"],
    },
    {
        "keys": ["rag", "检索", "召回", "向量", "embedding", "重排", "rerank", "知识库"],
        "domain": "rag",
        "tldr": "RAG 的成败八成在检索：先保证召回准，再谈生成好；生成必须带引用，检索不到要敢拒答。",
        "points": [
            ("切分", "按语义边界切（标题/问答对/段落），chunk 带来源与标题元数据。"),
            ("混合检索 + 重排", "向量召回 + 关键词召回互补，Top-K 后用 rerank 精排。"),
            ("引用与拒答", "答案绑定来源片段；证据不足时明确说「资料里没有」。"),
            ("分开评估", "召回率、命中位置、忠实度分别度量，定位问题环节。"),
        ],
        "pitfalls": ["chunk 过大导致语义稀释", "只看答案不看检索，问题无法归因"],
    },
    {
        "keys": ["评估", "评测", "eval", "指标", "benchmark", "golden", "judge"],
        "domain": "eval",
        "tldr": "评估要可复现、可归因：固定评测集与版本，分层看指标，别用一个总分掩盖问题。",
        "points": [
            ("评测集", "真实流量采样 + 人工标注，覆盖长尾与失败场景。"),
            ("分层指标", "端到端成功率/偏好 + 组件级命中率/正确率。"),
            ("LLM 裁判校准", "rubric 拆维度、成对比较、与人工标注对齐一致性。"),
            ("持续回归", "每次改动跑同一套集，记录基线差异。"),
        ],
        "pitfalls": ["评估集泄漏到调参过程", "没有基线，无法判断改动好坏"],
    },
    {
        "keys": ["注入", "injection", "越权", "安全", "防护"],
        "domain": "reliability",
        "tldr": "把外部内容一律当数据而不是指令：指令与数据分离，危险工具调用必须二次确认。",
        "points": [
            ("输入隔离", "检索内容、工具返回、用户上传都要标记为不可信，不进入 system 指令区。"),
            ("最小权限", "工具按需授权，写操作要求显式确认。"),
            ("输出校验", "对模型产出的动作做白名单校验，不直接执行。"),
            ("审计", "记录每次工具调用的参数与结果，异常可追溯。"),
        ],
        "pitfalls": ["把网页内容拼进 system prompt", "工具权限一刀切给全量"],
    },
    {
        "keys": ["多智能体", "multi-agent", "分工", "协作", "编排"],
        "domain": "agent",
        "tldr": "多 Agent 的收益在「分工降低单点复杂度」，代价是通信与一致性；先证明单 Agent 不够再上多 Agent。",
        "points": [
            ("角色边界", "每个 Agent 的输入输出契约写清楚，避免互相甩锅。"),
            ("共享状态", "用统一的任务看板/状态机协调，而不是靠自然语言互相喊话。"),
            ("失败恢复", "子任务失败要能重派或降级，整体有超时与预算。"),
            ("可观测", "每次委派都有 trace，能定位是哪个 Agent 出的问题。"),
        ],
        "pitfalls": ["为了架构好看而上多 Agent，成本翻倍收益为零", "Agent 之间无限对话，收敛不了"],
    },
    {
        "keys": ["工具", "tool", "mcp", "function call", "调用"],
        "domain": "tools",
        "tldr": "工具调用要像设计 API：职责单一、参数有约束、错误可判定、危险操作可确认。",
        "points": [
            ("描述清晰", "名称、用途、参数类型与示例齐全，模型才不会乱传。"),
            ("校验与超时", "调用前 schema 校验，调用中超时取消，调用后按结构判定成功。"),
            ("幂等与确认", "写操作带幂等键或人工确认，避免重试副作用。"),
            ("可观测", "记录工具名、参数摘要、耗时与结果状态。"),
        ],
        "pitfalls": ["工具描述含糊导致模型反复试错", "把 HTTP 200 当业务成功"],
    },
    {
        "keys": ["状态机", "状态管理", "state", "会话"],
        "domain": "agent",
        "tldr": "把「当前进行到哪一步」显式建模成状态机，比藏在对话历史里可靠得多。",
        "points": [
            ("显式状态", "状态枚举 + 允许的转移，非法转移直接拒绝。"),
            ("持久化", "状态落库而不是只放内存，进程重启可恢复。"),
            ("幂等转移", "同一事件重复到达不产生副作用。"),
            ("可回放", "记录状态变更历史，便于排查与补偿。"),
        ],
        "pitfalls": ["状态散落在多处变量里", "用对话历史隐式表达状态"],
    },
    {
        "keys": ["成本", "延迟", "缓存", "吞吐", "性能", "优化"],
        "domain": "deploy",
        "tldr": "先量化再优化：把延迟拆成模型/检索/工具三段，把成本拆成输入/输出/调用次数。",
        "points": [
            ("分段度量", "P50/P95 分开看，找到真正的瓶颈段。"),
            ("缓存", "相同检索/相同 prompt 结果缓存，注意失效策略。"),
            ("并行化", "独立的工具调用/检索并行，压缩尾延迟。"),
            ("模型分级", "简单任务用小模型，复杂任务才升级。"),
        ],
        "pitfalls": ["凭感觉优化，改完没有对比数据", "缓存不设 TTL，返回过期结果"],
    },
    {
        "keys": ["幻觉", "hallucination", "忠实", "引用"],
        "domain": "reliability",
        "tldr": "幻觉的根治手段是「有据可查」：答案绑定证据，证据不足就拒答。",
        "points": [
            ("证据约束", "生成时只允许基于给定片段，片段外内容不编造。"),
            ("引用校验", "答案里的每个事实点能对应到来源。"),
            ("置信度与拒答", "检索分数低时明确回答「资料不足」。"),
            ("回归评测", "用带标准答案的集合持续测忠实度。"),
        ],
        "pitfalls": ["为了「答得完整」强行补全", "引用只做装饰不校验"],
    },
]


def _match_topics(title: str, skill_key: str = "") -> list[dict[str, Any]]:
    """按关键词命中数挑考点块; 只命中 1 个词时要求与能力域一致, 避免「指标」这类泛词串味。"""
    low = title.lower()
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for i, t in enumerate(TOPIC_KNOWLEDGE):
        hits = sum(1 for k in t["keys"] if k.lower() in low)
        if not hits:
            continue
        if hits < 2 and t.get("domain") != skill_key:
            continue
        same = 1 if t.get("domain") == skill_key else 0
        scored.append((hits, same, i, t))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [x[3] for x in scored[:2]]


# ---------------------------------------------------------------- LLM 路径

LLM_SYSTEM = """你是资深 AI Agent 工程师，负责为面经题目补写参考答案。
要求：
1. 先给一句话结论，再用大白话解释（可以用生活类比），最后给可落地做法；
2. 面向准备面试的工程师，讲清楚「为什么」而不只是「是什么」；
3. 具体、可验证，避免空话套话；提到取舍时说明代价；
4. 只输出 JSON，不要多余文字。"""

LLM_USER_TMPL = """题目：{title}
能力域：{skill}
{qtype_hint}
请输出 JSON：
{{
  "tldr": "一句话结论（40 字内）",
  "analogy": "通俗类比，把原理讲给非专家听（80-150 字）",
  "examiner": ["面试官通过这题想验证的 2-4 个点"],
  "steps": [{{"title": "做法标题", "detail": "具体怎么做，含关键参数或判断标准（40-90 字）"}}],
  "pitfalls": ["2-4 个常见错误做法及后果"],
  "followups": ["2-3 个可能的追问"],
  "key_points": [{{"title": "评分要点", "detail": "命中该要点意味着候选人说清了什么"}}]
}}"""


async def _llm_answer(title: str, skill_key: str, skill_name: str, qtype: str) -> dict[str, Any] | None:
    adapter = AgentAdapter()
    if not adapter.active:
        return None
    qtype_hint = "题型为选择题，讲解要覆盖选项间的区别。" if qtype == "mcq" else "题型为开放题，按面试口述标准组织。"
    out = await adapter.chat(
        LLM_SYSTEM, LLM_USER_TMPL.format(title=title, skill=skill_name or skill_key, qtype_hint=qtype_hint), json_mode=True
    )
    if not out:
        return None
    content = (out.get("content") or "").strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("tldr"):
        return None
    return data


# ---------------------------------------------------------------- 规则路径

def _rule_answer(title: str, skill_key: str, skill_name: str) -> dict[str, Any]:
    frame = DOMAIN_FRAME.get(skill_key) or DOMAIN_FRAME["agent"]
    topics = _match_topics(title, skill_key)

    tldr = topics[0]["tldr"] if topics else (
        f"这题考的是「{skill_name}」里的工程判断：先说清楚目标与约束，再给可落地的做法和验证方式。"
    )
    if not topics:
        # 没有命中考点时, 用能力域的首个做法兜底成一句话
        tldr = f"{skill_name}的题，先给结论再展开：{frame['steps'][0][1]}"
    analogy = frame["analogy"]

    # 命中考点时以考点为主(讲解更贴题), 否则用能力域的通用做法兜底
    steps: list[tuple[str, str]] = []
    for t in topics:
        steps.extend(t["points"])
    if not steps:
        steps.extend((s[0], s[1]) for s in frame["steps"])
    seen: set[str] = set()
    uniq_steps: list[tuple[str, str]] = []
    for s in steps:
        if s[0] in seen:
            continue
        seen.add(s[0])
        uniq_steps.append(s)
    uniq_steps = uniq_steps[:6]

    pitfalls: list[str] = []
    for t in topics:
        pitfalls.extend(t["pitfalls"])
    if len(pitfalls) < 3:
        pitfalls.extend(frame["pitfalls"])
    pitfalls = list(dict.fromkeys(pitfalls))[:5]

    followups = list(dict.fromkeys([*frame["followups"], "如果线上已经出现这个问题，你的排查顺序是什么？"]))[:4]

    return {
        "tldr": tldr,
        "analogy": analogy,
        "examiner": frame["examiner"],
        "steps": [{"title": s[0], "detail": s[1]} for s in uniq_steps],
        "pitfalls": pitfalls,
        "followups": followups,
        "key_points": [
            {"title": s[0], "detail": s[1]} for s in uniq_steps[:4]
        ],
        "_source": "rule",
    }


# ---------------------------------------------------------------- 渲染

def render_html(title: str, skill_name: str, data: dict[str, Any], source: str) -> str:
    """把结构化讲解渲染成题库详情可直接展示的 HTML。"""
    esc = html.escape
    parts: list[str] = []

    tldr = (data.get("tldr") or "").strip()
    if tldr:
        parts.append(
            '<div class="ans-tldr"><div class="ans-tldr-label">一句话回答</div>'
            f'<p>{esc(tldr)}</p></div>'
        )

    analogy = (data.get("analogy") or "").strip()
    if analogy:
        parts.append("<h3>先把它讲人话</h3>")
        parts.append(f"<p>{esc(analogy)}</p>")

    examiner = [str(x).strip() for x in (data.get("examiner") or []) if str(x).strip()]
    if examiner:
        parts.append("<h3>面试官在考什么</h3>")
        parts.append("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in examiner) + "</ul>")

    steps = []
    for s in data.get("steps") or []:
        if isinstance(s, dict):
            t, d = str(s.get("title", "")).strip(), str(s.get("detail", "")).strip()
            if t or d:
                steps.append((t, d))
        elif str(s).strip():
            steps.append(("", str(s).strip()))
    if steps:
        parts.append("<h3>具体怎么做</h3>")
        items = []
        for i, (t, d) in enumerate(steps, start=1):
            head = f"<b>{esc(t)}</b>：" if t else ""
            items.append(f"<li>{head}{esc(d)}</li>")
        parts.append("<ol>" + "".join(items) + "</ol>")

    pitfalls = [str(x).strip() for x in (data.get("pitfalls") or []) if str(x).strip()]
    if pitfalls:
        parts.append("<h3>常见坑</h3>")
        parts.append("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in pitfalls) + "</ul>")

    followups = [str(x).strip() for x in (data.get("followups") or []) if str(x).strip()]
    if followups:
        parts.append("<h3>可能的追问</h3>")
        parts.append("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in followups) + "</ul>")

    key_points = []
    for k in data.get("key_points") or []:
        if isinstance(k, dict):
            t, d = str(k.get("title", "")).strip(), str(k.get("detail", "")).strip()
            if t or d:
                key_points.append((t, d))
        elif str(k).strip():
            key_points.append(("", str(k).strip()))
    if key_points:
        parts.append("<h3>评分要点</h3>")
        rows = "".join(
            f"<tr><td>{esc(t or '—')}</td><td>{esc(d)}</td></tr>" for t, d in key_points
        )
        parts.append(
            '<table class="ans-rubric"><thead><tr><th>要点</th><th>说清什么算命中</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
        )

    tag = "AI 生成" if source == "llm" else "规则引擎生成"
    parts.append(
        f'<p class="ans-foot">· 本题答案由{tag}补充，供复习参考，可手动修改。'
        f"能力域：{esc(skill_name or '—')}</p>"
    )
    return "\n".join(parts)


def build_rubric(data: dict[str, Any], title: str) -> dict[str, Any]:
    points = []
    for k in data.get("key_points") or []:
        if isinstance(k, dict):
            t = str(k.get("title", "")).strip()
            d = str(k.get("detail", "")).strip()
        else:
            t, d = str(k).strip(), ""
        if not t:
            continue
        # 关键词: 要点标题 + 描述里的技术词, 供规则评分命中
        kws = [w for w in re.split(r"[^\w\u4e00-\u9fff]+", t) if len(w) >= 2][:3]
        points.append({"title": t, "detail": d, "keywords": kws or [t[:10]]})
    if not points:
        points = [{"title": "要点", "detail": "", "keywords": [title[:10]]}]
    return {
        "points": points,
        "max_points": 5.0,
        "auto_generated": True,
    }


async def generate_answer(title: str, skill_key: str, skill_name: str, qtype: str = "open") -> tuple[str, dict[str, Any], str]:
    """返回 (answer_html, rubric, source) — source 为 llm/rule, 便于前端标注。"""
    data = await _llm_answer(title, skill_key, skill_name, qtype)
    source = "llm"
    if not data:
        data = _rule_answer(title, skill_key, skill_name)
        source = "rule"
    return render_html(title, skill_name, data, source), build_rubric(data, title), source
