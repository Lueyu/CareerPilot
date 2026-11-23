"""User review and bounded local planning; neither endpoint grants mastery."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.deps import get_workspace
from app.db import get_session
from app.models import MemoryItem, Workspace
from app.models.learning_feedback import LearningActivity, LearningGoal
from app.services.learning_memory import LEARNING_TYPES, invalidate_memory_dependents
from app.services.project_memory import lock_project_memories
from app.services.tracing import emit_event

router = APIRouter()


async def own_goal(session, ws, goal_id):
    if await session.scalar(select(Workspace.id).where(Workspace.id == ws.id).with_for_update()) is None:
        raise HTTPException(404, '工作空间不存在')
    goal = await session.scalar(select(LearningGoal).where(LearningGoal.workspace_id == ws.id,
        LearningGoal.id == goal_id).with_for_update().execution_options(populate_existing=True))
    if goal is None:
        raise HTTPException(404, '学习目标不存在')
    return goal


class MemoryReviewItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: int
    expected_revision: int
    action: Literal['confirm','ignore']
    edited_content: str | None = Field(default=None, max_length=10000)


class MemoryReviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[MemoryReviewItem] = Field(max_length=50)


@router.post('/learning/goals/{goal_id}/memory-review')
async def review_memories(goal_id: int, body: MemoryReviewIn, ws=Depends(get_workspace), session=Depends(get_session)):
    # Match the existing memory lock order before touching goal rows.
    await lock_project_memories(session, ws.id)
    goal = await own_goal(session, ws, goal_id)
    results = []
    for item in body.items:
        row = await session.scalar(select(MemoryItem).where(MemoryItem.workspace_id == ws.id,
            MemoryItem.id == item.candidate_id, MemoryItem.mtype.in_(LEARNING_TYPES),
            MemoryItem.meta['goal_id'].as_integer() == goal.id).with_for_update().execution_options(populate_existing=True))
        if row is None:
            results.append({'id':item.candidate_id,'status':'NOT_FOUND'}); continue
        if (row.revision != item.expected_revision or row.status != 'CANDIDATE'
                or (item.action == 'confirm' and (row.freshness != 'CURRENT' or goal.status in ('NEEDS_REVIEW','WITHDRAWN')))):
            results.append({'id':row.id,'status':'CONFLICT','revision':row.revision,'error':'记忆版本或来源已改变，请重新核对'}); continue
        if item.edited_content is not None:
            if not item.edited_content.strip():
                results.append({'id':row.id,'status':'INVALID','error':'描述不能为空'}); continue
            row.meta = {**(row.meta or {}), 'proposed_content':row.content}
            row.content = item.edited_content.strip(); row.source = 'USER'
        row.revision += 1
        if item.action == 'confirm':
            row.status = 'ACTIVE'; row.confirmed_at = datetime.now(timezone.utc)
        else:
            row.status = 'DISMISSED'
            await invalidate_memory_dependents(session, ws.id, row.id, '用户忽略候选记忆')
        await emit_event(session, ws.id, 'learning.memory_review', {'memory_id':row.id,'goal_id':goal.id,
            'action':item.action,'revision':row.revision,'mastery_updated':False}, 'memory', row.id)
        results.append({'id':row.id,'status':'CONFIRMED' if item.action=='confirm' else 'IGNORED','revision':row.revision})
    await session.commit()
    return {'results':results}


class AdjustIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['shorten','pause','resume','skip']
    reason: str = Field(min_length=1, max_length=2000)
    expected_plan_revision: int
    activity_id: int | None = None


@router.post('/learning/goals/{goal_id}/adjust')
async def adjust_goal(goal_id: int, body: AdjustIn, ws=Depends(get_workspace), session=Depends(get_session)):
    goal = await own_goal(session, ws, goal_id)
    if goal.plan_revision != body.expected_plan_revision:
        raise HTTPException(409, '计划已更新，请查看最新反馈后再调整')
    if not body.reason.strip():
        raise HTTPException(422, '请记录简短调整原因')
    if goal.status in ('NEEDS_REVIEW','WITHDRAWN'):
        raise HTTPException(409, '来源需要复核，不能用恢复计划绕过')
    activities = (await session.scalars(select(LearningActivity).where(LearningActivity.goal_id == goal.id,
        LearningActivity.workspace_id == ws.id).with_for_update().order_by(LearningActivity.position,LearningActivity.id))).all()
    changes = []
    if body.action == 'shorten':
        ready = [row for row in activities if row.status == 'READY' and row.latest_submission_id is None]
        for row in ready[1:]:
            changes.append({'activity_id':row.id,'from':row.status,'to':'DEFERRED'}); row.status = 'DEFERRED'
    elif body.action == 'pause':
        changes.append({'from':goal.status,'to':'PAUSED'}); goal.status = 'PAUSED'
    elif body.action == 'resume':
        if body.activity_id:
            row = next((r for r in activities if r.id == body.activity_id), None)
            if row is None:
                raise HTTPException(404, '活动不存在')
            if row.status not in ('DEFERRED','SKIPPED'):
                raise HTTPException(409, '只能恢复已暂缓或跳过的活动')
            changes.append({'activity_id':row.id,'from':row.status,'to':'READY'}); row.status = 'READY'
        if goal.status == 'PAUSED':
            changes.append({'from':'PAUSED','to':'ACTIVE'}); goal.status = 'ACTIVE'
    else:
        row = next((r for r in activities if r.id == body.activity_id), None)
        if row is None:
            raise HTTPException(404, '请选择当前目标下的活动')
        if row.status in ('RUNNING','QUEUED'):
            raise HTTPException(409, '当前提交仍在检查，完成或失败后可跳过后续活动')
        changes.append({'activity_id':row.id,'from':row.status,'to':'SKIPPED'}); row.status = 'SKIPPED'
    goal.plan_revision += 1
    history = list((goal.meta or {}).get('plan_history', []))
    history.append({'revision':goal.plan_revision,'at':datetime.now(timezone.utc).isoformat(),
        'reason':body.reason.strip(),'action':body.action,'changes':changes,'mastery_updated':False})
    goal.meta = {**(goal.meta or {}), 'plan_history':history}
    await emit_event(session, ws.id, 'learning.plan_adjusted', {'goal_id':goal.id,'action':body.action,
        'revision':goal.plan_revision,'reason':body.reason.strip(),'changes':changes,'mastery_updated':False}, 'learning_goal',goal.id)
    await session.commit()
    return {'goal_id':goal.id,'plan_revision':goal.plan_revision,'status':goal.status,
            'changes':changes,'mastery_updated':False}
