"""学习中心 API: 今日任务 / 任务详情 / 完成 / 复测链 / 教学内容 / 求助。"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import LearningTask, Workspace
from app.services.llm import AgentAdapter
from app.services.planner import plan_tasks_for_workspace
from app.services.teaching import HELP_MODES, build_teaching_content, help_answer
from app.services.tracing import emit_event

router = APIRouter()
_llm = AgentAdapter()


async def _own_task(session: AsyncSession, ws: Workspace, task_id: int) -> LearningTask:
    task = (
        await session.execute(
            select(LearningTask).where(LearningTask.id == task_id, LearningTask.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


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
    task = await _own_task(session, ws, task_id)
    if task.status == "PENDING":
        task.status = "IN_PROGRESS"
        await session.commit()
    return {"id": task.id, "status": task.status}


@router.get("/learning/tasks/{task_id}/content")
async def task_content(
    task_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """教学任务内容(7 步通俗教学): 生成一次, 缓存进 task.content; LLM 未配置走规则路径。"""
    task = await _own_task(session, ws, task_id)
    skill_name = task.skill.name if task.skill else "目标技能"
    skill_key = task.skill.key if task.skill else None
    content = await build_teaching_content(
        session, _llm, task=task, skill_name=skill_name, skill_key=skill_key
    )
    await session.commit()
    return {"id": task.id, "content": content, "llm_active": _llm.active}


class HelpIn(BaseModel):
    mode: str


@router.post("/learning/tasks/{task_id}/help")
async def task_help(
    task_id: int,
    body: HelpIn,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """学习求助(modes: simpler/analogy/example/project/code/diagram/unclear); 结果缓存进 content.help_cache。"""
    if body.mode not in HELP_MODES:
        raise HTTPException(status_code=422, detail=f"未知求助类型: {body.mode}")
    task = await _own_task(session, ws, task_id)
    skill_name = task.skill.name if task.skill else "目标技能"
    skill_key = task.skill.key if task.skill else None
    out = await help_answer(session, _llm, task=task, skill_name=skill_name, skill_key=skill_key, mode=body.mode)
    await session.commit()
    return out


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


# ---------------- 手动创建任务 / 安排复测 ----------------

class TaskCreate(BaseModel):
    title: str
    skill_key: str | None = None
    kind: str = "UNDERSTAND"  # UNDERSTAND | PRACTICE | RETEST
    est_minutes: int = 30
    due_in_days: int | None = None  # 安排复测: N 天后到期
    retest_of_task_id: int | None = None


@router.post("/learning/tasks")
async def create_task(
    body: TaskCreate,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    from app.models import Skill

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="任务标题不能为空")
    if body.kind not in ("UNDERSTAND", "PRACTICE", "RETEST"):
        raise HTTPException(status_code=422, detail="kind 必须是 UNDERSTAND / PRACTICE / RETEST")
    if body.kind == "RETEST" and body.retest_of_task_id is None:
        raise HTTPException(status_code=422, detail="复测任务需要指定 retest_of_task_id（被复测的任务）")

    skill_id: int | None = None
    skill_name = ""
    if body.skill_key:
        sk = (await session.execute(select(Skill).where(Skill.key == body.skill_key))).scalar_one_or_none()
        if sk is None:
            raise HTTPException(status_code=404, detail=f"能力域 '{body.skill_key}' 不存在")
        skill_id = sk.id
        skill_name = sk.name

    retest_of = None
    retest_round = 0
    if body.retest_of_task_id is not None:
        base = await _own_task(session, ws, body.retest_of_task_id)
        retest_of = base.id
        retest_round = (base.retest_round or 0) + 1

    due_date = None
    if body.due_in_days is not None:
        due_date = dt.date.today() + dt.timedelta(days=max(0, body.due_in_days))  # date 对象(asyncpg Date 列不接受 str)

    task = LearningTask(
        workspace_id=ws.id,
        skill_id=skill_id,
        title=title,
        kind=body.kind,
        why="手动创建" if body.kind != "RETEST" else f"复测: {skill_name or '该能力域'} 第 {retest_round} 轮",
        est_minutes=max(5, min(240, body.est_minutes)),
        acceptance="按任务说明完成并提交结果",
        due_date=due_date,
        retest_of=retest_of,
        retest_round=retest_round,
        status=LearningTask.STATUS_PENDING,
    )
    session.add(task)
    await session.commit()
    await emit_event(session, ws.id, "learning.task_created", {"task_id": task.id, "kind": task.kind, "manual": True}, "learning_task", task.id)
    await session.commit()
    return {"id": task.id, "title": task.title, "kind": task.kind, "due_date": task.due_date, "retest_round": task.retest_round, "status": task.status}
