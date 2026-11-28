# CareerPilot 已知短板与工业级差距清单

> 审计日期：2026-11-27（2026-11-27 二轮代码级复审与上海 AI Agent 社招竞争力重排）。基于当前工作区源码的只读审计，所有条目附代码位置。
> 交互版看板：`docs/known-issues/index.html`；施工时间线：`docs/known-issues/timeline.html`。由 `scripts/build_known_issues_page.py` 从本文件生成，**本文件是唯一事实源**，改动后重跑脚本同步网页。
> 目标：**聚焦上海 AI Agent 社招核心竞争力**（Agent Loop / Hybrid RAG / Memory 闭环 / Eval 校准 / LLM 网关与上下文工程 / 分布式可靠性），前 4 波约 5 周拿下核心高杠杆项，第 5 波收拢通用 Web/SaaS 运维杂活，避免精力分散。
>
> 严重度定义（按 **AI Agent 岗面试竞争力 + 生产致命度** 校准）：
> - **P0**＝Agent 核心能力缺口、评分/记忆/流式核心逻辑漏洞、或必被资深面试官击穿的硬洞（**前 3 波必做**）；
> - **P1**＝工业级 Agent 标配或显著加分项（第 3–4 波）；
> - **P2**＝通用 Web/SaaS 运维安全债、外围功能或规模化进阶能力（第 5 波按需做或写 ADR 标 `ACCEPTED`，不抢占主战场时间）。
>
> 状态标记：`OPEN`（未动）/ `IN_PROGRESS` / `DONE` / `ACCEPTED`（明确接受该取舍并准备好话术）。

---

## A. 检索与知识管理

### A1. 资料库检索无语义能力、无相关性排序 `P0` `OPEN`
- **现状**：正则门控（提到"题库/资料库"才触发）+ 手工停用词 + 多字段 `ILIKE '%term%'` OR + 固定排序（按 id/时间）。无 embedding、无 FTS/BM25、无 rerank、无图谱。
- **代码**：`server/app/services/study_chat_context.py:35-150`（`LIBRARY_REFERENCE`、`library_terms`、`_question_match_filters`、`order_by(Question.id).limit(12)`）；`server/requirements.txt`（无任何检索库）。
- **为什么是问题**：上海 AI Agent 社招必拷打 RAG 工程（切块/召回/排序/评测）。当前无语义泛化、无相关性排序（最相关可能被 limit(12) 截掉）、中文子串误命中——只靠“口头讲 tradeoff”撑不住深度追问，必须有实测对比实验。
- **目标方案**：① 先建评测集（A4）；② PG `tsvector` + jieba 离线分词做 BM25（零新服务）；③ pgvector + bge-small-zh/API embedding 混合召回 + RRF 融合；④ cross-encoder rerank top-50→top-8。每步输出 Recall@K / MRR / NDCG 对比表。

### A2. 中文查询理解靠停用词表，泛查句式残留检索词导致零命中 `P0` `OPEN`
- **现状**：无分词，中文取 2–6 字连续汉字切片；"题库里有哪些关于 X 的题"中的指引词（"里有哪些/关于/列出"）不在停用词表内，残留成检索词后三路全 miss。英文停用词表缺 do/does/from/can/could。
- **代码**：`server/app/services/study_chat_context.py:38-60`（`_LATIN_STOP`、`_CJK_STOP`、`library_terms`）；零命中路径测试 `server/tests/test_study_chat_library.py:91-101`。修复已在 PR #37 工作树（`.agent-runs/20261126-chat-library-ops/library/`：停用词按长度降序替换、显式主题抽取正则、清单请求绕过 LLM 直出），**未合入当前检出**。
- **为什么是问题**：已发生的真实用户事故，且修复停留在工作树。查询理解错误会让下游一切检索升级失效——先修 query understanding 再谈召回。
- **目标方案**：合入并验证 PR #37；长期用 jieba 分词 + 词性过滤替代停用词枚举；把泛查句式加入回归评测集。

### A3. 命中总数与注入条数落差，无"检索结果完整性"契约 `P1` `OPEN`
- **现状**：题库命中可达 30 条但只注入 12 条来源，模型不知情，曾产出英文坏表格；笔记只取 3 条、面经 6 条，均无"共 N 条，展示前 K 条"的显式声明。
- **代码**：`server/app/services/study_chat_context.py:106-148`（`limit(12)`、summary 组装）；事故记录 `.agent-runs/20261126-chat-library-ops/library-pr.md:3`。
- **为什么是问题**：LLM 拿到截断样本却以为是全集，会做出错误的全称判断（"题库只有这些"）。工业级 RAG 的 context 组装必须携带 total/offset/truncated 元信息。
- **目标方案**：sources 摘要带 `total_hits`/`shown`/`truncated` 字段并在 prompt 合同中声明；清单类请求走确定性直出（PR #37 已含）；分页检索工具化（配合 B1）。

### A4. 检索无评测集、无召回基线 `P0` `OPEN`
- **现状**：路由有冻结评测集（`server/acceptance/auto_quality_v1.json`，44+24 题，SHA-256 固定），但**资料库检索没有任何 query→相关文档标注集**，recall/MRR 无从谈起。
- **代码**：`server/acceptance/auto_quality.py:28-36`（只有路由评测）；全仓无检索评测。
- **为什么是问题**：没有基线就无法证明任何检索升级有效，也无法防回归。这是"做过实验"与"堆技术名词"的分水岭，面试必问。
- **目标方案**：从真实查询日志 + 人工构造 50–100 条 query→相关题/笔记标注；写离线评测脚本输出 recall@12 / MRR / 零命中率；每次检索改动跑基线对比，结果落 `docs/benchmarks/`。

### A5. 项目代码证据检索是手写字面打分器，无符号级理解 `P2` `OPEN`
- **现状**：CamelCase/snake_case 拆词 + 汉字 trigram + 中英别名词典 + 手工线性加权（20×exact + 7×direct + …）；无 AST/符号索引，找不到"定义在哪、被谁调用"。
- **代码**：`server/app/services/study_evidence_search.py:28-65,94-157`；`study_project_evidence.py:253-340`（定义/调用者/测试角色加分是启发式）。
- **为什么是问题**：代码问答的证据质量决定讲解质量；字面匹配对重构/别名/跨文件调用无能为力。工业级代码 agent 用 LSP/tree-sitter 符号索引。
- **目标方案**：tree-sitter 建符号表（定义/引用/调用边），检索按符号图扩展一跳；保留字面分作为 fallback；用"证据命中率"评测。

### A6. 记忆召回无相关性排序 `P1` `OPEN`
- **现状**：记忆召回全是 SQL 条件过滤 + 时间排序取前 N（学习记忆前 5、审核反馈前 8、项目记忆前 2）；记忆条数增长后“最近≠最相关”（注：聊天主链路彻底不接记忆的断链漏洞单列在 C5）。
- **代码**：`server/app/services/learning_memory.py:29-40`、`bank_review_memory.py:80-110`、`project_memory.py:17-22`。
- **为什么是问题**：记忆量级增长后按时间截断会把核心长期偏好/薄弱点挤出窗口，工业级 Memory 召回需要相关性 + 时效/重要度联合打分。
- **目标方案**：先修 C5 打通闭环，再按 skill/主题标签 + A1 混合检索做相关性召回，叠加时间衰减权重。

### A7. 代码证据检索标识符硬剪枝 + 文件名 Top-100 截断 + 面经/笔记全量进内存扫描 `P1` `OPEN`
- **现状**：① `study_evidence_search.py:149-150` 写死 `if self.identifiers and not exact: return 0`，提问带任何大小写稍异或误判标识符直接整文件归零；② `study_project_evidence.py:217-221` 仅按文件名打分取前 100 才打开读内容，按业务语义提问时文件名未命中则直接漏召回；③ `study_chat_context.py:115-143` 把整个 workspace 的 `ImportSession` 和 `StudyNote` 全量拉回 Python 内存做 `casefold()` 子串遍历。
- **代码**：`server/app/services/study_evidence_search.py:146-157`；`server/app/services/study_project_evidence.py:217-221`；`server/app/services/study_chat_context.py:115-143`。
- **为什么是问题**：代码问答漏召回与性能退化的直接元凶，面试官看检索代码一眼就能挑出的明显硬伤。
- **目标方案**：标识符精确命中改为高权重加分而非零容忍剪枝；代码内容召回改为倒排/ripgrep/语义索引替代“仅文件名 Top-100”；面经与笔记检索下推数据库 FTS/pgvector（与 A1 统一）。

---

## B. Agent 编排与自主性

### B1. 无 tool-calling 循环，模型无法主动获取信息 `P0` `OPEN`
- **现状**：LLM 出口从不传 `tools`/`tool_choice`（甚至 `llm_stream.py:124-125` 遇到 `tool_calls` 直接报错拒绝）；每轮最多 2 次固定用途调用（产物修复/档位 failover）；检索由代码预先执行，模型不能按推理结果动态调用工具再查/再读；`interview_workflow` 只是用户手工按钮驱动的字符串步骤表。
- **代码**：`server/app/services/llm.py:106-118`（payload 无 tools）；`server/app/services/llm_stream.py:124-125`（拒绝 `tool_calls`）；`study_chat.py:467`（max_calls=2）；`study_chat.py:229-264`（`_gather_sources` 预检索）。
- **为什么是问题**：**这是上海 AI Agent 社招最致命的短板。** 面试官必然深挖 ReAct/Function Calling Loop、工具 Schema 设计、工具调用失败重试/降级、循环终止与防死循环、权限与预算护栏。纯确定性 Workflow 可以做主干，但完全没有自主 Tool-Calling Loop 会让你在 Agent 岗竞争力直接掉一档。
- **目标方案**：依赖 B7 开放流式工具协议后，在资料库深度问答与项目代码问答中构建**受限 ReAct 工具环**：白名单只读工具（`search_bank` / `read_note` / `search_code` / `read_file_lines` / `get_user_memory`），最多 5 轮、token+时间+调用数三重预算封顶、工具参数 Pydantic 校验、异常转为可观察 error observation 让模型自愈、每步落结构化 Trace。同时保留确定性 Workflow 用于强审计评分链路，形成“Workflow vs Bounded Agent”完整架构叙事。

### B2. 意图路由纯正则枚举，长尾说法漏路由 `P1` `OPEN`
- **现状**：前端 intentMatchers/roleMatchers 硬编码正则，后端 teaching_action 再细分；无 LLM 分类、无 embedding 相似度路由。
- **代码**：`web/src/interview/chat/model.ts:130-189`；`server/app/services/study_chat.py:142-158`；`study_routing.py:88-155`。
- **为什么是问题**：正则覆盖不了自然语言长尾（"帮我看看这题我答得咋样"可能漏判）；路由错误静默降级到 coach，用户无感知。规则路由的可复现性是优点，但需要兜底层。
- **目标方案**：三级路由：正则（快、确定）→ 轻量分类器/小模型（长尾）→ 置信度低时显式问用户；路由决策与置信度落 trace；用现有 auto_quality 评测集扩长尾样本回归。

### B3. 工作流状态机只支持线性步骤，无分支/回退/动态插入 `P2` `OPEN`
- **现状**：6 技能固定步骤元组，支持 start/pause/resume/skip/advance/change，乐观锁防并发；不能回退到已完成步骤、不能按表现动态分支（如答得差插入补强步）。
- **代码**：`server/app/services/interview_workflow.py:9-68`；状态存 `study_sessions.context` JSONB（`api/v1/study_chat.py:189-201`）。
- **为什么是问题**：真实模拟面试需要根据回答质量动态追问/降难度；固定脚本的"面试官"体验机械。工业级对话 agent 的状态机应支持条件转移。
- **目标方案**：步骤图加条件边（由确定性评分信号驱动，不让 LLM 直接改状态）；支持回退与插入步骤；保持 revision 乐观锁与"LLM 只读状态"边界。

### B4. 模拟面试多轮靠"对话粘性"正则维持，无显式会话模式 `P2` `OPEN`
- **现状**：上一条 assistant 是 interview 且本轮无"结束/暂停/解释"等词则继续 interview——模式维持靠词面判断。
- **代码**：`server/app/services/study_chat.py:216-222`。
- **为什么是问题**：用户说"这题为什么"想听解释，可能被粘性规则困在面试模式；模式切换意图与 B2 同源但更隐蔽。
- **目标方案**：会话显式 `mode` 字段 + 切换意图识别；与 B3 的条件状态机合并设计。

### B5. evolution 策略闭环被硬编码熔断，无在线实验能力 `P2` `ACCEPTED`
- **现状**：候选生成（确定性规则）+ 离线投影评估已实现；激活被硬编码关闭（approve 403、get_active_candidate 恒 None），无 A/B、无 canary、无自动停损。
- **代码**：`server/app/services/evolution.py:337-363`；`server/app/config.py:98-100`（`evolution_activation_enabled` 恒 False）；`api/v1/evolution.py:138-144`。
- **为什么是问题**：作为"自进化学习系统"叙事，生效环节缺失使闭环断在最后一步。但熔断本身是正确的 fail-closed 设计（评估是投影不是实验，样本不足）。
- **目标方案**：保持默认关闭；补"小流量 workspace 灰度 + 指标守护（得分率/复测率不降）+ 自动回滚"后才允许激活；或明确 ACCEPTED 并准备好"为什么不敢开"的话术（评估证据不足）。

### B6. 追问建议/学习计划纯规则，无个性化排序 `P2` `ACCEPTED`
- **现状**：followup 是 7 类 Gap 规则表（文件头明确"LLM 不参与——必须稳定可复现"）；planner 从 rubric 缺失项确定性生成任务。
- **代码**：`server/app/services/followup.py:1-55`；`planner.py:17-113`。
- **为什么是问题**：个性化是学习产品的核心卖点，当前"千人一面"。规则可复现是优点，但难度无校准（D3）使"按失分学习"的精度受限。
- **目标方案**：难度标定（D3）先行；规则产出候选 + 模型排序的混合个性化；保留确定性兜底。

### B7. LLM 网关“伪多厂商 + 拒绝流式 tool_calls + 无连接池 + 流式回调同步阻塞 DB” `P0` `OPEN`
- **现状**：① `llm_stream.py:124-125` 遇到 `tool_calls` 直接 `fail('unsupported_delta')`，阻塞任何流式 Agent 工具调用；② `llm.py:113-114` 把智谱/DeepSeek 专属 `thinking` 字段硬塞进通用 OpenAI payload，`llm_stream.py:147` 只认 `reasoning_content` 且 `80-82, 112-120` 对 finish 后 usage 帧极挑剔，遇其他网关易误杀；③ `llm.py:195,206` 每次请求新建 `httpx.AsyncClient` 无连接池复用，`llm.py:126` 残留死变量 `retries = 0`，`llm.py:352` 全局单例 `adapter` 的 `last_error/last_usage` 实例属性存在并发覆盖隐患；④ `study_chat.py:433` 在 SSE `on_content` 回调内直接 `await save("generating", ...)` 同步写 PG，无背压解耦，DB 抖动会直接卡死上游流读取；`llm.py:294-308` 在 `finally` 中 `await _record_metric` 遇 `CancelledError` 有取消态写库风险。
- **代码**：`server/app/services/llm.py:69-71,106-126,195-208,294-308,352`；`server/app/services/llm_stream.py:80-82,112-125,147,209-212`；`server/app/services/study_chat.py:432-435`。
- **为什么是问题**：LLM 网关是 Agent 系统的基础设施底座。不解开 `tool_calls` 流式组装，B1 做不成流式体验；不解耦流式回调与 DB 写、不复用连接池，并发和高负载下 TTFT 与稳定性立刻崩；厂商私有字段直塞会导致“多模型路由/降级”一换模型就 400。
- **目标方案**：① 扩展 `llm_stream.py` 支持增量 `tool_calls` 分片组装（保持字节上限防打爆）+ 兼容 `reasoning`/`reasoning_content` 及尾帧 usage；② 抽 Provider Dialect 适配层（OpenAI/DeepSeek/Anthropic/Gemini），厂商扩展字段按 dialect 注入；③ 进程级复用 `httpx.AsyncClient` 连接池，消除全局可变诊断状态改为返回结构化 `CallResult`；④ `study_chat` 流式落库改异步缓冲/队列解耦（不阻塞上游 SSE 读取），`finally` 指标写入用 `asyncio.shield` 或独立后台落盘。

---

## C. 上下文工程与记忆

### C1. token 计数用 UTF-8 字节估算，"保守上界"对英文不成立 `P1` `OPEN`
- **现状**：无 tiktoken/任何 tokenizer；上下文预算按 UTF-8 字节算。中文高估（3 字节≈1–2 token）浪费窗口，英文/符号串可能低估（BPE 对罕见符号串可膨胀），`conservative upper bound` 注释不严格成立。
- **代码**：`server/app/services/study_chat.py:359-361`；`project_autopilot_engine.py:511-513`（自报 `conservative_utf8_bytes_not_actual_tokens`）；`public_model.py:151-160`。
- **为什么是问题**：低估→provider 400 context_length_exceeded；高估→白白丢弃可用上下文。工业级必须按目标模型 tokenizer 精确计数（至少对主流模型）。
- **目标方案**：tiktoken（OpenAI 系）+ 各 provider tokenizer 适配表；未知模型保留字节估算但加安全系数并标注估算口径；对 400 溢出加"自动重拟合降级"路径与测试（D6）。

### C2. 长对话机械截断，关键约束静默丢失，无 LLM 摘要 `P1` `OPEN`
- **现状**：最近 12 轮全文，更早轮次 `text[:180]` 硬截断成摘录；再超限每轮截 1400 字。无滚动摘要。产品自己的题库里就写着这种反模式（"预算 5 万"被压成"有预算"）。
- **代码**：`server/app/services/study_chat.py:119-139,285-292`；反面案例 `answer_gen.py:96`。
- **为什么是问题**：长会话中用户给过的约束（目标岗位、预算、已排除方案）被截掉后模型会重复犯错，直接损害教学可信度。
- **目标方案**：LLM 滚动摘要（保数字/边界条件/决策的 schema 化摘要）+ 关键事实钉住（pinned facts 不截断）；摘要质量用"约束保留率"评测；摘要调用计入预算与 trace。

### C3. 无 prompt caching，稳定前缀每轮重复计费 `P1` `OPEN`
- **现状**：system 人设/合同指令每轮全量发送；无 provider prompt cache 利用，也无本地语义缓存。评分侧 outbox 是防崩溃重复，不是成本缓存。
- **代码**：`server/app/services/llm.py:106-118`（payload 无 cache 控制）；`assessment_worker.py:79-98`（outbox 仅崩溃恢复）。
- **为什么是问题**：prefill 是 TTFT 与成本大头；稳定前缀不缓存等于每轮白付。工业级 LLM 网关标配 prompt cache + 结果缓存。
- **目标方案**：prompt 排布缓存友好（稳定内容前置）；接 provider cache 参数；评分按 `(input_hash, rubric_version, model_fingerprint)` 做结果缓存；上报缓存命中率与节省金额。

### C4. 记忆无遗忘/衰减/容量治理 `P2` `OPEN`
- **现状**：MemoryItem 有 SUPERSEDED/DISMISSED 状态与级联失效（亮点），但无时间衰减、无容量上限、无定期整理；freshness 只有 CURRENT/NEEDS_REVIEW 两态。
- **代码**：`server/app/models/memory.py:16-37`；`learning_memory.py:43-88`（级联失效）。
- **为什么是问题**：长期使用后记忆膨胀，召回前 N 条被陈旧记忆占据；工业级记忆系统需要 consolidation（合并/提炼）与遗忘策略。
- **目标方案**：定期离线整理任务（同主题记忆合并、过期降级）；召回加时间衰减权重；与 C2 的摘要机制共用基建。

### C5. 记忆治理与聊天主链路彻底割裂（且练习题干裸拼 `str(notes)`） `P0` `OPEN`
- **现状**：① 写了一整套 `learning_memory`（学习观察/偏好）和 `project_memory`（项目确认记忆）的审核状态机与级联失效，但高频交互入口 `study_chat.py` **完全不读 `learning_memory`**，也绕过了 `project_sources()` 不读 `project_memory`；② 唯一消费 `learning_memory` 的 `learning_feedback.py:228` 竟然直接用 `str(notes)` 把 Python 列表字面量 `['已确认的...']` 拼进给用户和模型看的练习题干。
- **代码**：`server/app/services/study_chat.py`（零引用 `learning_memory`/`project_memory`）；`server/app/api/v1/study_chat.py:29`（注释证实 `project_sources` 为死导入）；`server/app/services/study_project_evidence.py:527-573`；`server/app/services/learning_feedback.py:227-228`（`str(notes)` 裸拼）。
- **为什么是问题**：面试讲“我做了带审核状态机和级联失效的 Memory 系统”，面试官追问“用户确认后的记忆怎么影响下一轮对话教学？”，一翻代码发现主聊天根本没接、练习还拼成 `['...']`，瞬间露馅。
- **目标方案**：① 立刻修复 `learning_feedback.py:228` 改为结构化格式化拼接；② 把已确认的 `learning_memory` / `project_memory` / `bank_review_memory` 统一经独立 `memory_context`（带来源标注与 token 预算）接入 `study_chat`；③ 配合 B1 提供 `get_user_memory` 按需检索工具；补“确认记忆→聊天生效→撤回记忆→聊天不再引用”端到端回归测试。

---

## D. LLM 输出质量与评估体系

### D1. 评分 judge 无 meta-evaluation：从未与人工标注对齐 `P0` `OPEN`
- **现状**：judge 测试全是结构校验（拒伪引用/坏 JSON/越界分），没有任何"人工分 vs LLM 分"一致率测试；rubric 候选自认 `human_reviewed:false, is_gold_standard:false`；judge-prompt v3→v4 升级无分数回归证据。
- **代码**：`server/tests/test_core_scoring.py:311`、`test_assessment_rubric.py:137`（只测结构）；`docs/assessment/rubric-candidates.json:3-5`；`server/tests/fixtures/build_assessment_candidates.py:186`（coverage_gaps 自认未校准）；`llm_judge.py:145-146`（版本常量切换）。
- **为什么是问题**：产品核心主张是"用真实作答找出差距"，结构护栏只保证"格式对"不保证"分数对"。这是全项目最大的正确性洞，也是面试最锋利的追问点。
- **目标方案**：建 golden set（30–50 题 × 人工逐项判档），算逐 criterion 一致率 + Cohen's kappa + 总分 MAE；每次 judge prompt/模型变更跑回归；把 `UNCALIBRATED` 升级为"校准过、一致率 X%"并写进产品文案。

### D2. 聊天讲解正文无事实性校验 `P1` `OPEN`
- **现状**：结构化产物有强校验（图表数值必须与来源 JSON 逐值相等、project 图必须引真实代码证据），但自由文本讲解直接落库，只有事前 prompt 约束。
- **代码**：`server/app/services/study_blocks.py:74-95`（产物校验）；`study_chat.py:507-543`（正文无校验）。
- **为什么是问题**：教学场景里一句错误的技术断言比没有答案更糟；"引用分级"合同是软约束，模型可违反。
- **目标方案**：关键技术断言抽样二次 judge（cheap model 核查与来源一致性）；或 citation-grounding 轻量校验（断言→来源句蕴含）；先对 project 类回答启用（有代码证据可核对），统计违规率再决定覆盖面。

### D3. 生成题目难度写死、无区分度标定 `P1` `OPEN`
- **现状**：AI 生成题审核通过后 `difficulty=3` 硬编码；无试答标定、无区分度统计；rubric 多为 AI_DRAFT/LEGACY_UNCALIBRATED。
- **代码**：`server/app/services/question_review.py:92`；`assessment_rubric.py:75`（校准三档）。
- **为什么是问题**：难度失真的题池使"按失分学习/组卷"的梯度失效；工业级题库需要 item calibration。
- **目标方案**：用真实作答数据反推难度（得分率分桶）与区分度（高低分组差）；新题先入"试答池"积累 N 次独立作答再定级；与 D1 的 golden set 共用标注流程。

### D4. prompt 版本管理分裂：聊天/出题/播客硬编码，无变更回归 `P1` `OPEN`
- **现状**：judge 与项目管线有 prompt_version + sha256 落库（好）；但聊天教学 prompt 在 `locales/*.generation.json` 无版本字段，出题/播客 prompt 硬编码 Python；任何 prompt 改动没有"同批输入新旧对比"的回归机制。
- **代码**：`server/app/generation_i18n.py:10-13`（lru_cache 读文件无版本）；`llm_judge.py:20`、`api/v1/question_generation.py:35-87`、`podcast_content.py:47`（硬编码）；对比 `project_prompts/__init__.py:5`（有 bundle 版本）。
- **为什么是问题**：prompt 是 LLM 应用的"代码"，无版本无回归等于裸奔上线；面试讲"prompt 工程化"时这是明显断点。
- **目标方案**：所有 prompt 统一 bundle 化 + 内容 hash 版本落库（复用 judge 的模式）；建 prompt 回归夹具（固定输入→输出质量断言/人工抽检清单）；变更走与代码相同的 PR 审核。

### D5. 换模型只有"拒绝"没有"验证"：影子复核文案承诺了不存在的功能 `P1` `OPEN`
- **现状**：model_fingerprint/judge_version 变化→CONFIG_CHANGED 拒绝混跑，错误文案让用户"创建影子复核"，但产品内无此功能；唯一影子实现是一次性验收脚本。无 A/B/canary。
- **代码**：`server/app/services/assessment_jobs.py:272-273`、`assessment_worker.py:134-136`；`locales/zh-CN.json:272`；`server/acceptance/coach_shadow_judge.py:1,18`（READ_ONLY_SHADOW 仅验收）。
- **为什么是问题**：模型升级决策无质量证据支撑；文案与能力不符本身就是产品缺陷。工业级模型管理=指纹一致性+影子流量+质量门禁。
- **目标方案**：把验收脚本产品化为"影子复核任务"（新模型对历史 N 题只读复评，产出与 D1 golden set 的对齐报告）；通过门禁才允许切换默认模型；修正文案。

### D6. 无 provider 语义错误演练：上下文溢出 400 直接终态 `P2` `OPEN`
- **现状**：聊天路径 4xx（非 408/429）直接失败不重试；无 context_length_exceeded 自动降级重拟合；畸形流测试很全（16 种）但 provider 语义错误覆盖少。
- **代码**：`server/app/services/study_chat.py:548-551`；`tests/test_llm_stream_transport.py`。
- **为什么是问题**：C1 的估算偏差最终会在这里变成用户可见失败；工业级网关对可恢复的 400（溢出/参数）应有自动修复路径。
- **目标方案**：识别溢出错误码→按 C1 精确计数重拟合→降档重试一次；加 MockTransport 演练用例。

### D7. 代码题无沙箱执行，纯静态评审 `P2` `ACCEPTED`
- **现状**：coding_debugging 题只接收文本/diff 做静态评审 + 行号引用校验，limits 明确"无自动执行/CI 验证"，并正则拦截"测试已通过"类虚假声明。
- **代码**：`server/app/services/project_practice.py:77,89,236,256-259`。
- **为什么是问题**：代码能力的最终证据是"能跑对"，静态评审无法验证正确性；但本地跑不可信代码的安全成本高，当前诚实标注是正确取舍。
- **目标方案**：若要补：Docker 一次性容器 + 网络禁用 + CPU/内存/时限 + 只读挂载 + 输出限额；执行结果作为评分证据进入同一 rubric 链路。或保持 ACCEPTED 并讲清安全边界。

### D8. 无多模态输入：图片仅 OCR、语音输入未接入 `P2` `OPEN`
- **现状**：图片走 RapidOCR 提文字，LLM payload 恒纯文本（无 image_url/vision 块）；exam 预留 transcript 字段但无 ASR 链路；TTS 播客有文本侧规则校验但无听感质量评估。
- **代码**：`server/app/services/text_extract.py:22,142-157`；`llm.py:108`；`models/exam.py:94`；`podcast_content.py:213-252`。
- **为什么是问题**：面试白板题/架构图截图是高频场景，OCR 丢结构信息；语音模拟面试是差异化体验。工业级 agent 平台通常至少支持 vision 输入。
- **目标方案**：vision 模型接入（messages 支持 image_url 块 + 图片大小/类型闸）；ASR 用本地 whisper 系；TTS 加抽听评审清单与术语发音回归。

### D9. 评分引用校验过于脆弱且存在单字绕过漏洞 `P0` `OPEN`
- **现状**：`assessment_rubric.py:203-207` 的 `_quotes()` 直接做 `quote not in text` 严格精确子串匹配——连空白折叠/全半角标点归一化都没有（对比 `study_notes.py:158-160` 至少做了空白折叠），多一个换行或标点差异即整题报 `INVALID_QUOTE`；同时**无最小长度校验**，模型只要引用单个字（如 `"的"`/`"是"`）即可通过校验，反幻觉护栏被单字架空。
- **代码**：`server/app/services/assessment_rubric.py:203-207`；对比 `server/app/services/study_notes.py:158-160`。
- **为什么是问题**：评分引用校验是你全项目最核心的面试卖点之一。面试官只要追问“LLM 引用漏了个标点怎么办？模型引用一个‘的’字会不会绕过？”，当前代码立刻穿帮。
- **目标方案**：① 引入规范化匹配层（空白折叠 + 全半角/常见标点归一 + 轻度编辑距离/span 对齐回溯原文坐标）；② 增加最小有效字符长度与停用词拦截（防单字/纯标点绕过）；③ 把真实多模型 `INVALID_QUOTE` 失败率与绕过对抗样本纳入 D1 评估集回归。

---

## E. 成本与性能治理

### E1. 聊天生成走 BackgroundTasks 绕过队列：跨会话无并发上限 `P0` `DONE`
- **现状**："全局单并发"只覆盖后台评分/项目 worker；study_chat 用 FastAPI BackgroundTasks 直接生成，单会话单生成有 409 保护，但一个用户开 100 个会话可并发 100 个生成，每个最长 30 分钟。
- **代码**：`server/app/api/v1/study_chat.py:342`（tasks.add_task）；对比 `project_jobs.py:158-173`（advisory lock 单并发）；`config.py:14`（30 分钟超时）。
- **为什么是问题**：失控客户端/误操作可产生无上限并发账单与 provider 限流雪崩；"我做了单并发成本控制"的面试叙事在这里被戳穿。BackgroundTasks 还意味着 API 进程重启即丢在飞生成（靠租约恢复兜底，钱白花）。
- **目标方案**：聊天生成并入持久队列（复用租约/fencing/outbox 基建），或至少加 per-workspace 并发闸 + 全局 Semaphore；把"为什么聊天没走队列"的历史原因写进 ADR 并给出迁移路径。

### E2. 私有路径成本只有 ¥2/M 估算，无货币预算与告警 `P0` `DONE`
- **现状**：输入/输出不分价、不分模型，固定 `total_tokens × 2e-6`；无每日/每会话货币上限；项目自动分析默认预算为空（不限调用次数/读取字节）。真预算账本只在公开 seed 网关有。
- **代码**：`server/app/services/llm.py:271,326`；`project_autopilot_config.py:10-11`（DEFAULT_BUDGET={}）；对比 `public_model.py:119-124,172-188`（月/日预算 + advisory lock 账本，全仓唯一真计价）。
- **为什么是问题**：按 token 花钱的系统没有成本闸=生产事故预备役；估算价使成本报表不可信。
- **目标方案**：把 public_model 的预留-结算账本模式推广到私有路径；按模型配置输入/输出单价；日预算超限 429+提示；项目分析默认预算给非空值；成本异常（环比翻倍）进告警（G3）。

### E3. 无 API 层限流与配额 `P1` `DONE`
- **现状**：限流只存在于 telemetry/注册登录/公开 coach 等局部；主 API（聊天/出题/导入/分析触发）无每用户 RPS/并发/日配额。
- **代码**：`server/app/api/v1/telemetry.py:93-113`、`api/public_auth.py:205,239-240`（局部有）；`api/v1/study_chat.py`、`question_generation.py`（无）。
- **为什么是问题**：与 E1 叠加放大成本风险；注册版公网暴露时这是基本安全要求。
- **目标方案**：中间件级令牌桶（按 workspace/token），区分读/写/LLM 触发三类配额；超限返回 429+Retry-After；配额配置进 settings。

### E4. TTFT 优化缺 prefill 侧手段：无缓存、检索串行、连接冷启动 `P1` `OPEN`
- **现状**：已有双档路由 + 字节预算裁剪（好）；但 sources 串行收集后才开始生成（项目证据独立预算 120s）、无 prompt cache（C3）、httpx 每调用新建 client 无共享连接池。
- **代码**：`server/app/services/study_chat.py:229-264`（_gather_sources 串行）；`llm.py:196`（AsyncClient 用法）；`llm_stream.py:144`（TTFT 埋点已有）。
- **为什么是问题**：TTFT 分解里"检索+prefill"两段都没优化；面试讲 TTFT 优化时只有路由一张牌。
- **目标方案**：检索与首段生成并行/打字时预取；共享 httpx.AsyncClient 连接池；C3 的 prompt cache；artifact 校验不阻塞首字。用现有 bench_study_chat.py 配对 A/B 量化每项收益。

### E5. 指标聚合取最近 5000 条内存算分位数，量大失真 `P2` `OPEN`
- **现状**：ops summary 拉最近 5000 条 AgentMetric 在 Python 里算 p50/p95/p99；无预聚合、无时间分桶。
- **代码**：`server/app/api/v1/ops.py:129-160`。
- **为什么是问题**：数据量增长后既慢又截断失真（只反映最近窗口）；工业级用时序预聚合或 PG 百分位聚合。
- **目标方案**：按小时/天预聚合表（percentile_cont）；或接 Prometheus histogram（G2）。

---

## F. 可靠性与任务基建

### F1. outbox 是本地文件，多机/容器化即失效 `P1` `OPEN`
- **现状**：评分结果先 fsync+os.replace 落盘 `personal/assessment-outbox/<db-hash>/…json` 再提交 DB；崩溃接管靠同机文件重放。容器重建/多 worker 跨机时文件不可达，防重复付费语义丢失。
- **代码**：`server/app/services/assessment_worker.py:31-45,79-98,173-175`。
- **为什么是问题**：这是"单机正确"的设计，工业级要求存储与计算分离；docker-compose 部署时 outbox 目录若不挂卷，重建即丢。
- **目标方案**：事务性 outbox 表（结果与状态同库同事务，接管者从 DB 重放）；保留文件版作单机加速层可选；补"接管者跨进程重放"测试。

### F2. 单 worker 单并发无水平扩展实现，两套队列语义并存 `P2` `OPEN`
- **现状**：advisory lock 刻意全局单并发（成本可控，正确）；但无按 workspace 分片、无多 worker 竞争消费实测；播客队列是另一套独立租约实现。
- **代码**：`server/app/services/assessment_jobs.py:151-178`；`project_jobs.py:158-173`；`podcast_jobs.py:46-103`（第二套队列）。
- **为什么是问题**：面试必问"怎么扩展"，当前只能口头回答；两套队列实现增加维护面与语义漂移风险。
- **目标方案**：统一队列抽象（认领/租约/fencing/outbox 一套代码）；隔离库 PoC：N worker 分片认领 + 竞争消费正确性测试（不重复、不丢、fencing 生效）；写扩展性 ADR。

### F3. 无 CI 测试门禁，769 个测试全靠本地自觉 `P0` `OPEN`
- **现状**：`.github/workflows/` 只有 pr-content-review.yml（fork PR 内容安全审查）；后端 ~769 测试函数、前端 ~243 测试无 CI 执行；测试隔离纪律很好（_test 库+清凭据）但没有强制触发点。
- **代码**：`.github/workflows/pr-content-review.yml`；`server/tests/conftest.py:14-25`；`server/run_isolated_tests.py`。
- **为什么是问题**："测试怎么保证执行"是工程面试必问题；无门禁意味着 dev 分支可能带着红测试合入。
- **目标方案**：GitHub Actions：PG service container + run_isolated_tests.py + 前端 node --test + ruff/tsc/eslint/knip；PR 必须绿；nightly 跑 opt-in 崩溃探针。

### F4. 优雅停机是硬杀：API 不等在飞生成，worker 无 SIGTERM 处理 `P1` `OPEN`
- **现状**：部署脚本直接 Process.Kill()；API lifespan 只处理播客 worker；study_chat 在飞生成靠租约到期收敛 interrupted（钱白花）；project_worker 无信号处理器；compose 无 stop_grace_period。
- **代码**：`deploy/personal-processes.ps1:63-72`；`server/app/main.py:53-68`；`project_worker.py:310-322`；`docker-compose.yml:62-70`。
- **为什么是问题**：每次部署/重启都制造"中断+重复付费窗口"；工业级要求 drain（停止接单→等在飞完成/主动释放租约→退出）。
- **目标方案**：worker 捕获 SIGTERM/CancelledError 主动释放租约（播客已有范式 `podcast_jobs.py:263-271`，推广）；API 加 drain 期；uvicorn --timeout-graceful-shutdown；compose stop_grace_period；E1 并入队列后此问题大幅缓解。

### F5. 迁移无版本记账、无 down 路径，create_all 与 SQL 迁移双轨 `P2` `OPEN`
- **现状**：自研幂等 SQL 执行器（IF NOT EXISTS 约定），无版本表；新表靠 lifespan create_all，新列靠 SQL——双轨制无单一事实源；rollback 目录存在但无自动验证。
- **代码**：`server/migrate.py:1-9`；`server/app/main.py:55-57`；`server/migrations/`（22 个 SQL）。
- **为什么是问题**：单机可接受，但 schema 漂移无法检测（create_all 不改已有列）；团队协作/多环境部署会乱。
- **目标方案**：alembic（autogenerate + 版本表）或至少给自研执行器加版本记账与 schema diff 校验；CI 里跑"空库全量迁移+幂等重放"测试。

### F6. 备份无加密/异地/保留策略，灾难恢复到生产从未演练 `P2` `DONE`
- **现状**：backup.ps1 工程质量好（原子落盘+pg_restore 校验+SHA256），但 dump 含明文密钥（G5）、无加密、无异地、无旧备份清理；restore.ps1 只允许恢复到 *_test 库——**从未验证过真实灾难恢复**；runbook 与脚本行为矛盾（文档说可覆盖生产）。
- **代码**：`deploy/backup.ps1:13-36`；`deploy/restore.ps1:12-14,31`；`deploy/ECS_DEPLOY_RUNBOOK.md:513-517`。
- **为什么是问题**："有备份"≠"能恢复"；RTO/RPO 未知；备份本身是密钥泄露面。但属通用 DBA/运维债，个人求职冲刺期勿抢占 Agent 核心工时。
- **目标方案**：备份加密（age/GPG）+ 异地副本 + 保留策略（如 7 日/4 周/3 月）；写"恢复到生产"的受控流程（显式确认+先备份现库）；定期恢复演练并记录 RTO；修正 runbook 矛盾。

### F7. 无全局异常处理器，未处理异常裸 500 且不可关联 `P1` `OPEN`
- **现状**：main/public_main 均无 exception_handler；未处理异常走 Starlette 默认裸 500，traceback 只进 stderr，无 request_id 可关联；422 存在两种形态（auth 路由组统一 detail 字符串，其余 FastAPI 默认数组）。
- **代码**：`server/app/main.py`、`public_main.py`（无 handler）；`api/public_auth.py:57-75`（仅 auth 组）。
- **为什么是问题**：用户报障时无法定位；错误契约不一致增加前端处理成本。工业级 API 统一错误信封（code/message/request_id）。
- **目标方案**：全局 handler：未处理异常→统一 500 信封 + 结构化日志（带 request_id）+ 指标计数；422 统一形态；业务错误码表文档化。

### F8. 观测/配置路径静默吞错 + Worker 心跳协程脆弱 `P1` `OPEN`
- **现状**：① ops 聚合、指标落库、配置读取等路径存在 `except Exception: pass` 静默吞错；② `project_worker.py:220-227` 的 `keep_alive()` 心跳协程完全没有 `try/except`，一次 DB 瞬断会让心跳协程静默崩溃，导致后续租约过期任务被误判 `WORKER_LOST`；③ `assessment_worker.py:166-169` 一旦心跳捕获任意 DB 瞬断直接 `task.cancel()` 杀掉正在跑的付费 LLM 调用，缺少瞬态重连宽限；④ `assessment_worker.py` 本地 outbox 文件没有任何清理机制，只增不减；⑤ `study_notes.py:220-225` 写死 120s 租约不续期、过期即 `FAILED` 不重试。
- **代码**：`server/app/api/v1/ops.py:109-110,134-135,226-227,249-250`；`services/llm.py:51-52,290-291,307-308`；`server/app/project_worker.py:220-227`；`server/app/services/assessment_worker.py:166-169`；`server/app/services/study_notes.py:220-225`。
- **为什么是问题**：分布式队列是你最硬的后台工程卖点，但心跳在 DB 瞬断下直接挂掉或错杀付费任务是真实线上炸弹；观测吞错使排障自盲。
- **目标方案**：心跳协程加有限次瞬态 DB 异常退避重试（仅在确认失权或连续失败耗尽租约宽限时取消任务）+ 告警日志；`except Exception: pass` 全部改为 `logger.warning` + 失败计数器；outbox 补完成清理与 TTL 扫盘（F1 迁移 DB 后自然消除）。

---

## G. 可观测性与运维

### G1. 无结构化日志、无 request_id 贯穿链路、无轮转 `P0` `OPEN`
- **现状**：API 进程完全没有日志配置（只有 uvicorn access log）；worker 仅 basicConfig 纯文本；无 JSON 日志、无服务端 request_id/correlation_id 贯穿 API→worker→LLM（代码里的 request_id 全是客户端幂等键）；AgentMetric 无 trace_id 列且 workspace_id 从不填；日志靠部署脚本重定向文件，无限增长。
- **代码**：`server/app/main.py:1-130`（无 logging 配置）；`project_worker.py:318`（唯一 basicConfig）；`models/metric.py:21-42`、`services/llm.py:317-331`（指标不填 workspace_id）；`deploy/start-personal.ps1:72`。
- **为什么是问题**：用户报"生成失败"时，没有任何手段把一次 API 请求关联到 worker 步骤和具体 LLM 调用——只能盲猜。这是工业级可观测性的第一块地基，缺失使其他一切观测投资贬值。
- **目标方案**：structlog/JSON 日志 + 中间件生成 request_id 贯穿（响应头回传、落 AgentMetric/Trace、worker 任务携带）；日志轮转；级别可配。

### G2. 无 Prometheus/OTel 导出，指标只存 PG 靠人看看板 `P1` `OPEN`
- **现状**：AgentMetric/Trace/ops summary 自建体系质量不错（LLM 延迟分位/TTFT/错误分类/token 成本），但无 /metrics 端点、无 OTel、无 API 层 HTTP 延迟/错误率、无队列深度/积压指标、无连接池饱和度。
- **代码**：`server/app/api/v1/ops.py:295-344`；`models/metric.py`、`models/trace.py`；全仓无 prometheus_client/opentelemetry 依赖。
- **为什么是问题**：工业级标准是 metrics/traces/logs 三支柱可被外部系统抓取与关联；队列积压不可见意味着"用户等 10 分钟"无人知晓。
- **目标方案**：prometheus_client 暴露 /metrics（HTTP 延迟直方图、队列深度 gauge、LLM 调用计数器按 model/kind/error 分维度）；或 OTel SDK 导出；保留 DB 明细做审计回放。

### G3. 零告警：成本暴涨/worker 死亡/备份失败全靠人发现 `P1` `OPEN`
- **现状**：无任何告警通道（邮件/webhook/IM）。LLM 网关挂掉、成本环比翻倍、worker 心跳丢失、备份失败——都要等人主动打开看板。
- **代码**：全仓 grep alert 零命中（业务代码）；`api/v1/ops.py` 只展示不告警；WorkerHeartbeat 存在（`project_worker.py:184-189`）但无人消费其超时。
- **为什么是问题**：按 token 花钱的系统，"错误率没动但成本翻倍"这类事故完全无人值守；工业级 SLO 必须配告警。但在单人求职阶段应排在 Agent 核心能力与评测之后。
- **目标方案**：最小可用：定时巡检任务（worker 心跳年龄/队列积压/日成本/错误率/备份新鲜度）超阈值走已有 SMTP 通道发告警；后续接 Alertmanager。

### G4. /healthz 是静态存活探针，DB 挂了仍显示 healthy `P2` `OPEN`
- **现状**：/healthz 恒返回 ok，不查 DB/worker/队列；compose healthcheck 打的就是它。真深度检查 /ops/health 存在但要 owner token，不能给编排系统用，且不含队列积压。
- **代码**：`server/app/main.py:105-107`；`docker-compose.yml:55-60`；`api/v1/ops.py:347-381`。
- **为什么是问题**：健康检查撒谎比没有更糟——编排层在 DB 故障时认为一切正常，不重启不摘流。
- **目标方案**：拆 liveness（进程活着）与 readiness（DB SELECT 1 + 关键依赖）；compose/负载均衡打 readiness；深度诊断保留在鉴权后的 /ops/health。

### G5. LLM key / GitHub token 明文存 DB，随备份扩散 `P1` `DONE`
- **现状**：AppSetting.value JSONB 明文存 api_key/github token/仓库凭据；"API 永不回显只给尾 4 位"只是应用层遮罩，拖库/备份文件即泄露；backup dump 恰好包含这些表且无加密（F6）。仓库里的 AES-GCM vault 只用于公开门户 payload，与后端无关。
- **代码**：`server/app/models/app_setting.py:14-17`；`api/v1/settings.py:561-596,648-651`；`services/repository_credentials.py:10-14`；全 app 无 encrypt/Fernet。
- **为什么是问题**：密钥是最高价值资产；明文落库+明文备份=双重泄露面。生产化必须加密存储，但本地个人版阶段优先级低于 Agent 核心能力。
- **目标方案**：Fernet/AES-GCM 信封加密（主密钥来自 env/OS keyring，不进 DB）；或接系统凭据管理器；迁移现有明文行；备份加密（F6）兜底。

### G6. 弱默认凭据进 compose 与前端 bundle，个人版无启动断言 `P2` `DONE`
- **现状**：config 默认 api_token="cp-dev-token"、debug=True、DATABASE_URL 示例弱口令；compose 内置弱默认；个人版不像公开版那样启动拒绝弱口令；前端 localTransport 内置 dev token。
- **代码**：`server/app/config.py:23,30`；`docker-compose.yml:2-3`；`web/src/api/localTransport.ts:6,30`；对比 `public_main.py:32-33`（有断言）。
- **为什么是问题**：个人版一旦端口暴露到局域网/公网（ECS 部署场景真实存在），弱 token 即被扫到。
- **目标方案**：个人版启动断言拒绝默认 token/弱口令（复用公开版模式）；首次启动生成随机 token；前端 token 不硬编码进 bundle。

### G7. 数据无限增长：无保留策略、无归档、导出不完整 `P2` `OPEN`
- **现状**：agent_metrics/telemetry_events/traces/events 无 TTL/归档作业；备份目录不清理；工作空间删除是 CASCADE 硬删无冷静期；/settings/export 不含 study_sessions/study_messages/projects/podcasts/agent_metrics，events 只导 1000 条。
- **代码**：全仓无 retention/purge 作业；`api/v1/settings.py:320-436`。
- **为什么是问题**：长期运行 DB 膨胀拖慢查询（E5 叠加）；"数据可携带"承诺不完整；硬删无后悔药。
- **目标方案**：观测类表按月分区+保留策略（明细 90 天、聚合永久）；导出补全所有用户数据表；删除加冷静期/软删。

---

## H. 安全与输入面

### H1. 个人版永久静态 token：无过期/轮换/撤销 `P2` `DONE`
- **现状**：Bearer token SHA256 恒时比较（做得对），但 token 永久有效、无轮换/撤销机制、无失败限速；泄露即永久访问。
- **代码**：`server/app/api/deps.py:17-37`；`config.py:34,83-90`。
- **为什么是问题**：工业级凭据生命周期管理是基本要求；与 G6 叠加风险放大。
- **目标方案**：token 表（hash+过期时间+最后使用），支持轮换与撤销；失败尝试限速；管理端点操作审计。

### H2. LLM endpoint 可配任意 URL：SSRF 探测面 `P1` `DONE`
- **现状**：settings 可配任意 base_url，/llm/test 是现成的探测 oracle（可打内网地址看响应差异）；个人版无 URL 白名单/内网黑名单（注册版模型 URL 有 https/loopback 白名单，做得对）。
- **代码**：`server/app/api/v1/settings.py:493,549-599`；`services/llm.py:196`；对比注册版白名单。
- **为什么是问题**：ECS 部署时这是云元数据服务（169.254.169.254）/内网探测入口；工业级出站必须受控。
- **目标方案**：URL 校验拒绝私网/链路本地段（解析后校验，防 DNS rebinding）；或显式 allowlist；/llm/test 响应不泄露内部错误细节。

### H3. 面经提取 prompt 无注入防护，导入内容直通 LLM `P1` `OPEN`
- **现状**：study_chat/出题有 context_contract 防注入指令，但面经导入的提取 prompt（bank.py `_extract_questions`）没有"忽略文档内指令"约束；恶意 PDF 可注入指令操纵提取结果（入库污染）。无内容级注入检测。
- **代码**：`server/app/api/v1/bank.py:642-700`（提取 prompt 无防注入段）；对比 `locales/zh-CN.generation.json:109`（聊天有合同）。
- **为什么是问题**：导入是不可信输入的最大入口；入库材料会进后续教学 prompt，形成持久化注入链。工业级要求"外部内容=数据"的全链路一致执行。
- **目标方案**：所有消费外部内容的 prompt 统一注入防护合同；提取结果过结构校验（已有）+ 可疑指令模式检测；入库审核时展示原文对照。

### H4. 个人版文档解析在主进程线程池，缺 zip bomb/页数/像素闸 `P2` `OPEN`
- **现状**：注册版解析接近工业级（6MB/子进程/rlimit/zip bomb/30 页），个人版缺这些闸且在主进程 to_thread 解析——恶意 DOCX/PDF 可耗尽内存拖垮 API。
- **代码**：`server/app/services/text_extract.py:104,152,217`；对比注册版解析闸。
- **为什么是问题**：个人版≠可信输入版（面经来自网上下载）；DoS 面与注册版不一致是双标。
- **目标方案**：把注册版的解析闸（大小/页数/像素/解压比/子进程隔离）抽成共享模块，两版共用。

### H5. 前端无 CSP，Markdown 渲染靠手写白名单 sanitizer `P2` `OPEN`
- **现状**：react-markdown skipHtml + 手写白名单 sanitizer（无 DOMPurify）；Bank.tsx 有受控 trusted 旁路；无 Content-Security-Policy 头；注册版也无 HSTS。
- **代码**：`web/src/pages/Bank.tsx:680`；渲染链 `web/package.json:26-34`；nginx 配置无 CSP。
- **为什么是问题**：LLM 输出+导入内容都是半不可信 HTML 源；手写 sanitizer 的绕过风险高于成熟库；CSP 是纵深防御最后一层。
- **目标方案**：DOMPurify 替换手写白名单；nginx 加 CSP（script-src 'self'）；注册版加 HSTS；trusted 旁路收敛并审计。

### H6. Python 依赖零锁定零审计 `P2` `OPEN`
- **现状**：requirements.txt 全 `>=` 无上限无 lock；无 dependabot/renovate/pip-audit；前端有 package-lock 但 Vite 停留 5.x。
- **代码**：`server/requirements.txt`；`.github/`（无 dependabot 配置）。
- **为什么是问题**：不可复现构建（今天装和下月装版本不同）+ 已知 CVE 无感知；工业级供应链管控是底线。
- **目标方案**：uv lock/pip-tools 锁定 + dependabot + CI 跑 pip-audit/npm audit；升级走 PR 回归。

### H7. 注册版会话与账号安全缺口 `P2` `OPEN`
- **现状**：session 固定 7 天不轮换、无空闲超时；密码仅长度策略；无 MFA/邮箱验证；用户名可枚举（注册/恢复响应差异）；无安全审计日志。CSRF 三重防线（Origin 守卫+SameSite=strict+__Host-）做得对。
- **代码**：`server/app/api/public_auth.py:34,180,227-229`。
- **为什么是问题**：公网注册版的账号是真实攻击面；会话固定与枚举是 OWASP 常见项。
- **目标方案**：会话滑动轮换+空闲超时；统一注册/恢复响应防枚举；登录审计日志；可选 TOTP。

### H8. 裸 dict 接收 body 的端点残留 `P2` `OPEN`
- **现状**：5 处端点用裸 dict 接 body（部分无大小/深度限制），Pydantic 校验覆盖有洞；bank 分页 le=5000 偏大。
- **代码**：`server/app/api/v1/bank.py:267`、`learning_feedback.py:25`、`telemetry.py:44`、`learning_projects.py:188`、`public_content.py:557`。
- **为什么是问题**：无 schema 的输入面=注入与资源滥用的温床；telemetry 端点无认证更需限制。
- **目标方案**：全部换显式 Pydantic schema + 大小/深度限制；分页上限收敛到合理值。

---

## I. 工程化与交付

### I1. 文档与实现漂移：ARCHITECTURE/README 部分过时，runbook 与脚本矛盾 `P1` `OPEN`
- **现状**：skill 明确提示 ARCHITECTURE.md/README.md 部分描述已过时；restore runbook 说"可覆盖生产"而脚本禁止；KNOWN_ISSUES 原文件曾被访谈报告覆盖（本次已重建为短板清单）。
- **代码**：`ARCHITECTURE.md`、`README.md`；`deploy/ECS_DEPLOY_RUNBOOK.md:517` vs `deploy/restore.ps1:12-14`。
- **为什么是问题**：面试演示时文档与行为不一致会被当场戳穿；运维文档错误在事故时是致命的。
- **目标方案**：文档对齐当前实现（以调用链/迁移/测试为准）；关键 runbook 步骤加"与脚本行为核对"清单；ADR 记录重大取舍。

### I2. 无 ADR/决策记录体系，取舍理由散落在代码注释 `P2` `OPEN`
- **现状**：大量优秀取舍（单并发、fail-closed、字节估算、无 SDK）的理由写在代码注释/文件头，无统一决策记录；面试讲述依赖个人记忆。
- **代码**：如 `project_jobs.py:158-173` 注释、`llm.py:1-11` PRD 注释、`evolution.py:1-8`。
- **为什么是问题**：工业级工程用 ADR 沉淀"为什么"，新人/面试官可追溯；也是你自己面试叙事的弹药库。
- **目标方案**：`docs/adr/` 每决策一页（背景/选项/决定/后果），先把本清单里 ACCEPTED 项和核心取舍写成 ADR。

### I3. 前端测试用 Node 原生 runner，无组件交互/E2E 常态化 `P2` `OPEN`
- **现状**：前端 243 测试用 node --test + react-test-renderer（无 jest/vitest/testing-library 交互模拟）；Playwright 验收脚本存在但 opt-in 非常态；无 CI（F3）。
- **代码**：`web/package.json:13`；`web/tests/`；`server/acceptance/study_visible_latency.cjs`。
- **为什么是问题**：渲染器测试测不了用户交互（输入/点击/流式更新）；关键 UX 路径（发送→流式→中断→重试）无自动化回归。
- **目标方案**：关键路径补 Playwright E2E 进 CI（loopback+隔离库已有纪律）；组件交互测试按需引入 testing-library。

### I4. 双入口双版本（personal/public）行为分叉，安全闸不一致 `P1` `OPEN`
- **现状**：同一能力在两版实现不同闸（解析 H4、URL 白名单 H2、弱口令断言 G6、预算 E2 只在 public）；长期演进会持续分叉。
- **代码**：`server/app/main.py` vs `public_main.py`；`text_extract.py` 两版路径；`public_model.py` vs `llm.py`。
- **为什么是问题**：安全与成本闸"哪版有哪版没有"靠记忆维护，必然漏；工业级要求共享核心+薄入口差异。
- **目标方案**：把解析闸/URL 校验/预算账本/启动断言抽成共享层，两入口只做鉴权与路由差异；写"版本能力矩阵"文档并加一致性测试。

---

## J. 产品与测量有效性

### J1. 能力画像启发式未校准，无长期效果证据 `P1` `OPEN`
- **现状**：等级=最新独立作答得分率均值×5，置信度 0.15n 封顶 0.7，无时间衰减项、无统计校准、无"预测面试表现"的效度验证；产品已诚实标注 UNCALIBRATED。
- **代码**：`server/app/services/competency.py:15,124-148`；`models/skill.py:78`。
- **为什么是问题**：作为"测量系统"，信度（重测一致）与效度（预测真实面试）都未验证；诚实标注是优点但不能替代验证。
- **目标方案**：先做信度：同一能力域间隔重测的相关性；再做效度小样本：画像等级 vs 真实面试结果（用户自报）相关；样本不足就保持 UNKNOWN 语义并写清局限。与 D1 共用标注基建。

### J2. 间隔复测 1/3/7 天写死，无自适应间隔 `P2` `OPEN`
- **现状**：学习完成后固定 +1/+3/+7 天调度复测；无按表现调整间隔（答得好拉长、答得差缩短）。
- **代码**：`server/app/services/learning_loop.py:30-36`。
- **为什么是问题**：固定间隔是 SM-2 之前的水平；工业级记忆系统用自适应间隔（SM-2/FSRS）提升效率。
- **目标方案**：按复测结果调整下次间隔（简化 SM-2 即可）；与 B5 的策略候选机制打通，用 evolution 评估间隔策略。

### J3. 复测映射依赖人工审核表，覆盖不足即 UNMAPPED `P2` `OPEN`
- **现状**：原失分 criterion→新题 criterion 必须经人工审核映射表绑定，无映射判 UNMAPPED；映射表覆盖率决定复测闭环能否运转。
- **代码**：`server/app/services/retest_targets.py:70-84,196-222`。
- **为什么是问题**：人工映射是正确性护栏但也是吞吐瓶颈；题池扩大后 UNMAPPED 比例会上升，闭环空转。
- **目标方案**：LLM 生成映射候选+人工审核（复用记忆审核状态机模式）；统计 UNMAPPED 率作为运营指标。

### J4. 语音/离线/播客等外围功能质量无量化验收 `P2` `OPEN`
- **现状**：TTS 有文本规则校验但无听感评估；离线播放缓存无一致性校验；播客语言配比有规则但术语发音无回归。
- **代码**：`server/app/services/podcast_content.py:213-252`；`podcast_voice.py:238,413`。
- **为什么是问题**：外围功能"能用"与"好用"之间没有测量；面试被问"质量怎么保证"时只有规则没有数据。
- **目标方案**：抽听评审清单+术语发音回归样本；离线缓存加版本/hash 校验；质量指标进 ops 看板。

---

## 附录：面试红线（不要声称的东西）

- ❌ "我做了 RAG / 向量检索" —— 没有 embedding，只有字面匹配（A1）。
- ❌ "自主 Agent 根据任务调用工具" —— 无 tool-calling 循环（B1）；讲"确定性编排 + 受限生成节点"。
- ❌ "能力画像经过校准 / 能预测面试通过率" —— 启发式、UNCALIBRATED（J1）。
- ❌ "代码题自动执行测试" —— 静态评审，无沙箱（D7）。
- ❌ "策略自动进化生效" —— evolution 激活硬编码关闭（B5）。
- ❌ "全局单并发控制所有 LLM 成本" —— 聊天走 BackgroundTasks 绕过队列（E1）。
- ❌ "有完整灾难恢复能力" —— 恢复到生产从未演练（F6）。
- ⚠️ 讲检索修复（PR #37）前先确认已合入演示分支；讲发布版功能前核对实际部署 SHA。

## 附录：开工计划（60 项全量排程，依赖拓扑 + 并行分波）

> 规则：**同一波内的轨道互不改同一批文件，可并行**；跨波是硬依赖（前置波验收后才开工）。
> 每波给出门禁（本波完成的验收标准）。按 AGENTS.md 纪律：每项独立分支 → 隔离库验证 → PR 到 dev → 授权合入。
> 标注 `[串行]` 的轨道内部有先后；其余轨道之间可并行。估算为单人专注工时。

### 第 0 波 · 地基（先让一切改动可验证、可追溯）≈ 3–4 天
| 轨道 | 项 | 内容 | 依赖 |
|---|---|---|---|
| 0-A | **F3** | CI 测试门禁：PG service container + run_isolated_tests + 前端 node --test + ruff/tsc/knip | 无，**第一个开工** |
| 0-B | **G1** | structlog JSON 日志 + request_id 中间件贯穿 API→worker→AgentMetric/Trace + 轮转 | 无 |
| 0-C | **I2** | `docs/adr/` 骨架，把既有核心取舍（单并发/fail-closed/无 SDK/字节估算）写成 ADR | 无 |

**门禁**：PR 不绿不能合；任一次请求能用 request_id 串起 API 日志、worker 步骤、LLM 调用行。
**为什么最先**：后面 57 项每一项都要靠 CI 回归和日志排障；没有这两样，改动质量无法证明。

### 第 1 波 · 止血（资金 / 安全 / 正确性快赢）≈ 1–1.5 周
| 轨道 | 项 | 内容 | 依赖 |
|---|---|---|---|
| 1-A `[串行]` | **E1 → E2 → E3** | 聊天生成加 per-workspace 并发闸+全局 Semaphore（先不动队列）；成本账本推广到私有路径（复用 public_model 预留-结算模式）+按模型单价+日预算；API 令牌桶限流 | 0-B（账本/限流日志要带 request_id） |
| 1-B `[串行]` | **G5 → F6** | 密钥信封加密（Fernet，主密钥进 env）+迁移存量明文行；备份加密+异地+保留策略+受控"恢复到生产"流程+演练一次记录 RTO | G5 先于 F6（备份加密依赖密钥方案） |
| 1-C | **G6 + H1** | 启动断言拒绝弱默认 token/口令+首启随机 token；token 表（过期/轮换/撤销）+失败限速 | 无 |
| 1-D | **H2 + H8** | LLM base_url 私网/链路本地黑名单（解析后校验）+/llm/test 不泄露内部细节；5 处裸 dict 换 Pydantic schema+大小限制 | 无 |
| 1-E | **H3 + H4** | 面经提取 prompt 补注入防护合同；注册版解析闸（zip bomb/页数/像素/子进程）抽共享模块给个人版 | 无 |
| 1-F | **A2** | 合入并验证 PR #37 查询理解修复，泛查句式进回归测试 | 0-A（CI 跑回归） |
| 1-G | **G4 + F7 + F8** | readiness/liveness 拆分（compose 打 readiness）；全局异常处理器统一错误信封；静默吞错改 logger.warning+失败计数 | 0-B |
| 1-H | **G3** | 最小告警巡检：worker 心跳/队列积压/日成本/错误率/备份新鲜度，超阈值走 SMTP | 0-B、1-A（成本指标真实化后告警才有意义，可先上心跳/错误率部分） |

**门禁**：并发/预算/限流有测试证明超限被拒；拖库拿不到明文密钥；恢复演练有回执；弱默认启动即拒。
**注意**：1-A 与 1-G 都动 `main.py`/中间件，合并时注意冲突；1-A 的 E1 只做"闸"，队列化留给第 4 波 F1/F4 一起重构，避免反复改。

### 第 2 波 · 证据基建（评测集是一切升级的前提）≈ 1–1.5 周
| 轨道 | 项 | 内容 | 依赖 |
|---|---|---|---|
| 2-A | **D1** | judge golden set：30–50 题人工逐项判档，一致率/kappa/总分 MAE 评测脚本+回归入口 | 0-A |
| 2-B | **A4** | 检索评测集：50–100 条 query→相关题/笔记标注，recall@12/MRR/零命中率基线脚本 | 1-F（修复后的查询理解作为基线版本） |
| 2-C | **D4** | 全部 prompt bundle 化+内容 hash 版本落库（复用 judge 模式）+prompt 回归夹具 | 2-A 的评测入口（同波内 D4 收尾接上 D1） |
| 2-D | **H6** | uv lock/pip-tools 锁定+dependabot+CI pip-audit/npm audit | 0-A |
| 2-E | **I1** | 文档对齐：ARCHITECTURE/README 按当前实现修订，runbook 与脚本矛盾修正 | 第 1 波完成后（行为已变） |

**门禁**：D1/A4 各有一份带数字的基线报告落 `docs/benchmarks/`；此后任何 judge/检索/prompt 改动必须附对比。
**为什么在此**：第 3 波起的每一项能力升级（混合检索/摘要/缓存/影子复核/难度标定）都要用这两套评测集证明"变好了"，否则只是堆名词。

### 第 3 波 · 上下文与成本能力（LLM 网关现代化）≈ 1.5–2 周
| 轨道 | 项 | 内容 | 依赖 |
|---|---|---|---|
| 3-A `[串行]` | **C1 → D6** | tiktoken/provider tokenizer 适配表精确计数；溢出 400 自动重拟合降档重试+MockTransport 演练 | 2-C（prompt 版本化后回归可跑） |
| 3-B | **C3** | prompt 缓存友好排布+provider cache 参数+评分结果缓存（input_hash×rubric_version×fingerprint）+命中率上报 | 2-C、D1（缓存正确性用 golden set 验证） |
| 3-C | **C2** | LLM 滚动摘要+pinned facts；"约束保留率"评测 | 3-A（摘要调用预算按精确 token 计） |
| 3-D | **E4** | 共享 httpx 连接池；检索与首段生成并行/预取；artifact 校验不阻塞首字；bench_study_chat 配对 A/B 出 TTFT 收益报告 | 3-B（cache 是 TTFT 收益大头，先落再测总收益） |
| 3-E | **D5** | 影子复核产品化：新模型对历史 N 题只读复评→对齐报告→质量门禁；修正 CONFIG_CHANGED 文案 | 2-A（对齐报告对着 golden set 算） |
| 3-F | **D2** | project 类回答事实性抽检（cheap judge 核对代码证据一致性），违规率统计 | 2-A（judge 基建复用） |

**门禁**：TTFT p50/p95 与成本有前后对比数字；换模型走影子门禁而非口头；长对话约束保留率有评测。

### 第 4 波 · Agent 深度与检索升级（面试核心叙事）≈ 2–3 周
| 轨道 | 项 | 内容 | 依赖 |
|---|---|---|---|
| 4-A `[串行]` | **A1 → A3 → A6** | jieba+tsvector BM25 → pgvector 混合召回 RRF →（可选 rerank）；sources 带 total/shown/truncated 契约；记忆按技能域相关性召回进聊天 | 2-B（每步用评测集证明提升） |
| 4-B | **B1** | 受限 tool-calling 环：search_bank/read_note/read_file 白名单、≤5 轮、预算封顶、逐轮 trace | 4-A 的检索工具（同波内 B1 收尾接 A1；工具 schema 可先行设计） |
| 4-C `[串行]` | **B2 → B4 → B3** | 三级路由（正则→小模型→低置信度问用户）+置信度落 trace；会话显式 mode；工作流条件边/回退/动态插入 | 2-B 路由评测集扩长尾（auto_quality 已有） |
| 4-D | **A5** | tree-sitter 符号表+调用边一跳扩展，证据命中率评测 | 2-B（评测方法复用） |
| 4-E | **C4** | 记忆 consolidation 离线任务+时间衰减召回 | 3-C（摘要基建共用） |

**门禁**：检索升级每一步有 recall/MRR 增量数字；工具环有越界/超预算被拦的测试；路由长尾准确率有回归。
**注意**：4-A 与 4-B 都动 `study_chat_context.py`/`study_chat.py`，建议同一人串行做或明确文件分工。

### 第 5 波 · 工业化收尾（可靠性 / 观测 / 扩展）≈ 2 周
| 轨道 | 项 | 内容 | 依赖 |
|---|---|---|---|
| 5-A `[串行]` | **F1 → F4 → E1 队列化** | 事务性 outbox 表；worker SIGTERM 主动释放租约+API drain+graceful shutdown；聊天生成正式并入持久队列（替换第 1 波的临时闸） | 1-A（临时闸先保安全，此处重构） |
| 5-B | **F2** | 统一队列抽象（合并播客第二套实现）；隔离库 N worker 分片认领 PoC+竞争消费正确性测试 | 5-A |
| 5-C | **G2 + E5** | prometheus_client /metrics（HTTP 直方图/队列深度/LLM 计数）+指标预聚合表替换 5000 条内存分位 | 0-B、5-A（队列深度指标依赖统一队列） |
| 5-D | **G7** | 观测表分区+保留策略；导出补全；删除冷静期 | 5-C（聚合永久、明细 TTL 的分层依赖预聚合） |
| 5-E | **F5** | alembic 或自研执行器版本记账+schema diff；CI 空库全量迁移+幂等重放测试 | 0-A |
| 5-F | **I4** | 双版本共享层收尾：把第 1 波抽的解析闸/URL 校验/预算/断言统一成共享模块+能力矩阵文档+一致性测试 | 1-C/1-D/1-E、1-A |
| 5-G | **H5 + H7** | DOMPurify+CSP/HSTS；注册版会话滑动轮换+防枚举+审计日志 | 无硬依赖，放此波平衡负载 |
| 5-H | **I3** | 关键 UX 路径 Playwright E2E 进 CI（发送→流式→中断→重试） | 0-A、5-A（流式链路稳定后再固化 E2E） |

**门禁**：kill -9 / SIGTERM / 容器重建三种故障注入下不丢任务不重复付费（有测试回执）；/metrics 可被抓取；多 worker PoC 报告。

### 第 6 波 · 测量有效性与进阶（差异化谈资）≈ 2 周+
| 轨道 | 项 | 内容 | 依赖 |
|---|---|---|---|
| 6-A | **D3** | 难度/区分度标定：真实作答反推+试答池 | 2-A（标注流程共用）、5 波后数据积累 |
| 6-B | **J1** | 画像信度（间隔重测相关）+效度小样本（vs 真实面试自报） | 2-A、6-A |
| 6-C `[串行]` | **J3 → J2** | LLM 映射候选+人工审核降 UNMAPPED 率；自适应复测间隔（简化 SM-2） | J3 先（映射覆盖是复测运转前提） |
| 6-D | **B5** | evolution 灰度激活：小流量+指标守护+自动回滚 | 6-C（间隔策略作为首个灰度候选）、5-C（守护指标） |
| 6-E | **B6** | 规则候选+模型排序的个性化追问/计划 | 6-A（难度信号） |
| 6-F | **D7** | 代码沙箱：一次性容器+网络禁用+资源限额，执行证据进 rubric 链 | 5 波（可靠性基建）；或正式 ACCEPTED 写 ADR |
| 6-G | **D8** | vision 输入（image_url 块+图片闸）；ASR 本地 whisper；TTS 抽听回归 | 3-A（多模态 token 预算） |
| 6-H | **J4** | 播客听感评审清单+术语发音回归+离线缓存 hash 校验 | 无硬依赖 |

**门禁**：每项产出"数字或正式 ACCEPTED ADR"二选一，不允许静默搁置。

### 依赖速查（关键链）
- F3/G1 → 一切
- E1 闸 → E2 账本 → G3 成本告警；E1 队列化 ← F1 outbox ← F4 停机
- G5 加密 → F6 备份加密演练
- D1 golden set → D4 回归 / D5 影子 / D2 抽检 / D3 标定 / J1 效度
- A4 评测集 → A2 基线 → A1 混合检索 → B1 工具环
- C1 精确 token → D6 溢出恢复 / C2 摘要预算
- C3 缓存 → E4 TTFT 总收益
- 5-C 指标 → G7 保留分层 / B5 灰度守护
- J3 映射 → J2 间隔 → B5 灰度

### 里程碑（面试叙事 checkpoints）
- **M1（第 1 波末）**："我发现并修复了自己系统的资金与安全洞"——并发闸/成本账本/密钥加密/SSRF，事故驱动叙事。
- **M2（第 2 波末）**："我先建评测再升级"——golden set + 检索基线两份带数字的报告。
- **M3（第 3+4 波末）**："完整的工业级 LLM 网关 + 混合检索 + 受限 agent 环"——TTFT/成本/召回三组前后对比数字。
- **M4（第 5 波末）**："故障注入下不丢不重"——outbox/停机/多 worker 测试回执。
- **M5（第 6 波末）**："测量系统自己也被测量"——judge 一致率、画像信效度、灰度守护。