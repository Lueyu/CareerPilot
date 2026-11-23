from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import LearningTask, Workspace
from app.models.learning_feedback import LearningGoal
from app.services.daily_learning_plan import (
    AGENT_INTERVIEW_SPRINT,
    DEFAULT_CAPACITY,
    DEFAULT_DEADLINE,
    MAX_DAYS,
    PREFERENCE_KEY,
    PlanItem,
    build_plan,
    next_workday,
    pace_baseline,
)
from app.services.task_memory import record_daily_plan_evidence

router = APIRouter(prefix='/learning/daily-plan', tags=['daily-learning-plan'])
LOCAL_TIME = timezone(timedelta(hours=8))


class PlanSettings(BaseModel):
    deadline: date
    daily_minutes: int = Field(ge=35, le=480)
    confirm_baseline: bool = False
    schedule_profile: Literal['agent_interview_sprint'] | None = None


class EvidenceIn(BaseModel):
    item_key: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=8, max_length=4000)


async def _workspace(session: AsyncSession, ws: Workspace) -> Workspace:
    return (await session.execute(select(Workspace).where(Workspace.id == ws.id)
            .with_for_update().execution_options(populate_existing=True))).scalar_one()


def _load(ws: Workspace, today: date) -> dict:
    saved = (ws.preferences or {}).get(PREFERENCE_KEY)
    return dict(saved) if saved else {
        'start_date': today.isoformat(), 'deadline': max(today, DEFAULT_DEADLINE).isoformat(),
        'daily_minutes': DEFAULT_CAPACITY,
    }


def _is_new_plan(ws: Workspace) -> bool:
    return PREFERENCE_KEY not in (ws.preferences or {})


async def _items(session: AsyncSession, workspace_id: int) -> list[PlanItem]:
    goals = (await session.scalars(select(LearningGoal).where(LearningGoal.workspace_id == workspace_id)
                                    .order_by(LearningGoal.id))).all()
    by_task = {goal.source_task_id: goal for goal in goals if goal.source_task_id is not None}
    tasks = (await session.scalars(select(LearningTask).where(LearningTask.workspace_id == workspace_id)
                                    .order_by(LearningTask.id))).all()
    items = []
    for task in tasks:
        goal = by_task.get(task.id)
        completed_on = task.completed_at.astimezone(LOCAL_TIME).date().isoformat() if (
            task.status == LearningTask.STATUS_DONE and task.completed_at) else None
        if not completed_on and (task.status == LearningTask.STATUS_DONE or
                                goal and goal.status in {'PAUSED', 'WITHDRAWN', 'NEEDS_REVIEW'}):
            continue
        items.append(PlanItem(
            f'task:{task.id}', task.title, '当前学习目标' if task.kind != 'RETEST' else '延迟复测',
            'retest' if task.kind == 'RETEST' else 'goal' if goal else 'task', task.est_minutes or 25,
            task.acceptance or (goal.target_behavior if goal else '完成任务中的要求，并在任务页保存结果。'),
            task_id=task.id, goal_id=goal.id if goal else None,
            due=task.due_date.isoformat() if task.due_date else None, completed_on=completed_on,
            verification=goal.verification if goal else None,
        ))
    for goal in goals:
        if goal.source_task_id is None and goal.status == 'ACTIVE':
            items.append(PlanItem(f'goal:{goal.id}', goal.target_behavior, '当前学习目标', 'goal', 25,
                                  goal.target_behavior, goal_id=goal.id, verification=goal.verification,
                                  study_session_id=(goal.origin_refs or {}).get('study_session_id')))
    return items


async def _render(session: AsyncSession, ws: Workspace, today: date, state: dict, *, reschedule=False,
                  reset_baseline=False):
    items = await _items(session, ws.id)
    capture_baseline = reset_baseline or (_is_new_plan(ws) and pace_baseline(state) is None)
    plan = build_plan(state, today, items,
                      reschedule=reschedule or capture_baseline or state.get('planned_on') != today.isoformat(),
                      capture_baseline=capture_baseline)
    assignments = plan.pop('assignments')
    baseline = plan.pop('pace_baseline')
    next_state = {**state, 'assignments': assignments, 'planned_on': today.isoformat()}
    if baseline is not None:
        next_state['pace_baseline'] = baseline
    if next_state != (ws.preferences or {}).get(PREFERENCE_KEY):
        ws.preferences = {**(ws.preferences or {}), PREFERENCE_KEY: next_state}
    await session.commit()
    return plan


@router.get('')
async def get_plan(ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    today = datetime.now(LOCAL_TIME).date()
    ws = await _workspace(session, ws)
    return await _render(session, ws, today, _load(ws, today))


@router.put('')
async def update_plan(body: PlanSettings, ws: Workspace = Depends(get_workspace),
                      session: AsyncSession = Depends(get_session)):
    today = datetime.now(LOCAL_TIME).date()
    ws = await _workspace(session, ws)
    state = _load(ws, today)
    deadline = body.deadline.isoformat()
    changed = state['deadline'] != deadline or state['daily_minutes'] != body.daily_minutes
    if 'schedule_profile' in body.model_fields_set and body.schedule_profile != state.get('schedule_profile'):
        changed = True
        state['schedule_profile'] = body.schedule_profile
        if body.schedule_profile == AGENT_INTERVIEW_SPRINT:
            state['sprint_start_date'] = next_workday(today).isoformat()
    reset_baseline = changed or (body.confirm_baseline and pace_baseline(state) is None)
    if reset_baseline and not today <= body.deadline <= today + timedelta(days=MAX_DAYS):
        raise HTTPException(422, '目标日期须在今天至未来 180 天内。')
    state.update(deadline=deadline, daily_minutes=body.daily_minutes)
    return await _render(session, ws, today, state, reset_baseline=reset_baseline)


@router.post('/reschedule')
async def reschedule(ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    today = datetime.now(LOCAL_TIME).date()
    ws = await _workspace(session, ws)
    return await _render(session, ws, today, _load(ws, today), reschedule=True)


@router.post('/evidence')
async def record_evidence(body: EvidenceIn, ws: Workspace = Depends(get_workspace),
                          session: AsyncSession = Depends(get_session)):
    now = datetime.now(LOCAL_TIME)
    today = now.date()
    ws = await _workspace(session, ws)
    state = _load(ws, today)
    roll_sprint = (state.get('schedule_profile') == AGENT_INTERVIEW_SPRINT
                   and state.get('planned_on') != today.isoformat())
    plan = build_plan(state, today, await _items(session, ws.id), reschedule=roll_sprint,
                      capture_baseline=_is_new_plan(ws))
    eligible = next((item for day in plan['days'] if day['date'] <= today.isoformat()
                     for item in day['items'] if item['key'] == body.item_key and
                     item['task_id'] is None and item['status'] != 'completed'), None)
    note = body.note.strip()
    if (state.get('evidence') or {}).get(body.item_key, {}).get('note') == note:
        return await _render(session, ws, today, state)
    if eligible and not eligible.get('prerequisite_complete', True):
        raise HTTPException(422, '请先提交上一轮模拟面试的完成依据。')
    if not eligible or len(note) < 8:
        raise HTTPException(422, '请为今日或逾期的开放练习提交具体完成依据（至少 8 个字符）。')
    state['evidence'] = {**(state.get('evidence') or {}), body.item_key: {'date': today.isoformat(), 'note': note}}
    state['assignments'] = plan['assignments']
    if plan['pace_baseline'] is not None:
        state['pace_baseline'] = plan['pace_baseline']
    await record_daily_plan_evidence(session, workspace_id=ws.id, item_key=body.item_key,
                                     title=eligible['title'], completed_at=now, note=note)
    return await _render(session, ws, today, state)
