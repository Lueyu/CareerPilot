"""学习中心 API: 今日任务 / 任务详情 / 完成 / 复测链。"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import LearningTask, Workspace
from app.services.planner import plan_tasks_for_workspace
from app.services.tracing import emit_event

router = APIRouter()


@router.get("/learning")
async def learning_center(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    today = dt.date.today()
    tasks = (
        await session.execute(
            select(LearningTask)
            .where(LearningTask.workspace_id == ws.id, LearningTask.status != "DONE")
            .order_by(LearningTask.retest_round, LearningTask.id)
        )
    ).scalars().all()
    if not tasks:
        tasks = await plan_tasks_for_workspace(session, ws.id)
        await session.commit()

    main_tasks = [t for t in tasks if t.retest_round == 0]
    retests = [t for t in tasks if t.retest_round > 0]
    done_total = (
        await session.execute(
            select(LearningTask).where(LearningTask.workspace_id == ws.id, LearningTask.status == "DONE")
        )
    ).scalars().all()

    return {
        "today": [_task_out(t, with_content=False) for t in main_tasks[:3]],
        "retest_plan": [
            {
                "id": t.id,
                "title": t.title,
                "round": t.retest_round,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "due_in_days": (t.due_date - today).days if t.due_date else None,
                "parent_id": t.retest_of,
                "status": t.status,
            }
            for t in retests
        ],
        "stats": {"done_total": len(done_total), "pending": len(tasks)},
    }


def _task_out(t: LearningTask, with_content: bool = True) -> dict:
    out = {
        "id": t.id,
        "title": t.title,
        "kind": t.kind,
        "skill": t.skill.name if t.skill else None,
        "skill_key": t.skill.key if t.skill else None,
        "why": t.why,
        "est_minutes": t.est_minutes,
        "acceptance": t.acceptance,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "status": t.status,
        "retest_round": t.retest_round,
    }
    if with_content:
        out["content"] = t.content
    return out


@router.get("/learning/tasks/{task_id}")
async def task_detail(
    task_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    task = (
        await session.execute(
            select(LearningTask).where(LearningTask.id == task_id, LearningTask.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_out(task)


@router.post("/learning/tasks/{task_id}/start")
async def start_task(
    task_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    task = (
        await session.execute(
            select(LearningTask).where(LearningTask.id == task_id, LearningTask.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status == "PENDING":
        task.status = "IN_PROGRESS"
        await session.commit()
    return {"id": task.id, "status": task.status}


@router.post("/learning/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """完成任务; 复测任务完成时写一条低置信证据(表示该域刚被复测, 待考试验证)。"""
    task = (
        await session.execute(
            select(LearningTask).where(LearningTask.id == task_id, LearningTask.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    task.status = "DONE"
    task.completed_at = dt.datetime.now(dt.timezone.utc)
    await emit_event(session, ws.id, "learning.task_done", {"task_id": task.id, "kind": task.kind}, "learning_task", task.id)
    await session.commit()
    return {"id": task.id, "status": task.status}
