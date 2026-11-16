"""面经答案生成。

目标(PRD + 用户要求): 导入的面经常常只有问题、没有答案。系统要补上一份
**深入浅出、能教会人怎么答** 的深度参考答案, 要求:
- 积极运用类比(点明对应关系与类比失效之处);
- 给回答脚本(先说什么再说什么怎么收尾);
- 追问必须带参考答案(不是只列问题);
- 30 秒电梯稿 + 详细拆解 + 术语卡, 与公开题库 8 段式结构对齐。

两条路径:
1. LLM 可用(settings.llm_provider + llm_api_key): 深度教学式讲解(首选);
2. 规则引擎降级(默认): 能力域框架 + 关键词考点拼装(结构一致, 深度有限)。

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
        "followups": [
            {"q": "如果模型规划能力不够，你会怎么降低对它的依赖？",
             "a": "把规划从「一次生成完整计划」降级为「小步推进」：每步只让模型产出下一个动作，用确定性代码串联；关键路径用模板/规则替代模型决策，模型只负责参数填充；同时每步输出做 schema 校验，不合式就重试或转人工。规划能力不足时，架构上多用「约束」，少用「放手」。"},
            {"q": "多轮之后目标漂移，你怎么检测？",
             "a": "把任务目标与完成标准在会话开始时结构化落库（不是只放对话里）；每 N 步或每次工具调用后做一次轻量校验：当前状态对完成标准还有贡献吗？偏了就重注入目标摘要，或回滚到最近有效状态。漂移的本质是目标只存在于上下文里，治法是把它变成可校验的外部状态。"},
        ],
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
        "followups": [
            {"q": "工具返回的内容不可信，怎么防止它污染后续推理？",
             "a": "把工具返回当「数据」而不是「指令」：拼进 prompt 时明确标注来源与边界，禁止把返回内容放进 system 指令区；对返回做结构校验和字段白名单；高风险动作（写库/发消息）执行前二次确认。核心是：模型可以读它，但流程不无条件信它。"},
            {"q": "工具数量到 50 个以后，怎么让模型选对？",
             "a": "分层暴露：先按任务类型/域分组，第一层只给目录（每个工具一句话），模型选定域后再展开该域完整 schema；或检索式选择：把任务描述向量化，召回 top-K 工具再给模型。同时工具命名规范（动词+对象）和描述质量决定一大半准确率。"},
        ],
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
        "followups": [
            {"q": "多个 Agent 共享记忆时怎么隔离？",
             "a": "按 workspace/会话分区是底线；共享层只放确认过的、低敏的结构化事实（带来源与置信度）；私有上下文（草稿、对话原文）不出智能体边界；写入走审批或规则过滤，读取按角色授权。类比：共享的是公告栏，不是各自的工作台。"},
            {"q": "用户说「你记错了」，你怎么定位到那条记忆？",
             "a": "记忆条目必须带来源（哪次对话/哪份文档）与写入时间；按语义检索 top 候选给用户确认，确认后走「纠正」流程：旧条目标记 SUPERSEDED，写入新条目并关联原条目，而不是原地覆盖——保留轨迹才能审计，也才能回滚。"},
        ],
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
        "followups": [
            {"q": "召回率上不去，你会先怀疑哪一环？",
             "a": "按顺序排查：切分（chunk 是否破坏语义边界）→ 查询理解（用户问法和文档措辞是否对齐，要不要改写/扩展）→ 检索方式（纯向量对专有名词弱，上混合检索）→ rerank 阈值是否过严。用带标注的小评测集逐环归因，不要凭感觉调参。"},
            {"q": "多跳问题怎么处理？",
             "a": "链式检索：第一跳检索→生成中间结论（带引用）→用结论改写第二跳查询→再检索；或查询分解一次拆成子问题并行检索后合并。关键是中间结论也要过证据校验，否则错误会沿跳数放大。"},
        ],
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
        "followups": [
            {"q": "LLM 当裁判，怎么证明它判得准？",
             "a": "拿人工标注做对齐集：抽 100-200 条双标注，算裁判与人工的一致率（≥0.8 才可信）；prompt 用 rubric 拆维度打分而非整体印象分；边界样本用成对比较代替绝对打分；定期重跑对齐集防漂移。裁判是工具，不是真理。"},
            {"q": "线上没有标注，怎么评估？",
             "a": "代理指标 + 抽样人审：成功率、重试率、拒答率、用户显式反馈（点赞/重写）做日常监控；每周抽 N 条失败样本人工复盘；重要改动用小流量 A/B 对比代理指标。没有标注就先建标注管线，哪怕从失败样本开始——标注能力本身就是评估基建。"},
        ],
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
        "followups": [
            {"q": "流量涨十倍，先动哪里？",
             "a": "先压测量化瓶颈段（通常是 DB 慢查询与外部调用串行），再动手：外部调用并行化+缓存、DB 索引/读写分离、同步改异步队列削峰。同时算成本账：十倍流量不一定值十倍资源，很多时候加缓存和批处理就能扛住。"},
            {"q": "如何设计一个可回滚的发布？",
             "a": "每次变更可独立回滚：代码不可变版本+蓝绿/灰度切换，配置与代码分离且带版本；DB 变更向前兼容（先加后删，双写过渡）；发布前演练过回滚（不是「应该能回滚」而是「演练过」）；灰度期盯错误率与核心指标，异常自动回滚。"},
        ],
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
        "followups": [
            {"q": "如何验证幂等真的生效？",
             "a": "并发重放测试：同一幂等键并发 N 次相同请求，断言只产生一次副作用（一条订单/一条扣款）且返回同一结果；再测「执行到一半失败后重试」：断言从断点恢复而不是重复执行。验证要打在数据库副作用上，不是看返回值。"},
            {"q": "prompt 注入的检测放在哪一层？",
             "a": "多层防御：入口层对不可信内容（网页/文件/工具返回）做标记与过滤，不进指令区；执行层对敏感动作做白名单+二次确认；输出层校验动作参数。单点检测必然漏，防线设计成「即使注入成功也拿不到权限」。"},
        ],
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
        "followups": [
            {"q": "怎么设计一次安全的模型切换？",
             "a": "把模型当依赖治理：配置化+版本化，切流量用灰度（5%→25%→100%）；切换前用固定评测集跑基线对比，关键指标（成功率/延迟/成本）无回归才放量；新旧模型并行一段时间，异常一键切回；评测集与线上流量分布定期同步，防止「评测过了但线上翻车」。"},
            {"q": "成本突然翻倍，你怎么排查？",
             "a": "先归因：是调用量涨了还是单次成本涨了？单次成本拆成输入 token/输出 token/工具调用次数三段，对照近期变更（prompt 变长？检索 top-K 调大？模型换档？循环重试？）；再用 trace 定位到具体请求样本。成本监控要按请求维度，月底看账单只能算 autopsy。"},
        ],
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

LLM_SYSTEM = """你是资深 AI Agent 工程师兼面试教练，为面经题目补写「能教会人怎么答」的深度参考答案。读者是准备 P6 级别面试的 AI 应用工程师。
写作要求：
1. 深入浅出：每个技术点先用大白话讲清「为什么这么设计、怎么运作」，再给专业表述与关键参数/阈值/判断标准；讲机制时不跳步，把因果链条说完整；
2. 积极运用类比：至少一个贯穿全文的生活化类比，把抽象机制映射到日常事物；类比后要点明「对应关系」和「类比在哪里失效」，不许只贴一个比喻标签；
3. 教会怎么回答：给出回答脚本——先说什么、再说什么、怎么收尾，像教练给逐字稿大纲一样具体到句式；
4. 追问必须带答案：每个追问给出可直接口述的参考答案，不是只列问题；
5. 具体、可验证：避免「注意」「做好」这类空话，写到参数、比例、动作；提取舍时说明代价；
6. 只输出 JSON，不要多余文字。"""

LLM_USER_TMPL = """题目：{title}
能力域：{skill}（面试岗位：AI Agent 应用开发工程师，P6 级别）
{qtype_hint}
请输出 JSON（每个字段都必须有，内容宁多勿少、追求深度）：
{{
  "tldr": "一句话结论（40 字内）——面试开口先说的那句结论",
  "elevator": "30 秒电梯稿：面试时最先说出口的 4-6 句话，覆盖 结论+核心机制+一个亮点/数字（150-250 字）",
  "analogy": "通俗类比：一个贯穿全文的生活化类比讲透原理，点明与技术的对应关系，并说明类比在哪里失效（150-250 字）",
  "examiner": ["面试官通过这题想验证的 2-4 个点"],
  "steps": [{{"title": "拆解小节标题", "detail": "深入浅出讲透一个机制：为什么这么设计→怎么运作→关键参数/判断标准→失效边界（100-200 字）"}}],
  "how_to_answer": "回答脚本：这题先说什么（句式给出来）、按什么顺序展开、用什么收尾，让面试官跟上你的结构（150-250 字）",
  "pitfalls": ["2-4 个常见错误做法及直接后果"],
  "followups": [{{"q": "面试官可能的追问", "a": "追问的参考答案，可直接口述（100-200 字）"}}],
  "key_points": [{{"title": "评分要点", "detail": "命中该要点意味着候选人说清了什么"}}],
  "terms": [{{"term": "本题关键术语", "desc": "一句话解释（20-50 字）"}}]
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
    data["_source"] = "llm"
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

    # 追问带答案: 能力域框架内置 q/a 对
    followups = []
    seen_q: set[str] = set()
    for fu in frame["followups"]:
        q = fu["q"] if isinstance(fu, dict) else str(fu)
        if q in seen_q:
            continue
        seen_q.add(q)
        followups.append({"q": q, "a": fu.get("a", "") if isinstance(fu, dict) else ""})
    followups.append({"q": "如果线上已经出现这个问题，你的排查顺序是什么？", "a": "先看监控定位故障段（哪一跳错误率/延迟异常）→ 拉该段 trace 找具体请求样本 → 复现并二分（关掉可疑变更/开关）→ 修复后跑回归评测确认无副作用，最后补一条告警或预案防止复发。排查的原则是先止损（降级/回滚）再归因。"})
    followups = followups[:4]

    step_titles = [s[0] for s in uniq_steps]
    elevator = (
        f"开场先抛结论：{tldr} "
        f"然后一句话点出核心机制——重点讲「{step_titles[0] if step_titles else '整体思路'}」和"
        f"「{step_titles[1] if len(step_titles) > 1 else '验证方式'}」，"
        f"再给一个具体例子或参数收住，最后主动补一个常见坑展示工程判断力。"
    )
    how_to_answer = (
        f"按「结论 → 机制 → 例子 → 边界」四段式回答：①20 秒内先给结论（{tldr[:40]}…）；"
        f"②按 {'、'.join('「' + t + '」' for t in step_titles[:3])} 的顺序逐条展开机制，每条带一个具体参数或判断标准；"
        f"③用一个实际例子把机制落地（项目里怎么用的、踩过什么坑）；"
        f"④收尾主动说一个常见坑与规避方式，把话题引到你熟悉的深度。全程让面试官跟得上结构。"
    )

    return {
        "tldr": tldr,
        "elevator": elevator,
        "analogy": analogy,
        "examiner": frame["examiner"],
        "steps": [{"title": s[0], "detail": s[1]} for s in uniq_steps],
        "how_to_answer": how_to_answer,
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

    elevator = (data.get("elevator") or "").strip()
    if elevator:
        parts.append(
            '<div class="ans-elevator"><div class="ans-elevator-label">⏱ 30 秒电梯稿（先背这段）</div>'
            f"<p>{esc(elevator)}</p></div>"
        )

    analogy = (data.get("analogy") or "").strip()
    if analogy:
        parts.append("<h3>先把它讲人话（类比）</h3>")
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
        parts.append("<h3>详细拆解</h3>")
        items = []
        for i, (t, d) in enumerate(steps, start=1):
            head = f"<b>{esc(t)}</b>：" if t else ""
            items.append(f"<li>{head}{esc(d)}</li>")
        parts.append("<ol>" + "".join(items) + "</ol>")

    how_to = (data.get("how_to_answer") or "").strip()
    if how_to:
        parts.append(
            '<div class="ans-script"><div class="ans-script-label">🎤 这题怎么答（回答脚本）</div>'
            f"<p>{esc(how_to)}</p></div>"
        )

    pitfalls = [str(x).strip() for x in (data.get("pitfalls") or []) if str(x).strip()]
    if pitfalls:
        parts.append("<h3>常见坑</h3>")
        parts.append("<ul>" + "".join(f"<li>{esc(x)}</li>" for x in pitfalls) + "</ul>")

    # 追问: 新结构 [{q, a}], 兼容旧字符串列表
    followups: list[tuple[str, str]] = []
    for fu in data.get("followups") or []:
        if isinstance(fu, dict):
            q, a = str(fu.get("q", "")).strip(), str(fu.get("a", "")).strip()
            if q:
                followups.append((q, a))
        elif str(fu).strip():
            followups.append((str(fu).strip(), ""))
    if followups:
        parts.append("<h3>面试官追问（带参考答案）</h3>")
        blocks = []
        for q, a in followups:
            ans = f'<div class="fu-a">{esc(a)}</div>' if a else '<div class="fu-a fu-a-missing">（暂无参考答案，可在审核时补充）</div>'
            blocks.append(f'<div class="fu-item"><div class="fu-q">Q：{esc(q)}</div>{ans}</div>')
        parts.append('<div class="fu-list">' + "".join(blocks) + "</div>")

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

    terms = []
    for tm in data.get("terms") or []:
        if isinstance(tm, dict):
            w, d = str(tm.get("term", "")).strip(), str(tm.get("desc", "")).strip()
            if w:
                terms.append((w, d))
    if terms:
        parts.append("<h3>术语卡</h3>")
        rows = "".join(f"<tr><td><b>{esc(w)}</b></td><td>{esc(d)}</td></tr>" for w, d in terms)
        parts.append(
            '<table class="ans-rubric ans-terms"><thead><tr><th>术语</th><th>一句话解释</th></tr></thead>'
            f"<tbody>{rows}</tbody></table>"
        )

    tag = "AI 深度生成" if source == "llm" else "规则引擎生成"
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
