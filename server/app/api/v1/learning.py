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


def _today():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


@router.get("/learning")
async def learning_center(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    today = _today()
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

    main_tasks = [t for t in tasks if t.kind != "RETEST"]
    retests = [t for t in tasks if t.kind == "RETEST"]
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
        "completed": [_task_out(t, with_content=False) for t in done_total],
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
        "retest_of": t.retest_of,
        "source_exam_id": t.source_exam_id,
        "source_exam_question_id": t.source_exam_question_id,
        "source_evidence_id": t.source_evidence_id,
        "source_criterion": t.source_criterion,
        "gap_type": t.gap_type,
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
    from sqlalchemy import update

    # PostgreSQL rechecks this predicate after waiting for a concurrent completion.
    await session.execute(update(LearningTask).where(
        LearningTask.id == task_id, LearningTask.workspace_id == ws.id,
        LearningTask.status == "PENDING",
    ).values(status="IN_PROGRESS").execution_options(synchronize_session=False))
    status = await session.scalar(select(LearningTask.status).where(
        LearningTask.id == task_id, LearningTask.workspace_id == ws.id))
    if status is None:
        raise HTTPException(status_code=404, detail="task not found")
    await session.commit()
    return {"id": task_id, "status": status}


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


class CompleteTaskIn(BaseModel):
    artifact_text: str | None = None


@router.post("/learning/tasks/{task_id}/complete")
async def complete_task(
    task_id: int,
    body: CompleteTaskIn | None = None,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """Learning progress only. Only a scored exam may complete a retest."""
    await session.execute(select(LearningTask.id).where(
        LearningTask.id == task_id, LearningTask.workspace_id == ws.id).with_for_update())
    task = await _own_task(session, ws, task_id)
    if task.kind == "RETEST":
        raise HTTPException(status_code=409, detail="复测必须提交新的答卷并完成评分，不能通过完成按钮通过。")
    if task.status != "DONE":
        task.status = "DONE"
        task.completed_at = dt.datetime.now(dt.timezone.utc)
        if body and body.artifact_text is not None:
            task.content = {**(task.content or {}), "artifact_text": body.artifact_text[:20000], "artifact_verified": False}
        from app.services.learning_loop import schedule_retests
        await schedule_retests(session, task)
        await emit_event(session, ws.id, "learning.task_done", {"task_id": task.id, "kind": task.kind, "mastery_updated": False}, "learning_task", task.id)
    retests = (await session.execute(select(LearningTask).where(LearningTask.retest_of == task.id).order_by(LearningTask.due_date))).scalars().all()
    await session.commit()
    return {"id": task.id, "status": task.status, "mastery_updated": False,
            "retests": [_task_out(t, with_content=False) for t in retests]}


@router.post("/learning/tasks/{task_id}/retest")
async def start_retest(task_id: int, ws: Workspace = Depends(get_workspace),
                       session: AsyncSession = Depends(get_session)):
    """Open a new-question exam; completion never supplies its result."""
    from app.api.v1.exams import CreateExamReq, create_exam
    from app.services.learning_loop import retest_fields

    await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
    await session.execute(select(LearningTask.id).where(
        LearningTask.id == task_id, LearningTask.workspace_id == ws.id).with_for_update())
    task = await _own_task(session, ws, task_id)
    if task.kind == "RETEST":
        check = task
        parent = await _own_task(session, ws, task.retest_of) if task.retest_of else None
        if parent is None or parent.status != "DONE":
            raise HTTPException(status_code=409, detail="请先完成对应学习任务。")
        if task.due_date and task.due_date > _today():
            raise HTTPException(status_code=409, detail="延迟复测尚未到期；可从学习任务进行即时检查。")
        source_task = parent
    else:
        if task.status != "DONE":
            raise HTTPException(status_code=409, detail="请先记录学习完成，再提交新的复测答案。")
        source_task = task
        check = (await session.execute(select(LearningTask).where(
            LearningTask.workspace_id == ws.id, LearningTask.retest_of == task.id,
            LearningTask.kind == "RETEST", LearningTask.retest_round == 0,
        ))).scalar_one_or_none()
        if check is None:
            check = LearningTask(**retest_fields(task), title=f"即时检查：{task.title}",
                retest_round=0, due_date=_today(),
                content={"observation": (task.content or {}).get("observation", {}),
                         "verification_mode": "immediate"})
            session.add(check)
            await session.flush()
    from app.models.learning_feedback import LearningGoal
    goal = await session.scalar(select(LearningGoal).where(LearningGoal.workspace_id == ws.id,
        LearningGoal.source_task_id == source_task.id))
    if goal:
        from app.services.learning_feedback import assert_goal_sources
        await assert_goal_sources(session, ws, goal)
    if not check.skill_id:
        raise HTTPException(status_code=409, detail="该任务没有明确关联的能力域，暂时不能自动复测。")
    if check.status == "PENDING":
        check.status = "IN_PROGRESS"
    await session.flush()
    from app.models import Exam
    attempts = (await session.execute(select(Exam).where(
        Exam.workspace_id == ws.id, Exam.retest_task_id == check.id).order_by(Exam.id.desc()))).scalars().all()
    reusable = next((item for item in attempts if item.status not in ("ABORTED", "EXPIRED")), None)
    key = reusable.idempotency_key if reusable else f"retest-{check.id}-{len(attempts) + 1}"
    exam = await create_exam(CreateExamReq(kind="focused", question_count=1,
        retest_task_id=check.id, idempotency_key=key), ws=ws, session=session)
    return {"exam_id": exam["id"], "mode": (check.content or {}).get("verification_mode", "delayed")}


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
