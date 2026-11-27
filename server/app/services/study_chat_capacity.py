from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import JSONB

from app.config import get_settings
from app.i18n import request_language
from app.models.study_chat import StudyMessage, StudySession


async def admit_generation(session, workspace_id):
    """Caller holds the Workspace row lock until its turn is committed."""
    settings = get_settings()
    await session.execute(select(func.pg_advisory_xact_lock(20261128, 1101)))
    active = select(StudySession.workspace_id).join(
        StudyMessage, StudyMessage.session_id == StudySession.id,
    ).where(
        StudyMessage.role == "assistant", StudyMessage.status == "generating",
        StudyMessage.expires_at > func.clock_timestamp(),
    ).subquery()
    total, workspace = (await session.execute(select(
        func.count(), func.count().filter(active.c.workspace_id == workspace_id),
    ).select_from(active))).one()
    if workspace >= settings.study_generation_workspace_limit or total >= settings.study_generation_global_limit:
        detail = ("学习回复正在生成，请稍后重试。" if request_language() == "zh-CN"
                  else "Study generation capacity is full. Retry shortly.")
        raise HTTPException(429, detail, headers={"Retry-After": "2"})
    return {
        "workspace_id": workspace_id,
        "workspace_limit": settings.study_generation_workspace_limit,
        "global_accepted_limit": settings.study_generation_global_limit,
    }


def generation_event(trace, event, attempt):
    trace["lifecycle"] = [*(trace.get("lifecycle") or [])[-31:], {
        "event": event, "attempt": attempt, "at": datetime.now(timezone.utc).isoformat(),
    }]


def interrupted_coach(event):
    trace = func.coalesce(StudyMessage.coach["trace"], literal({}, type_=JSONB))
    events = func.coalesce(StudyMessage.coach["trace"]["lifecycle"], literal([], type_=JSONB))
    terminal = func.jsonb_build_object(
        "stage", "interrupted", "error_kind", event, "lifecycle",
        events.op("||")(func.jsonb_build_array(func.jsonb_build_object(
            "event", event, "attempt", StudyMessage.attempt, "at", func.clock_timestamp(),
        ))),
    )
    return StudyMessage.coach.op("||")(func.jsonb_build_object("trace", trace.op("||")(terminal)))
