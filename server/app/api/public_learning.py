"""Daily planning for the authenticated workspace's selected role."""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import message as localized_message
from app.api.public_auth import current_workspace
from app.db import get_session
from app.models import Question, Workspace
from app.models.question import SOURCE_GENERATED, SOURCE_PRIVATE
from app.services.daily_learning_plan import DEFAULT_CAPACITY, MAX_DAYS, PREFERENCE_KEY, PlanItem, build_plan
from app.services.task_memory import record_daily_plan_evidence

router = APIRouter(prefix="/learning/daily-plan", tags=["public-learning"])
LOCAL_TIME = timezone(timedelta(hours=8))
Db = Annotated[AsyncSession, Depends(get_session)]
Space = Annotated[Workspace, Depends(current_workspace)]


class RoleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_role: str = Field(max_length=120)


class PlanSettings(RoleSelection):
    deadline: date
    daily_minutes: int = Field(ge=35, le=480)
    confirm_baseline: bool = False


class EvidenceIn(RoleSelection):
    item_key: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=8, max_length=4000)


async def _load(db: AsyncSession, workspace_id: int, today: date, expected_role: str | None = None):
    ws = (await db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one()
    role = ws.target_role or ""
    if expected_role is not None and expected_role != role:
        raise HTTPException(409, localized_message("errors.your_target_role_has_changed_refresh_the_study_plan"))
    if expected_role is not None and not role:
        raise HTTPException(422, localized_message("errors.set_your_target_role_in_the_question_bank_first"))
    roles = (ws.preferences or {}).get(PREFERENCE_KEY, {}).get("roles", {})
    state = dict(roles.get(role) or {
        "start_date": today.isoformat(),
        "daily_minutes": DEFAULT_CAPACITY,
    })
    if not state.get("deadline_confirmed"):
        state["deadline"] = (today + timedelta(days=14)).isoformat()
    items = []
    if role:
        rows = await db.execute(
            select(Question.id, Question.title, Question.topic)
            .where(Question.workspace_id == ws.id, Question.role == role, Question.is_demo.is_(False),
                   Question.source_type.in_((SOURCE_PRIVATE, SOURCE_GENERATED)))
            .order_by(Question.id)
        )
        items = [
            PlanItem(f"question:{question_id}", title, topic, "question", 25,
                     "完成学习或答题，记录你的答案和一项待改进之处。", question_id=question_id)
            for question_id, title, topic in rows
        ]
    return ws, role, state, items


def _schedule(state: dict, today: date, items: list[PlanItem], *, replan=False, baseline=False):
    return build_plan(
        state, today, items,
        reschedule=replan or baseline or state.get("planned_on") != today.isoformat(),
        capture_baseline=bool(items) and (baseline or "pace_baseline" not in state),
    )


async def _save(db: AsyncSession, ws: Workspace, role: str, state: dict, plan: dict):
    state.update(assignments=plan.pop("assignments"), planned_on=plan["today"])
    baseline = plan.pop("pace_baseline")
    if baseline is not None:
        state["pace_baseline"] = baseline
    if role:
        preferences = ws.preferences or {}
        saved = preferences.get(PREFERENCE_KEY, {})
        roles = saved.get("roles", {})
        if roles.get(role) != state:
            ws.preferences = {**preferences, PREFERENCE_KEY: {**saved, "roles": {**roles, role: state}}}
    await db.commit()
    return {**plan, "target_role": role, "deadline_confirmed": state.get("deadline_confirmed", False)}


@router.get("")
async def get_plan(db: Db, workspace: Space):
    today = datetime.now(LOCAL_TIME).date()
    ws, role, state, items = await _load(db, workspace.id, today)
    return await _save(db, ws, role, state, _schedule(state, today, items))


@router.put("")
async def update_plan(body: PlanSettings, db: Db, workspace: Space):
    today = datetime.now(LOCAL_TIME).date()
    if not today <= body.deadline <= today + timedelta(days=MAX_DAYS):
        raise HTTPException(422, localized_message("errors.choose_a_target_date_within_the_next_180_days"))
    ws, role, state, items = await _load(db, workspace.id, today, body.target_role)
    changed = state["deadline"] != body.deadline.isoformat() or state["daily_minutes"] != body.daily_minutes
    state.update(deadline=body.deadline.isoformat(), daily_minutes=body.daily_minutes, deadline_confirmed=True)
    plan = _schedule(state, today, items, baseline=changed or (body.confirm_baseline and "pace_baseline" not in state))
    return await _save(db, ws, role, state, plan)


@router.post("/reschedule")
async def reschedule(body: RoleSelection, db: Db, workspace: Space):
    today = datetime.now(LOCAL_TIME).date()
    ws, role, state, items = await _load(db, workspace.id, today, body.target_role)
    return await _save(db, ws, role, state, _schedule(state, today, items, replan=True))


@router.post("/evidence")
async def record_evidence(body: EvidenceIn, db: Db, workspace: Space):
    now = datetime.now(LOCAL_TIME)
    today = now.date()
    ws, role, state, items = await _load(db, workspace.id, today, body.target_role)
    if not any(item.key == body.item_key for item in items):
        raise HTTPException(404, localized_message("errors.this_question_is_outside_the_plan_for_the_current"))
    plan = _schedule(state, today, items)
    note = body.note.strip()
    evidence = state.get("evidence", {})
    if evidence.get(body.item_key, {}).get("note") == note:
        return await _save(db, ws, role, state, plan)
    eligible = next((item for day in plan["days"] if day["date"] <= today.isoformat()
                     for item in day["items"] if item["key"] == body.item_key and item["status"] != "completed"), None)
    if eligible is None or len(note) < 8:
        raise HTTPException(422, localized_message("errors.describe_how_you_completed_an_open_exercise_due_today"))
    state["evidence"] = {**evidence, body.item_key: {"date": today.isoformat(), "note": note}}
    state["assignments"] = plan["assignments"]
    if plan["pace_baseline"] is not None:
        state["pace_baseline"] = plan["pace_baseline"]
    await record_daily_plan_evidence(db, workspace_id=ws.id, item_key=body.item_key,
                                     title=eligible["title"], completed_at=now, note=note)
    return await _save(db, ws, role, state, _schedule(state, today, items))
