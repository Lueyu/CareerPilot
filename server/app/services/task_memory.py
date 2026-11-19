from datetime import datetime

from app.models import MemoryItem
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert


TASK_COMPLETION_TYPE = "TaskCompletion"


async def record_task_completion(session, task):
    await session.execute(insert(MemoryItem).values(
        workspace_id=task.workspace_id,
        mtype=TASK_COMPLETION_TYPE,
        content=f"已记录学习任务完成：{task.title}",
        source="SYSTEM",
        source_ref=f"learning_task:{task.id}",
        status=MemoryItem.STATUS_CANDIDATE,
        meta={
            "source_task_id": task.id,
            "completed_at": task.completed_at.isoformat(),
            "task_kind": task.kind,
            **({"exam_id": task.content["retest_result"]["exam_id"]} if task.kind == "RETEST" else {}),
        },
    ).on_conflict_do_nothing())


async def record_daily_plan_evidence(
    session: AsyncSession, *, workspace_id: int, item_key: str, title: str, note: str, completed_at: datetime,
) -> None:
    await session.execute(insert(MemoryItem).values(
        workspace_id=workspace_id,
        mtype=TASK_COMPLETION_TYPE,
        content=f"自记学习进度：{title}\n{note}",
        source="SYSTEM",
        source_ref=f"daily_plan:{item_key}",
        status=MemoryItem.STATUS_CANDIDATE,
        meta={
            "daily_plan_date": completed_at.date().isoformat(),
            "daily_plan_item_key": item_key,
            "evidence_kind": "self_report",
            "completed_at": completed_at.isoformat(),
        },
    ).on_conflict_do_nothing())
