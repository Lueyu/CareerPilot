"""Personal showcase study records. The caller owns the transaction and seed guard."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import MemoryItem
from app.models.project import Project
from app.models.study_chat import StudyMessage, StudySession
from app.models.study_notes import StudyCard, StudyNote, StudyNoteVersion
from app.models.workspace import User, Workspace


async def seed_study(session: AsyncSession, workspace: Workspace, user: User, today: date) -> None:
    """Add 林舟's study history to the caller's persisted workspace."""
    local_zone = timezone(timedelta(hours=8))
    for entry in PROJECT_STUDIES:
        started = datetime.combine(today - timedelta(days=entry["days_ago"]), time(20, 15), local_zone)
        finished = started + timedelta(minutes=24)
        project = Project(
            workspace_id=workspace.id,
            name=entry["name"],
            repo_url="",
            owner="",
            repo="",
            branch="",
            status="NEVER",
            meta={
                "kind": "manual",
                "description": entry["description"],
                "profile": entry["profile"],
                "profile_version": 1,
            },
            created_at=started - timedelta(days=12),
        )
        session.add(project)
        await session.flush()

        chat = StudySession(
            workspace_id=workspace.id,
            title=entry["topic"],
            status="active",
            context={
                "source_type": "free",
                "source_id": None,
                "topic": entry["topic"],
                "skill_ids": [],
                "target_role": workspace.target_role,
                "sources": [],
                "mode": "auto",
                "project_id": project.id,
            },
            created_at=started,
            updated_at=finished,
        )
        session.add(chat)
        await session.flush()

        sources = []
        message_ids = []
        parent_id = None
        for index, (prompt, reply, quote) in enumerate(entry["turns"]):
            request_id = uuid4().hex
            turn_start = started + timedelta(minutes=8 * index)
            for role, content, created_at in (
                ("user", prompt, turn_start),
                ("assistant", reply, turn_start + timedelta(minutes=1)),
            ):
                message = StudyMessage(
                    session_id=chat.id,
                    request_id=request_id,
                    role=role,
                    project_id=project.id,
                    content=content,
                    status="completed",
                    action="explain",
                    coach={"mode": "auto", "parent_message_id": parent_id},
                    sources=[],
                    created_at=created_at,
                )
                session.add(message)
                await session.flush()
                parent_id = message.id
                message_ids.append(message.id)
            sources.append({
                "session_id": chat.id,
                "message_id": message.id,
                "role": "assistant",
                "quote": quote,
                "materials": [],
            })

        note = StudyNote(
            workspace_id=workspace.id,
            origin_session_id=chat.id,
            title=entry["note_title"],
            updated_at=finished,
        )
        session.add(note)
        await session.flush()
        version = StudyNoteVersion(
            note_id=note.id,
            title=note.title,
            body=entry["note"],
            author="USER",
            sources=sources,
            session_ids=[chat.id],
            coverage=message_ids,
            cards_generated=True,
            created_at=finished,
        )
        session.add(version)
        await session.flush()
        note.current_version_id = version.id
        for knowledge_point, question, answer in entry["cards"]:
            session.add(StudyCard(
                workspace_id=workspace.id,
                note_id=note.id,
                version_id=version.id,
                question=question,
                answer=answer,
                knowledge_point=knowledge_point,
                status="CANDIDATE",
            ))

        session.add(MemoryItem(
            workspace_id=workspace.id,
            mtype="ProjectDecision",
            content=entry["decision"],
            source="USER",
            source_ref=f"study-note:{note.id}",
            status=MemoryItem.STATUS_ACTIVE,
            meta={"project_id": project.id, "scope": "user_statement", "note_id": note.id},
            created_at=finished,
            confirmed_at=finished,
        ))

    goal_recorded_at = datetime.combine(today - timedelta(days=4), time(20, 0), local_zone)
    session.add(MemoryItem(
        workspace_id=workspace.id,
        mtype="CareerGoal",
        content="三年后端开发经验，目标岗位为 AI 应用后端工程师。用工单知识助手讲清检索、权限和评估，用异步导入服务讲清事务、重试和恢复。每个项目准备一段两分钟概述和一个能复盘的故障场景。",
        source="USER",
        status=MemoryItem.STATUS_ACTIVE,
        created_at=goal_recorded_at,
        confirmed_at=goal_recorded_at,
    ))
    await session.flush()


PROJECT_STUDIES = (
    {
        "name": "工单知识助手",
        "days_ago": 1,
        "description": "面向一线客服的知识检索与答案草稿服务，引用可追溯到原文和文档版本。",
        "profile": {
            "background": "客服处理退款、账号权限等问题时，需要在历史工单和业务手册之间切换。旧手册和生效规则同时存在时，关键词命中也可能把人带到过期条款。",
            "role": "后端开发，负责知识入库和问答接口，与客服同学一起整理回归问题。",
            "responsibilities": "实现文档版本化、入库重试、租户内检索和引用回查。维护一组包含歧义问法、无答案、过期条款及跨租户同名文档的回归用例。",
            "stack": "Python、FastAPI、PostgreSQL、pgvector。文档按章节切段，切段保留标题路径和原文位置。",
            "decisions": "先按租户、可见范围和生效版本收窄片段，再合并全文与向量召回结果。生成前只传已授权片段，输出后检查引用是否指向本次上下文。证据不足时保留相关入口并交给客服判断。",
            "challenges": "退款流程在两个产品线命名相同。只看召回分数会选错条款，需要把产品线、适用对象和生效时间一起带入判断。引用真实存在也无法保证引用内容支持回答里的每个条件。",
            "outcomes": "跑通从文档入库、检索到引用回查的链路。回归时能区分漏召回、召回了过期条款和回答引用不支持原句。面试可演示产品线重名与过期条款两个案例。",
            "boundaries": "我负责应用后端和回归样例，规则内容由客服团队维护。当前能讲清回归现象和验证方法，业务节省时长仍待统计。",
        },
        "topic": "工单知识助手：权限边界与引用核验",
        "turns": (
            (
                "我有三年后端经验，准备投 AI 应用后端。介绍工单知识助手时，我总从 pgvector 开始讲，面试官追问权限时答案就散了。怎么按请求流程讲？",
                "从客服问退款规则说起。服务端用会话身份确定租户和可见范围，在检索查询里同时限制租户、可见范围和生效版本。候选片段连同文档 ID、小节和版本交给后续流程。未授权片段不能进入候选集、重排上下文或答案草稿。举一个两个租户都有《退款手册》的用例，说明你会查中间候选 ID，也会查最终引用。",
                "未授权片段不能进入候选集、重排上下文或答案草稿。",
            ),
            (
                "客服可以看到引用，怎么判断回答真的被原文支持？比如手册写的是签收后七天，我的草稿只说七天内可退款。",
                "把引用检查拆成两层。第一层确认引用片段属于本次已授权上下文，且文档版本和小节可回查。第二层检查主张是否保留适用条件，例如起算点、适用商品和例外。签收后七天的起算点丢失，引用真实也应判主张不通过。回归记录同时保留问题、期望条件和引用片段，排查时可以看到条件在哪一步丢失。",
                "签收后七天的起算点丢失，引用真实也应判主张不通过。",
            ),
            (
                "检索为空、条款冲突、有人直接问另一条产品线，这三种情况怎么区分？我想留一个下次复练能直接用的检查清单。",
                "检索为空时先看授权过滤和召回日志，再确认知识库是否缺材料。条款冲突时核对适用产品和生效时间，仍不能确定就说明冲突并转人工。产品线缺失时只问一个最有区分度的问题，例如订单属于哪个产品。回归分别断言候选范围、条款选择和用户看到的下一步。只返回相关入口时，入口也要经过权限检查。",
                "回归分别断言候选范围、条款选择和用户看到的下一步。",
            ),
        ),
        "note_title": "RAG 面试复盘：权限过滤、有效版本与引用",
        "note": """## 两分钟讲法
客服要快速查到当前生效条款。我负责知识入库和问答接口，把请求身份带到召回查询，先限定租户、可见范围和生效版本，再把候选片段交给后续流程。候选保留原文位置，方便确认草稿引用支持哪条主张。

## 复练时检查四个例子
- 两个租户都有《退款手册》。查候选 ID 和最终引用，确保都在授权范围内。
- 新旧版本都命中同一个问法。先核对生效日期，再检查是否错误引用历史条款。
- 草稿写“七天内可退款”。回查原文，确认是否漏了“签收后”、适用范围或例外。
- 产品线缺失，或者授权范围内没有依据。分别练习只追问一个关键信息和给出可访问的人工处理入口。

## 下次自测
反过来从一条错误草稿定位问题。先验证授权候选，再查是否召回正确片段，最后对照草稿的每个条件。项目介绍里提到的版本切换也可以接到《异步导入服务》的失败恢复讨论。
""",
        "decision": "工单知识助手在候选召回阶段按租户、可见范围和生效版本过滤。引用核验先验证来源与版本，再核对主张是否保留条款的适用条件。无法确认时保留可访问的处理入口。",
        "cards": (
            ("召回权限", "两个租户的同名手册都命中退款关键词。要在哪一层拦截？验收时查什么？",
             "在召回查询中按服务端确认的租户、可见范围及生效版本过滤。检查中间候选、重排输入和最终引用都没有越界片段。"),
            ("引用核验", "草稿说七天内可退款，引用写签收后七天。引用真实，为什么草稿仍不通过？",
             "草稿漏了七天的起算点。先验证引用属于本次授权上下文且可回查，再逐条核对主张的条件、适用对象和例外。"),
        ),
    },
    {
        "name": "异步导入服务",
        "days_ago": 3,
        "description": "把大批量表格处理从同步请求迁出，支持按批恢复、逐行错误定位和结果追踪。",
        "profile": {
            "background": "运营在页面导入表格，旧接口要等校验和写入结束才应答。浏览器超时后用户容易重复提交，后来也难确认哪些行已经落库。",
            "role": "后端开发，负责导入 API、任务状态流转和批次写入。",
            "responsibilities": "定义任务与行结果表，编写流式解析、批处理和恢复逻辑。和页面同学约定进度、失败原因和下载错误清单的接口。",
            "stack": "FastAPI、PostgreSQL、Redis Streams。数据库保存任务和行结果，队列传递待处理任务 ID。",
            "decisions": "提交按工作区与客户端请求键去重，并保存输入指纹；同键不同输入提示冲突。任务与待投递事件同事务写入，投递允许重试。批次业务写入、行结果和进度在同一数据库事务内提交，确认队列消息发生在提交之后。",
            "challenges": "首个故障复盘场景是批次已提交但 worker 在消息确认前退出。恢复时依据已持久化的批次进度续跑，并用任务与行号的唯一键约束重复结果。多个 worker 争用时通过行锁确认任务归属，写入前校验该次领取的版本。",
            "outcomes": "页面可以在请求返回后继续查看任务进度，校验失败的行有可下载清单。恢复用例覆盖重复提交、提交后确认前退出，以及中途断开后的接续处理。",
            "boundaries": "我实现服务端与恢复测试。未验证跨系统写入的原子性；向外部系统同步时需另行约定幂等键和对账方式。",
        },
        "topic": "异步导入服务：提交成功后 worker 退出怎么办",
        "turns": (
            (
                "上次说我做了幂等，面试官问到底是哪个东西幂等。我把表格内容做哈希是不是就够了？",
                "先说清两个边界。提交去重用工作区和客户端请求键，输入指纹用于发现同键异参。新请求即使使用同一文件，也可能是用户有意再导一次。处理去重用任务 ID 和行号，它保证同一任务的行结果最多落一次。若业务要求不同任务也不能重复创建实体，需要单独定义业务唯一键和更新规则。",
                "输入指纹用于发现同键异参。",
            ),
            (
                "worker 写完一批后退出，Redis Streams 那条消息还没确认。重试时是从头跑还是读进度？如果接手的 worker 与原 worker 重叠怎么办？",
                "先看数据库中最后一个已提交批次。业务行、行结果和 checkpoint 同事务提交，重投时从 checkpoint 继续。领取任务时在数据库锁住任务行并递增领取版本，提交批次时重新核对版本。旧 worker 即使还活着，持有旧版本也无法提交新批次。仍需让行结果有唯一约束，并在已完成任务上直接确认重投消息。",
                "业务行、行结果和 checkpoint 同事务提交，重投时从 checkpoint 继续。",
            ),
            (
                "工单知识助手的文档入库也能直接套这套吗？我担心新片段还没写完，检索已经读到了新版本。",
                "可以复用任务去重和批次恢复。知识库还需要发布边界：新片段写到待生效版本，等索引和引用定位都通过检查，再在数据库事务里切换当前版本。读取端只查当前版本。中途退出时让旧版本继续对外，写完再投递时仍以版本状态判断是否需要发布。",
                "读取端只查当前版本。",
            ),
        ),
        "note_title": "异步任务面试复盘：三处崩溃窗口",
        "note": """## 按时间线回答
1. **任务已写入、消息未投递**。任务和待投递事件同事务；投递器补发后，消费者按数据库状态处理。
2. **批次写入中退出**。业务行、行结果和 checkpoint 在同一事务内回滚；下一次从已提交边界开始。
3. **批次提交、队列未确认**。重投回读数据库 checkpoint，校验领取版本后继续。任务已经结束时可以直接确认消息。

## 反问自己
- 同工作区同请求键传了另一份表，应该返回冲突。相同文件使用新请求键，是否重复写实体由业务唯一键决定。
- 旧 worker 在租约过期后恢复，批次提交必须检查领取版本。验收时同时检查业务行数、逐行结果数，以及最终任务状态。
- 如果下一步会写外部系统，数据库事务覆盖不到那个系统。需要给远端传稳定幂等键，并保留对账与补偿入口。

## 迁移到工单知识助手
沿用可恢复的入库任务。新版本先写片段，再验证可检索和引用定位，最后切换生效版本。用“写到一半时查询”的测试证明用户仍能检索旧版条款。
""",
        "decision": "异步导入服务拆开提交去重、业务唯一键和批次恢复。任务与待投递事件同事务，业务写入和 checkpoint 同事务，确认消息在事务提交之后。文档入库复用恢复机制，并单独设置版本发布边界。",
        "cards": (
            ("批次恢复", "一个批次已提交，消息未确认，此时重投凭什么不会重复写入？",
             "业务写入、行结果和 checkpoint 同事务。接手者从数据库里的已提交边界续跑，检查领取版本，并由任务与行号唯一约束保护行结果。"),
            ("文档版本切换", "借用导入服务做知识入库。如何确保用户看不到只写了一半的新版本？",
             "把片段写进待生效版本，验证索引和引用定位，再在事务内切换当前版本。读取端始终按当前版本查询，入库中断时继续读旧版。"),
        ),
    },
)
