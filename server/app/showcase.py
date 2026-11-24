"""Install Lin Zhou's fictional preparation history in a separate local workspace.

Run explicitly from an initialized server directory: python -m app.showcase
"""

import asyncio
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, engine
from app.models import ImportItem, ImportSession, JdSource, Skill, User, Workspace, WorkspaceMember
from app.services.benchmark_jd import parse_jd
from app.services.learning_loop import LOCAL_TZ
from app.showcase_study import seed_study
from app.showcase_training import seed_training

SLUG = "linzhou"
OWNER_EMAIL = "linzhou@careerpilot.invalid"

ROLES = (
    ("知识平台 · AI 应用后端工程师", "AI 应用后端工程师", 13,
     "维护面向客服的工单知识检索服务，负责 Python API、文档增量索引和权限过滤。\n"
     "建立按问题类型拆分的离线评测集，能区分召回遗漏、引用错误以及答案过期。\n"
     "需要三年以上后端经验，能够说明知识库变更后的灰度发布、监控指标和回滚条件。"),
    ("数据接入平台 · 后端研发工程师", "平台后端研发工程师", 8,
     "设计批量文件导入 API，管理从校验、入队、执行到对账的任务状态。\n"
     "熟悉 PostgreSQL、消息队列与幂等处理，能分析重复消费和超时重试造成的不一致。\n"
     "参与跨团队排障，基于队列积压、处理时延和失败样本说明容量瓶颈。"),
)

REVIEW = (
    ("知识库两天内更新了三次，怎样判断用户看到的答案来自哪个版本？", "rag",
     "在索引和答案引用上保留文档版本、段落位置及索引构建时间。先用问题回放检查旧版本是否仍在召回，再检查缓存键和引用指向的正文。",
     [("区分索引与缓存", "分别检查索引使用的文档修订号和缓存键包含的修订号。"),
      ("复现与核对", "对同一问题回放两次，定位到两个版本的具体段落。")]),
    ("导入任务重试后报表多出一批记录，你会按什么顺序排查？", "backend",
     "取重复记录的业务键，对照上传批次、队列消息和提交日志，找出第一次出现两份的环节。用稳定业务键约束最终写入，把确认消息安排在事务提交之后。",
     [("按业务键比对", "串联批次、消息和事务记录，指出第一次出现重复的位置。"),
      ("安全重试", "用唯一约束防止重复生效，数据库提交成功后再确认消息。")]),
)


async def install_workspace(session: AsyncSession, today: date) -> bool:
    """Create everything in the caller's transaction; repeat runs preserve user edits."""
    opened_at = datetime.combine(today - timedelta(days=30), time(9, 0), LOCAL_TZ)
    ws_id = await session.scalar(
        insert(Workspace)
        .values(slug=SLUG, name="林舟的求职笔记", target_role="AI 应用后端工程师",
                target_level="中级", is_demo=False, created_at=opened_at)
        .on_conflict_do_nothing(index_elements=[Workspace.slug])
        .returning(Workspace.id)
    )
    if ws_id is None:
        owner = await session.scalar(
            select(User.id).join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(Workspace.slug == SLUG, Workspace.is_demo.is_(False),
                   WorkspaceMember.role == "owner", User.email == OWNER_EMAIL)
        )
        if owner is None:
            raise ValueError(f"已有同名工作空间 {SLUG}，请保留其原有内容。")
        return False

    workspace = await session.get(Workspace, ws_id)
    owner = User(email=OWNER_EMAIL, display_name="林舟", created_at=opened_at)
    session.add(owner)
    await session.flush()
    session.add(WorkspaceMember(workspace_id=ws_id, user_id=owner.id, role="owner"))

    await seed_training(session, workspace, owner, today)
    await seed_study(session, workspace, owner, today)

    skills = (await session.execute(select(Skill.key, Skill.name))).mappings().all()
    for title, role, days_ago, description in ROLES:
        session.add(JdSource(
            workspace_id=ws_id, title=title, role=role, level="中级", source_type="paste",
            raw_text=description, parsed=parse_jd(description, skills), status="PARSED",
            created_at=datetime.combine(today - timedelta(days=days_ago), time(19, 20), LOCAL_TZ),
        ))
    filename = "技术面追问整理.md"
    imported = ImportSession(
        short_code=f"IM-LZ-{ws_id}", workspace_id=ws_id, filename=filename,
        paste_text="\n\n".join(f"{title}\n{answer}" for title, _, answer, _ in REVIEW),
        status=ImportSession.STATUS_REVIEW,
        created_at=datetime.combine(today - timedelta(days=1), time(20, 15), LOCAL_TZ),
    )
    session.add(imported)
    await session.flush()
    for title, skill, answer, points in REVIEW:
        session.add(ImportItem(
            session_id=imported.id, title=title, suggested_skill_key=skill,
            suggested_type="open", draft_answer=answer,
            draft_rubric={"points": [{"title": label, "detail": detail} for label, detail in points],
                          "source": "original"},
            generation_meta={"source_files": [filename]}, status=ImportItem.STATUS_DRAFT,
        ))
    return True


async def main() -> None:
    try:
        async with SessionLocal() as session, session.begin():
            created = await install_workspace(session, datetime.now(LOCAL_TZ).date())
        print(f"{SLUG}: {'created' if created else 'already installed'}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
