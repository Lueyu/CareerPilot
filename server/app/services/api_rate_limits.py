"""Shared PostgreSQL token buckets. One transaction admits all categories or none."""
import hashlib
from datetime import datetime, time, timedelta, timezone
from math import ceil
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ApiRatePolicy
from app.models.api_rate_limit import ApiRateBucket

Category = Literal["read", "write", "llm"]
DAY_ZONE = timezone(timedelta(hours=8))


async def _database_time(session: AsyncSession) -> datetime:
    return await session.scalar(select(func.clock_timestamp()))


async def admit(session: AsyncSession, subject: str, categories: tuple[Category, ...], policy: ApiRatePolicy) -> None:
    """Caller supplies a new independent session, after resolving a trusted subject."""
    lock = int.from_bytes(hashlib.blake2b(subject.encode(), digest_size=8, person=b"cp-api-e3").digest(), signed=True)
    async with session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
        # clock_timestamp, unlike now(), is sampled after waiting for the lock.
        now = await _database_time(session)
        today = now.astimezone(DAY_ZONE).date()
        reset = datetime.combine(today + timedelta(days=1), time(), tzinfo=DAY_ZONE)
        rows = {row.category: row for row in await session.scalars(
            select(ApiRateBucket).where(ApiRateBucket.subject == subject).with_for_update()
        )}
        changes = []
        retry = 0
        for category in dict.fromkeys(categories):
            quota = getattr(policy, category)
            row = rows.get(category)
            elapsed = max(0.0, (now - row.updated_at).total_seconds()) if row else 0
            burst = min(quota.burst, row.burst_tokens + elapsed * quota.per_minute / 60) if row else quota.burst
            count = row.daily_count if row and row.quota_day == today else 0
            retry = max(retry, ceil(max(0, 1 - burst) * 60 / quota.per_minute),
                        ceil((reset - now).total_seconds()) if count >= quota.daily else 0)
            changes.append((category, row, burst, count, quota))
        if retry:
            raise HTTPException(429, {"code": "API_RATE_LIMITED", "message": "API quota exceeded. Try again later."},
                                headers={"Retry-After": str(max(1, retry))})
        for category, row, burst, count, quota in changes:
            if row is None:
                row = ApiRateBucket(subject=subject, category=category)
                session.add(row)
            row.burst_tokens, row.daily_count = burst - 1, count + 1
            row.quota_day = today
            row.updated_at = now
            # Never discard today's hard counter, or a burst bucket before it fills.
            row.expires_at = max(reset, now + timedelta(seconds=quota.burst * 60 / quota.per_minute))
        expired = select(ApiRateBucket.subject, ApiRateBucket.category).where(
            ApiRateBucket.expires_at <= now, ApiRateBucket.subject != subject,
        ).order_by(ApiRateBucket.expires_at).limit(100).with_for_update(skip_locked=True)
        await session.execute(delete(ApiRateBucket).where(
            tuple_(ApiRateBucket.subject, ApiRateBucket.category).in_(expired)
        ))
