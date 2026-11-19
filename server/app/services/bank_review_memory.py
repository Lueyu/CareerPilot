from collections.abc import Sequence
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportItem, ImportSession, MemoryItem

BANK_REVIEW_FEEDBACK_TYPE = "BankReviewFeedback"
MAX_REVIEW_CASES = 8
MAX_REVIEW_EXCERPT = 1200


def review_feedback_eligible(row: MemoryItem) -> bool:
    return (
        row.mtype == BANK_REVIEW_FEEDBACK_TYPE
        and row.status == "ACTIVE"
        and row.freshness == "CURRENT"
        and row.confirmed_at is not None
        and bool((row.meta or {}).get("skill_key"))
    )


async def record_review_feedback(
    session: AsyncSession,
    workspace_id: int,
    import_id: int,
    item_id: int,
    skill_key: str | None,
    action: str,
    request_id: str,
    feedback: str | None,
) -> int | None:
    """Record the submitted text in the caller's locked review transaction."""
    if not feedback or not feedback.strip():
        return None
    owned_item_id = await session.scalar(
        select(ImportItem.id)
        .join(ImportSession, ImportSession.id == ImportItem.session_id)
        .where(ImportItem.id == item_id, ImportSession.id == import_id, ImportSession.workspace_id == workspace_id)
    )
    if owned_item_id is None:
        raise ValueError("Review feedback source does not belong to this workspace and import")

    request_digest = sha256(request_id.encode()).hexdigest()
    source_ref = f"bank-review:{import_id}:{item_id}:{request_digest}"
    previous_id = await session.scalar(
        select(MemoryItem.id).where(
            MemoryItem.workspace_id == workspace_id,
            MemoryItem.mtype == BANK_REVIEW_FEEDBACK_TYPE,
            MemoryItem.source_ref == source_ref,
        )
    )
    if previous_id is not None:
        return previous_id

    row = MemoryItem(
        workspace_id=workspace_id,
        mtype=BANK_REVIEW_FEEDBACK_TYPE,
        content=feedback,
        source="USER",
        source_ref=source_ref,
        status="ACTIVE",
        freshness="CURRENT",
        confirmed_at=datetime.now(timezone.utc),
        meta={
            "import_id": import_id,
            "item_id": item_id,
            "skill_key": skill_key,
            "action": action,
            "request_id": request_id,
        },
    )
    session.add(row)
    await session.flush()
    return row.id


async def load_review_feedback(
    session: AsyncSession, workspace_id: int, skill_keys: Sequence[str]
) -> list[dict]:
    """Return brief, recent cases scoped to the requested skills and workspace."""
    keys = {key for key in skill_keys if key}
    if not keys:
        return []
    rows = (
        await session.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.workspace_id == workspace_id,
                MemoryItem.mtype == BANK_REVIEW_FEEDBACK_TYPE,
                MemoryItem.status == "ACTIVE",
                MemoryItem.freshness == "CURRENT",
                MemoryItem.confirmed_at.is_not(None),
                MemoryItem.meta["skill_key"].as_string().in_(keys),
            )
            .order_by(MemoryItem.id.desc())
            .limit(MAX_REVIEW_CASES)
        )
    ).all()
    return [
        {
            "memory_id": row.id,
            "skill_key": row.meta["skill_key"],
            "action": row.meta["action"],
            "feedback": row.content[:MAX_REVIEW_EXCERPT],
        }
        for row in rows
    ]
