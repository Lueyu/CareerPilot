"""Weekday curriculum for the November–December Agent sprint."""

from app.services.daily_learning_plan import SprintBlock


_FOUNDATION = "Agent 基础"
_PROJECT = "项目冲刺"
_APPLICATION = "投递准备"


def _interview(
    topic: str,
    stage: str,
    title: str,
    days: tuple[int, int, int],
    *,
    diagnose: str,
    repair: str,
    retest: str,
) -> tuple[SprintBlock, ...]:
    keys = tuple(f"sprint:question:{topic}:attempt:{attempt}" for attempt in (1, 2, 3))
    return (
        SprintBlock(
            keys[0], days[0], stage, f"{title} · 第 1 轮闭卷诊断", 40,
            f"闭卷口述 4 分钟并保存原答。{diagnose}回看原答，记录一条具体失分及补证动作。",
        ),
        SprintBlock(
            keys[1], days[1], stage, f"{title} · 第 2 轮修错验证", 55,
            f"先复述上轮失分。{repair}保存修复前后证据，再答一次并标出补齐的判断。",
            keys[0],
        ),
        SprintBlock(
            keys[2], days[2], stage, f"{title} · 第 3 轮新情景复测", 30,
            f"闭卷限时 4 分钟。{retest}留存作答，核对旧失分是否复现。",
            keys[1],
        ),
    )


_QUESTIONS = (
    *_interview(
        "python-api", _FOUNDATION, "Python API 的输入与错误契约", (0, 1, 4),
        diagnose="为一个接收消息列表的 Python API 设计输入校验、成功响应和错误返回。给出缺字段与上游超时各自的处理。",
        repair="用本地桩分别发送缺字段、合法数据和模拟上游超时三类请求。纠正状态码或错误体失配，断言三类响应。",
        retest="客户端收到网关超时后重发同一请求。解释校验在哪一层完成、调用方能重试哪类错误，以及如何避免重复执行。",
    ),
    *_interview(
        "llm-boundary", _FOUNDATION, "模型客户端如何隔离供应商差异", (0, 2, 4),
        diagnose="两个模型返回内容、结束原因和用量的格式各异。解释 Python 应用层接口、边界转换和可预测的异常类型。",
        repair="为本地桩构造正常文本、空内容和供方返回限流三个响应。调整适配器并验证业务层只消费统一结果或已分类错误。",
        retest="新供应商遗漏用量且合法拒答与超时都没有文本。说明如何表达三种结果，并按它们设计日志与客户端提示。",
    ),
    *_interview(
        "structured-output", _FOUNDATION, "结构化输出怎样防止坏数据流入业务", (1, 2, 5),
        diagnose="模型需给出工单优先级与标签。解释 JSON Schema 约束、业务校验、有限重试和失败状态如何配合。",
        repair="让桩返回可解析 JSON 但优先级枚举无效，再返回缺业务必填证据的 JSON。验证两类响应都被拦截且重试有上限。",
        retest="工单分派对象通过 Schema 校验却引用了已经停用的队列。解释选择哪一层校验、如何回退以及什么结果能对用户显示。",
    ),
    *_interview(
        "streaming", _FOUNDATION, "流式响应如何判断完成与中断", (1, 3, 5),
        diagnose="画出从模型到 API 再到浏览器的文本增量流，解释事件顺序、终止、失败、取消和部分文本如何呈现。",
        repair="用本地流模拟三段文本后断开并重连。验证终止事件、部分状态及重复事件的去重，记录客户端与服务端各自的表现。",
        retest="浏览器已看见最后一段文本却没收到终止事件。说明界面状态、持久化和重连策略，给出两个可观察的验收信号。",
    ),
    *_interview(
        "tool-contract", _FOUNDATION, "模型发起工具调用后由谁做决定", (2, 3, 5),
        diagnose="给一个只读工单查询工具定义参数、结果和错误契约，解释模型提议、宿主校验、执行和结果回传各自的职责。",
        repair="用本地桩测试合法查询、未知工具名与权限不足。确认后两种不会执行工具，并核对失败分类和日志记录。",
        retest="新增可更新工单的工具。给出写权限检查、用户确认、幂等键和结果回传的时序，解释失败如何向用户呈现。",
    ),
    *_interview(
        "state-machine", _FOUNDATION, "Agent 状态与中途恢复", (2, 4, 6),
        diagnose="为执行工具的多步任务画出状态转换，解释运行 ID、检查点、终态和进程重启后的恢复。可以类比 Kubernetes 控制器的调谐。",
        repair="在本地任务执行了模拟写操作、尚未记录完成时终止执行器。重启并验证检查点、幂等键和终态，留下写入次数对照。",
        retest="任务恢复时发现工具结果已落盘且任务标记仍为执行中。解释如何对账、选择恢复节点，并避免两个 worker 同时执行。",
    ),
    *_interview(
        "workflow-agent", _FOUNDATION, "工作流与 Agent 循环怎么选", (2, 3, 6),
        diagnose="为固定顺序的报表生成和目标模糊的文档排查分别设计执行方式。说明宿主循环、步数和预算停止条件。",
        repair="让本地执行器遇到模型连续请求同一只读工具的情况。用终止测试验证预算或无进展规则，以及可解释的停止状态。",
        retest="审批步骤固定但取证路径动态。画出混合流程，解释人审节点、模型决策范围和提前退出条件。",
    ),
    *_interview(
        "rag-retrieval", _FOUNDATION, "RAG 召回链路与定位漏检", (3, 4, 6),
        diagnose="从文档入库讲到查询返还证据。区分切块、向量或关键词检索、过滤与重排，举例说明一次漏召回的定位顺序。",
        repair="构造含相似词和正确资料的小语料，用三条带标注查询比对两套切块或检索配置。记录预期资料在 Top-k 的命中和排名。",
        retest="查设备型号时新旧手册结构类似，正确版本排在后面。说明如何利用版本字段、检索、重排定位并度量改善。",
    ),
    *_interview(
        "rag-grounding", _FOUNDATION, "RAG 答案怎样引用证据和拒答", (3, 5, 7),
        diagnose="给出证据充足、互相冲突、证据不足三类结果的引用或拒答策略，说明召回正确与答案可靠各怎么测。",
        repair="建立三条带标注样例，复现答对却引用错段、证据冲突和无证据仍断言。修好一项并核验答案与引用是否一致。",
        retest="两份文档对退款期限说法不同，其中一份更新且适用范围更窄。解释版本、范围、引用和何时向用户澄清。",
    ),
    *_interview(
        "memory", _FOUNDATION, "会话状态、长期记忆与上下文", (4, 5, 7),
        diagnose="设计一个跨会话助手，说明会话状态、可持久化信息、上下文选取、来源和删除。用一条用户更正说明如何避免旧信息复活。",
        repair="在本地构造两位用户及一条相互矛盾的偏好。执行更新和删除，验证另一用户读不到数据且旧摘要不会复用已删除偏好。",
        retest="用户要求忘记地址，当前会话历史摘要仍包含旧地址。说明删除传播、检索缓存与后续验证，区分可观测证据。",
    ),
    *_interview(
        "async", _FOUNDATION, "Python 异步工具的超时与取消", (4, 6, 8),
        diagnose="设计三个独立工具的并发执行。说明 async 适用边界、有限并发、总截止时间、单工具超时及用户取消的传播。",
        repair="用本地延时桩设置一个慢工具和一个异常工具。验证并发上限、终止后的任务清理和允许返回的部分结果。",
        retest="批量任务的某个工具内部有阻塞 I/O，取消后它仍占着线程。说明如何隔离、限制资源、判断何时停止重试。",
    ),
    *_interview(
        "eval", _FOUNDATION, "Agent 评估怎样捕捉真实回归", (5, 6, 8),
        diagnose="设计一个离线小评测，分别覆盖任务通过、证据质量、工具行为和耗时。解释基线、样例分层及失败项复盘。",
        repair="为本地两版输出制作带标签的最小数据集，加入工具失败和应拒答案例。纠正只看总分的判断并列出逐条差异。",
        retest="新版总分上涨但高风险动作通过率下降，样本只有十几条。说明发布判断、人工复核和下一批补样的优先级。",
    ),
    *_interview(
        "reliability", _FOUNDATION, "重试、幂等与重复副作用", (5, 7, 9),
        diagnose="解释失败前、已提交后响应丢失和连续限流三种情况的处理。用一个写工具说明幂等键、退避和总预算。",
        repair="在本地模拟写入已经提交但响应超时，随后重试。核对只有一次写入并记录可关联的运行 ID 与重试原因。",
        retest="三个并发调用各自重试，下游恢复但积压继续增加。解释全局限流、退避、停止策略和最先查看的信号。",
    ),
    *_interview(
        "security", _FOUNDATION, "Agent 的源数据与执行权限边界", (5, 7, 9),
        diagnose="说明来源文本、模型输出以及已授权动作的边界。用一个只读检索加写工具的场景解释权限最小化、显式确认和日志脱敏。",
        repair="用本地无副作用的桩模拟文档内容提出越权动作以及用户访问隔离失败。验证拦截、允许路径和审计脱敏。",
        retest="模型从附件提取了敏感字段，后续工具报告把它带入日志。说明调用与记录层各自的约束、响应方式和验证依据。",
    ),
    *_interview(
        "context-cost", _FOUNDATION, "上下文、延迟和预算如何取舍", (6, 8, 10),
        diagnose="拆解一次 Agent 运行的输入输出 token、检索、工具与模型耗时。说明什么内容能裁剪或缓存，以及如何检查质量回归。",
        repair="用相同的局部样例比对有无上下文裁剪的两组记录，核对 token、延迟和引用正确率。没有模型计费数据时标出估算依据。",
        retest="上下文裁剪令成本下降但遗漏最新一轮用户限制。说明保留策略、对照指标和可接受的回退条件。",
    ),
    *_interview(
        "operations", _FOUNDATION, "Agent 服务的可观测部署与恢复", (6, 8, 10),
        diagnose="用一次 API 部署和 PostgreSQL 恢复为例，说明 readiness、运行 ID、发布前检查、版本回退、恢复验证以及 RPO/RTO。",
        repair="用隔离环境或本地桩回放一次启动失败，并执行一次临时数据恢复核对。记下从告警到定位的证据，以及实际能恢复的数据范围。",
        retest="新版本启动健康但任务重启后丢失最近更新。解释先看什么、何时回滚、如何验证数据和如何量化中断。",
    ),
    *_interview(
        "project-contribution", _PROJECT, "项目价值与个人贡献怎么证明", (7, 10, 15),
        diagnose="根据选定项目说清目标用户、关键动作、自己负责的决策和结果。指出每一项能对应的材料，并标记尚缺的证据。",
        repair="对照一次真实操作、提交记录或个人笔记，校正一条模糊归因和一条无来源成果。留下证据链接、记录或明确标明待验证。",
        retest="面试官追问团队已有基础和你亲手决定的改动。结合一项可核对证据说明作用、取舍及已确认的结果。",
    ),
    *_interview(
        "project-architecture", _PROJECT, "项目主链路怎样从入口跑到结果", (8, 11, 16),
        diagnose="用当前仓库画出用户动作、API、模型或工具、存储和最终界面。说明同步与异步边界，给出对应的观察点。",
        repair="跟踪一次本地请求，补齐图中遗漏的一段状态或依赖。对照请求、日志或数据库快照，核验正常和降级路径。",
        retest="下游响应突然拉长且用户重复提交。沿项目实际链路解释哪些状态会变化、哪些证据能定位问题。",
    ),
    *_interview(
        "project-failure", _PROJECT, "怎么定位项目中一次关键失败", (9, 12, 17),
        diagnose="选一次可重现的项目失败，说明用户症状、初步假设、最早错误证据、可验证修复和剩余风险。",
        repair="在隔离环境或桩环境重现故障，控制一次变量，记录首次证据、修改或恢复操作，以及原样例回放结果。",
        retest="用户报告同一界面症状，日志却显示另一个阶段失败。说明如何改写假设、缩小范围及决定何时宣告恢复。",
    ),
    *_interview(
        "project-evaluation", _PROJECT, "项目效果与回归怎样衡量", (10, 13, 18),
        diagnose="给项目主链路选择可执行的成功条件、最小基线和需要单独追踪的失败样例，并指出样本局限。",
        repair="执行一次小样回归，逐个标出成功、失败和无法判断。针对一条误判修正标注，重算并保存分子分母。",
        retest="面试官发现成功率只统计了正常返回的任务。解释端到端分母、被排除的样例和据此得出的发布判断。",
    ),
    *_interview(
        "project-tradeoff", _PROJECT, "描述项目的一次设计取舍", (11, 14, 19),
        diagnose="选项目实际发生的一项设计决定，说明约束、当时的两个可行方案、选择依据和重新评估的触发条件。",
        repair="找出一项原先凭感觉的判断，用同一输入跑两套可复现方案或明确记录可查的历史证据。更新选择理由和适用范围。",
        retest="假设流量十倍而预算维持原样。基于同一设计解释先保什么、改变什么、用什么阈值决定进一步迁移。",
    ),
    *_interview(
        "project-isolation", _PROJECT, "项目如何隔离数据与危险动作", (12, 15, 20),
        diagnose="沿项目的一条可信任边界说明身份、数据来源、工具权限和留痕。指出已实现的保护及需验证的缺口。",
        repair="用两组本地假身份和虚构数据核对一条允许路径及一条应拦截路径。检查最早授权点及日志脱敏，记录实际观察。",
        retest="隔离验证通过但一条调试日志暴露了敏感字段。说明如何确认影响范围、修复和回归验证。",
    ),
    *_interview(
        "project-cost", _PROJECT, "项目性能与模型成本怎样陈述", (14, 17, 21),
        diagnose="把一次项目运行拆成可观测阶段，说明计量口径、样本规模、统计量和成本估算边界。使用证据支持陈述。",
        repair="用固定样例做一轮重复测试，记录环境、成功和失败样本、分阶段耗时及可取到的用量。标出波动和缺失信息。",
        retest="项目演示变慢但总 token 近似不变。说明如何从分阶段的数据判别瓶颈，并报出能负责的数字和条件。",
    ),
    *_interview(
        "project-rollout", _PROJECT, "项目发布、回滚与数据恢复", (16, 19, 22),
        diagnose="描述项目当前可执行的本地部署与恢复路径，说明示范版本、依赖、数据、验收点，标出未验证环节。",
        repair="在临时环境验证一次恢复或回滚演练，再跑主链路。记录版本、检查点、退出码及剩余风险。",
        retest="恢复后的服务能启动却读不到一个旧任务。说明如何区分版本、数据库和附件问题，并给出何时算复原的证据。",
    ),
)


_PRACTICE = (
    SprintBlock(
        "sprint:foundation:workspace", 0, _FOUNDATION, "建练习沙盒与错题记录", 100,
        "准备一个隔离的 Python 练习目录和本地模型桩。每道题保留日期、原答、具体失分、修复证据和复测结果。先用一次正常请求跑通记录流程。",
    ),
    SprintBlock(
        "sprint:foundation:python-api-lab", 0, _FOUNDATION, "练 Python 接口、校验与错误分类", 145,
        "做一个接收消息并返回结果的最小 API。练习字典、类型标注、异常与输入模型。验证正常、缺字段、空列表、上游超时四种结果，保存响应差异。",
    ),
    SprintBlock(
        "sprint:foundation:model-client-lab", 0, _FOUNDATION, "接起最小模型客户端", 75,
        "用本地桩接起模型请求和响应，区分角色、文本、用量和完成原因。让两种响应格式通过同一个应用接口，写下调用边界与一个失败例子。",
    ),
    SprintBlock(
        "sprint:foundation:structured-lab", 1, _FOUNDATION, "练结构化输出的有效与无效样例", 145,
        "为小型工单分类定义字段和业务约束。验证语法错误、字段合法但业务无效、正常三种输入，记录重试次数和终态。",
    ),
    SprintBlock(
        "sprint:foundation:streaming-lab", 1, _FOUNDATION, "练流式文本与事件生命周期", 120,
        "用本地服务演示增量、成功终止、异常终止和客户端取消。保存一条完整事件序列，核对部分结果、完成状态与任务清理。",
    ),
    SprintBlock(
        "sprint:foundation:harness-lab", 2, _FOUNDATION, "搭最小 Agent 执行器", 115,
        "用模型桩、只读工具和工具结果回传跑通执行循环。演示固定工作流和由模型选工具两条路径，验证最大步数、非法工具、正常完成。",
    ),
    SprintBlock(
        "sprint:foundation:state-lab", 2, _FOUNDATION, "画执行状态与恢复边界", 75,
        "为执行器记录运行 ID、阶段和检查点。中途停止一次，区分已提交的模拟副作用和只存在内存的状态，用控制器调谐类比画出恢复路径。",
    ),
    SprintBlock(
        "sprint:foundation:rag-retrieval-lab", 3, _FOUNDATION, "搭可观察的 RAG 检索链路", 105,
        "用几份自制文档完成切块、检索和来源保留。用三条带标注查询对比切块或排序策略，保存输入、候选、排名与不命中的原因。",
    ),
    SprintBlock(
        "sprint:foundation:rag-answer-lab", 3, _FOUNDATION, "练 RAG 引用核对与拒答", 70,
        "回放有证据、冲突和证据不足的三种输入。记录检索结果、生成答案和对应引用，独立核对召回与归因。",
    ),
    SprintBlock(
        "sprint:foundation:memory-lab", 4, _FOUNDATION, "练会话状态、记忆与上下文选取", 95,
        "给两段会话设计会话状态、持久记忆和一次上下文装配。验证更正、删除、跨用户隔离，记录每条信息的用途、来源和读取依据。",
    ),
    SprintBlock(
        "sprint:foundation:async-lab", 4, _FOUNDATION, "练异步并发、超时和取消", 75,
        "用延迟桩对照串行与有限并发。主动取消一次，检查剩余任务是否清理，记录总耗时、超时分类和并发上限。",
    ),
    SprintBlock(
        "sprint:foundation:eval-lab", 5, _FOUNDATION, "建小型回归评测和失分判据", 65,
        "准备至少六条标注样例，覆盖主路径、拒答、工具异常。先出手工基线，再用同一规则比较两组结果并记录三项分开统计的指标。",
    ),
    SprintBlock(
        "sprint:foundation:reliability-lab", 5, _FOUNDATION, "对照可靠性和权限错误", 35,
        "用本地桩回放重复请求、权限不足和不可重试错误。列出每个失败在哪一层拦截、如何确认以及日志需要保留的关联字段。",
    ),
    SprintBlock(
        "sprint:foundation:operability-lab", 6, _FOUNDATION, "练部署检查、回滚与备份恢复", 75,
        "在沙盒记录版本、依赖与健康信号。演练停止、启动、健康检查并恢复一份临时数据，核对至少两个关键实体及实际恢复耗时。",
    ),
    SprintBlock(
        "sprint:foundation:cost-lab", 6, _FOUNDATION, "复盘基础首轮和运行成本", 65,
        "用一次完整运行标出模型、检索、工具、存储耗时和可获得的 token。逐域核对实操与错题证据，圈出最值得迁移到 12 月项目的两处缺口。",
    ),
    SprintBlock(
        "sprint:project:choose-scope", 7, _PROJECT, "确定项目主链路和证据范围", 100,
        "选定一个可展示的实际项目，画出用户入口与结果，写定一个演示主线和三个验收点。列出版本、材料、个人职责、待验证事项和明确的演示边界。",
    ),
    SprintBlock(
        "sprint:project:baseline-run", 7, _PROJECT, "跑通项目基线并确认缺口", 110,
        "按现有说明在隔离环境运行选中的主链路，保存版本、步骤和结果。缺少依赖时记录具体阻塞和可验证的本地替代，按影响挑出三个缺口。",
    ),
    SprintBlock(
        "sprint:project:walkthrough", 8, _PROJECT, "从用户入口追到持久化与返回", 115,
        "实际跟一条请求，标出关键文件、状态和观测点。用日志、测试输出或本地快照验证至少三个连线，给假设连线标待证。",
    ),
    SprintBlock(
        "sprint:project:trace-example", 8, _PROJECT, "制作一个可对照的运行记录", 95,
        "保留同一操作的正常和降级记录。给每条记录配关联标识、起止状态和用户看到的内容，加入脱敏示例与复现条件。",
    ),
    SprintBlock(
        "sprint:project:failure-replay", 9, _PROJECT, "重放一个优先级最高的真实故障", 165,
        "从现有症状或基线缺口选择一个故障。固定环境和输入，控制一次变量，定位最早出现差异的位置。保留复现步骤、日志和用户影响。",
    ),
    SprintBlock(
        "sprint:project:acceptance-cases", 9, _PROJECT, "建立主线验收和错误样例", 145,
        "为可演示主链路准备至少一个正常、一个空输入或异常和一个恢复案例。给每条写预期行为、证据位置与当前结果。",
    ),
    SprintBlock(
        "sprint:project:eval-dataset", 10, _PROJECT, "定项目评估口径和样例集", 145,
        "为关键行为挑六至十条带预期的小样。写清通过条件、人工复核规则、覆盖范围和样例来源，检查正常、边界、失败是否都有。",
    ),
    SprintBlock(
        "sprint:project:baseline-measurement", 10, _PROJECT, "跑小样基线并核对证据", 115,
        "保存逐例结果、计数口径、错误阶段和不可判定项。挑一个反映用户影响的问题，固定回放输入并记下修复前证据。",
    ),
    SprintBlock(
        "sprint:project:select-fix", 11, _PROJECT, "选择一处高影响改进", 95,
        "按用户影响、修复成本和验证难度排现有缺口。选一处能回放的设计或故障，留存两种可行处理、决策理由和完成证据。",
    ),
    SprintBlock(
        "sprint:project:fix-primary-path", 11, _PROJECT, "修复或收紧一处项目主链路", 230,
        "做尽可能小的改动，固定输入比较前后。验证原障碍与一条邻近正常路径，保存差异、运行结果和剩余限制。",
    ),
    SprintBlock(
        "sprint:project:permission-check", 12, _PROJECT, "核对数据隔离、工具权限与日志", 165,
        "用本地虚构数据验证一条允许路径和一条应拦截路径，追溯最早决定点。检查用户错误信息与日志脱敏，记录当前保护与缺口。",
    ),
    SprintBlock(
        "sprint:project:failure-check", 12, _PROJECT, "补一个故障回归与用户提示", 160,
        "用同一触发条件跑变更前后，核对返回状态、可恢复性和记录。保留可复现样例，写一段用户能据此采取下一步的错误提示。",
    ),
    SprintBlock(
        "sprint:project:run-regressions", 13, _PROJECT, "跑项目回归并修正一个误判", 190,
        "按同一环境和固定样例比较项目基线与当前结果，核对失败、空结果和正常结果。确认一个误判原因，修好判据或实现后重跑。",
    ),
    SprintBlock(
        "sprint:project:evidence-review", 13, _PROJECT, "复核实验结果和限制", 175,
        "逐条核对样例分母、用量、来源和不能下结论的地方。写出一条有证据的改善、一条无变化和一条必须保留的局限。",
    ),
    SprintBlock(
        "sprint:project:cost-probe", 14, _PROJECT, "测量一次端到端运行的各阶段", 170,
        "固定版本、环境和输入，重复运行并保留成功与失败样本。记录可取到的阶段耗时、token 与依赖，标记缺测及估算方法。",
    ),
    SprintBlock(
        "sprint:project:tradeoff-lab", 14, _PROJECT, "检验一个成本或体验取舍", 155,
        "以同一组输入比较一项改动前后，记录用户可见指标和副作用。对比失败时保留观察，据此决定继续、回退或缩小适用范围。",
    ),
    SprintBlock(
        "sprint:project:contribution-evidence", 15, _PROJECT, "整理可核对的个人贡献", 155,
        "选择三条职责或结果陈述，逐条挂上真实记录、截图或代码位置。为每条写适用范围及个人动作，删掉无法归因或验证的数字。",
    ),
    SprintBlock(
        "sprint:project:demo-first-cut", 15, _PROJECT, "做项目演示的第一版脚本", 180,
        "录一遍 5 至 7 分钟演示，包含用户问题、实际操作、关键判断、一个故障或恢复和结果。回看两处卡顿并修改步骤或措辞。",
    ),
    SprintBlock(
        "sprint:project:reproducible-start", 16, _PROJECT, "用干净环境验证启动和演示", 190,
        "从冻结版本按当前说明启动隔离环境，跑两条主路径。记录版本、依赖、样例输入、退出码和依赖出错时的处理。",
    ),
    SprintBlock(
        "sprint:project:restore-plan", 16, _PROJECT, "写可执行的回滚与恢复步骤", 160,
        "给演示环境准备恢复步骤、验证数据和已知局限。用临时数据演练至少一个恢复动作，记录实际耗时与恢复后的主链路行为。",
    ),
    SprintBlock(
        "sprint:project:benchmark-compare", 17, _PROJECT, "重跑关键样例确认性能解释", 170,
        "用固定样例和同一记录方式对比两组运行。列出成功与失败样本、波动、清楚测到了什么，以及能下的结论。",
    ),
    SprintBlock(
        "sprint:project:failure-story", 17, _PROJECT, "把实测故障整理成简洁项目故事", 165,
        "选一条有前后证据的故障，写症状、最早证据、修复与剩余风险。口述两遍并标记每句事实对应的记录。",
    ),
    SprintBlock(
        "sprint:project:milestone-acceptance", 18, _PROJECT, "跑一轮项目版本验收", 205,
        "核对基线验收点、故障路径与评估样例。对每项写实际结果或阻塞，修复一个影响演示的问题并重跑相关检查。",
    ),
    SprintBlock(
        "sprint:project:one-page-evidence", 18, _PROJECT, "制作项目一页证据摘要", 185,
        "汇总主链路图、样例回放、度量口径、个人贡献和项目局限。给每个重要结论标可查材料，把未验证目标写成下一步。",
    ),
    SprintBlock(
        "sprint:project:rollback-rehearsal", 19, _PROJECT, "演练版本回滚并走通恢复后路径", 190,
        "使用隔离数据演练一次回滚或恢复。核对版本、两项关键数据和一次真实主链路，记录开始与完成时间、验证结果和停手条件。",
    ),
    SprintBlock(
        "sprint:project:decision-review", 19, _PROJECT, "复核设计证据和发布边界", 145,
        "逐条找设计决策的输入、结果和回退条件。选三处面试追问，对照证据修掉不准确表述，标记仍待补证的点。",
    ),
    SprintBlock(
        "sprint:project:untrusted-source-demo", 20, _PROJECT, "演练项目边界和降级路径", 180,
        "用虚构输入依次走正常、应拦截和工具不可用三条路径。核对用户反馈、日志以及权限检查，补一项可验证的演示前置条件。",
    ),
    SprintBlock(
        "sprint:project:review-architecture", 20, _PROJECT, "做一次项目架构自审", 210,
        "用白板从入口讲到结果，挑状态恢复、数据隔离和观测各一处核对实物。修正图文与当前实现的差异，链接到复现材料。",
    ),
    SprintBlock(
        "sprint:project:demo-candidate", 21, _PROJECT, "冻结候选演示版本并核对演示素材", 210,
        "选定可复现版本与样例，完整跑演示和一个故障回放。将运行步骤、版本、素材位置与脱敏检查结果整理到同一处。",
    ),
    SprintBlock(
        "sprint:project:resume-evidence", 21, _PROJECT, "把实测结果写成简历项目条目", 180,
        "写三条具体项目贡献，每条给动作、可查证据和适用条件。仅使用带统计口径和样本数的数字，准备一份自查用证据映射。",
    ),
    SprintBlock(
        "sprint:project:mock-interview", 22, _PROJECT, "做一场 45 分钟项目模拟面试", 195,
        "使用已经练过的题目和项目材料，演示一次主链路并限时追问。回放记录，按事实准确、结构、边界三个维度标出最大的三处失分。",
    ),
    SprintBlock(
        "sprint:project:mock-repair", 22, _PROJECT, "补齐模拟面试暴露的证据", 195,
        "从三处失分中挑影响最大的两处。每处执行一次针对性的项目验证，保存可查证据与修正后的陈述，再复跑相关演示步骤。",
    ),
    SprintBlock(
        "sprint:project:demo-regression", 23, _PROJECT, "回归演示主线和两条边界路径", 205,
        "从头走演示主线、异常输入和恢复操作。逐项核对用户结果、状态和日志，修最影响可复现性的一处并留前后记录。",
    ),
    SprintBlock(
        "sprint:project:handoff-review", 23, _PROJECT, "把启动和验收说明交给新读者自检", 215,
        "用干净目录按说明独立重跑，让步骤、样例输入、预期和失败信号都能抄走验证。删掉依赖个人记忆才能完成的隐含步骤。",
    ),
    SprintBlock(
        "sprint:project:clean-demo", 24, _PROJECT, "录一遍干净启动的项目演示", 210,
        "用 7 分钟展示主路径、一个限制和一次错误恢复。保存可复录脚本、素材和运行结果，核对画面没有敏感信息。",
    ),
    SprintBlock(
        "sprint:project:evidence-package", 24, _PROJECT, "核对简历、架构图和演示证据", 210,
        "逐条核对三项材料的项目范围、角色归因、版本与数据口径。固定最终映射表，让每条简历陈述都有可查依据。",
    ),
    SprintBlock(
        "sprint:application:role-shortlist", 25, _APPLICATION, "用实际目标岗位校对项目材料", 180,
        "从实际目标岗位摘出关键工作要求，对照当前证据标记可支持和待验证。改写两条项目描述，并保留原版与岗位适配版。",
    ),
    SprintBlock(
        "sprint:application:technical-mock", 25, _APPLICATION, "做一次岗位视角的技术模拟", 225,
        "从已练题目抽取项目取舍、数据边界、评估或故障。先演示后用证据回答，用回放找出两处会引发误解的表述并修正。",
    ),
    SprintBlock(
        "sprint:project:two-audience-demo", 26, _PROJECT, "演练两个版本的项目介绍", 210,
        "做一遍 90 秒项目概览和一遍 7 分钟工程演示。核对两版都包含目标、个人投入和有证据的结果，统一时间与指标口径。",
    ),
    SprintBlock(
        "sprint:project:reset-rehearsal", 26, _PROJECT, "练演示出错时的快速复位", 210,
        "在隔离环境演练一次依赖未启动、一次样例数据异常。记录重置到可演示状态的步骤和耗时，准备一份可查的离线运行证据。",
    ),
    SprintBlock(
        "sprint:application:resume-final-pass", 27, _APPLICATION, "收敛投递项目材料和追问证据", 200,
        "用两份实际岗位描述核对简历重点，保留两个适配版本。为每个项目亮点写一句口述与一个可查证据，清掉未经核实的荣誉或数据。",
    ),
    SprintBlock(
        "sprint:application:story-rehearsal", 27, _APPLICATION, "限时演练项目与故障叙述", 205,
        "用已练的项目题做两次限时演练。第一遍按项目故事，第二遍按故障时间线，分别核对个人贡献、证据和限制。",
    ),
    SprintBlock(
        "sprint:project:full-dress", 28, _PROJECT, "做最终版全流程演练", 260,
        "从干净状态用冻结版本做一次 7 分钟演示，覆盖主路径与限制。复现两个验收点，复查证据目录并修一处阻塞。记录演示耗时、回归结果和待缓解项。",
    ),
    SprintBlock(
        "sprint:application:claim-audit", 28, _APPLICATION, "逐条审查投递材料里的事实", 195,
        "检查简历、项目介绍和演示中的数字、归因和适用范围。对照实际样本，记录删除、缩窄或保留的依据，确认素材可直接打开。",
    ),
    SprintBlock(
        "sprint:application:january-handoff", 31, _APPLICATION, "整理 1 月投递与复测待办", 180,
        "定稿一份项目投递材料和一份可复核的演示素材。依据 12 月证据挑三项保持练习，记录目标岗位和第一次复盘要收集的信号。",
    ),
    SprintBlock(
        "sprint:application:january-week-one", 31, _APPLICATION, "做 1 月首轮岗位材料复核", 185,
        "更新实际岗位和项目证据的对应关系。任选已练过的两道高失分题复述并对照 12 月记录，更新材料优先级。",
    ),
    SprintBlock(
        "sprint:application:january-week-two", 36, _APPLICATION, "按本周真实反馈修正项目材料", 190,
        "汇总已收到的面试或投递信号。暂无外部反馈时回放一次岗位模拟。挑一个可核实的差距，补证据并更新相关口述。",
    ),
    SprintBlock(
        "sprint:application:january-week-three", 41, _APPLICATION, "重跑演示并复查高失分点", 185,
        "冻结一份可复跑的演示，复核一个此前薄弱的失败场景。对照旧记录说明改变、剩余风险与后续优先级。",
    ),
    SprintBlock(
        "sprint:application:january-week-four", 46, _APPLICATION, "复盘 1 月项目证据和投递材料", 185,
        "对比月初和现有材料，归档有实证的改动与反馈，列出还未验证的两个主要环节与下一步。",
    ),
)


def sprint_blocks() -> list[SprintBlock]:
    """Return a fresh, chronological list with stable persistent keys."""

    return sorted((*_QUESTIONS, *_PRACTICE), key=lambda block: block.day)
