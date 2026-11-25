"""Lease-backed podcast jobs. Content callbacks run outside claim and result transactions."""

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update

from app.db import SessionLocal
from app.models.podcast import EpisodeStatus, PodcastEpisode, PodcastSource
from app.services.podcast_library import extend_series_playlists, lock_workspace

logger = logging.getLogger(__name__)

LEASE = timedelta(seconds=45)
POLL_SECONDS = 1
MAX_AUDIO_BYTES = 64 * 1024 * 1024

FAILURES = {
    EpisodeStatus.GENERATING: (EpisodeStatus.SCRIPT_FAILED, "讲稿生成失败。请检查已配置的模型后重试。"),
    EpisodeStatus.SYNTHESIZING: (EpisodeStatus.AUDIO_FAILED, "音频合成失败。请检查本机中文语音引擎后重试。"),
}


@dataclass(frozen=True)
class Claim:
    episode_id: int
    workspace_id: int
    status: EpisodeStatus
    token: UUID

    def condition(self):
        return (
            (PodcastEpisode.id == self.episode_id)
            & (PodcastEpisode.workspace_id == self.workspace_id)
            & (PodcastEpisode.status == self.status)
            & (PodcastEpisode.lease_token == self.token)
            & (PodcastEpisode.lease_until > func.clock_timestamp())
        )


async def _claim_next():
    async with SessionLocal.begin() as session:
        row = (
            await session.execute(
                select(
                    PodcastEpisode.id,
                    PodcastEpisode.workspace_id,
                    PodcastEpisode.status,
                )
                .where(
                    PodcastEpisode.status.in_(FAILURES),
                    or_(PodcastEpisode.lease_until.is_(None), PodcastEpisode.lease_until <= func.clock_timestamp()),
                )
                .order_by(PodcastEpisode.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).one_or_none()
        if row is None:
            return None

        claim = Claim(row.id, row.workspace_id, EpisodeStatus(row.status), uuid4())
        await session.execute(
            update(PodcastEpisode)
            .where(PodcastEpisode.id == row.id)
            .values(
                lease_token=claim.token,
                lease_until=func.clock_timestamp() + LEASE,
            )
        )
        return claim


async def _renew(claim):
    async with SessionLocal.begin() as session:
        return (
            await session.scalar(
                update(PodcastEpisode)
                .where(claim.condition())
                .values(
                    lease_until=func.clock_timestamp() + LEASE,
                )
                .returning(PodcastEpisode.id)
            )
            is not None
        )


async def _release(claim):
    async with SessionLocal.begin() as session:
        await session.execute(
            update(PodcastEpisode)
            .where(
                PodcastEpisode.id == claim.episode_id,
                PodcastEpisode.lease_token == claim.token,
            )
            .values(lease_token=None, lease_until=None)
        )


async def _generate_scripts(claim, content):
    async with SessionLocal() as session:
        source = (
            await session.execute(
                select(
                    PodcastSource.title,
                    PodcastSource.answer,
                    PodcastSource.topic,
                    PodcastSource.series,
                    PodcastSource.source_type,
                )
                .select_from(PodcastEpisode)
                .join(PodcastSource)
                .where(claim.condition())
            )
        ).one_or_none()
    if source is None:
        return None

    async with SessionLocal() as session:
        scripts = await content.make_scripts(
            session,
            claim.workspace_id,
            title=source.title,
            answer=source.answer,
            topic=source.topic,
            series=source.series,
            source=source.source_type,
        )

    if not isinstance(scripts, list) or not scripts or (not source.series and len(scripts) != 1):
        raise ValueError("Invalid number of podcast scripts")
    parts = []
    for script in scripts:
        title, text = script["title"].strip(), script["script"].strip()
        if not title or not text or "\x00" in title or "\x00" in text:
            raise ValueError("Empty or invalid podcast script")
        parts.append((title, text))
    return parts


async def _generate_audio(claim, content):
    async with SessionLocal() as session:
        script = await session.scalar(select(PodcastEpisode.script).where(claim.condition()))
    if script is None:
        return None

    audio, seconds = await content.synthesize_mp3(script)
    if not isinstance(audio, bytes) or not 0 < len(audio) <= MAX_AUDIO_BYTES:
        raise ValueError("Invalid podcast audio data")
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Invalid podcast audio duration")
    return audio, seconds


async def _generate(claim):
    content = import_module("app.services.podcast_content")
    if claim.status == EpisodeStatus.GENERATING:
        return await _generate_scripts(claim, content)
    return await _generate_audio(claim, content)


async def _save_scripts(claim, parts):
    async with SessionLocal.begin() as session:
        if await lock_workspace(session, claim.workspace_id) is None:
            return
        title, script = parts[0]
        source_id = await session.scalar(
            update(PodcastEpisode)
            .where(claim.condition())
            .values(
                title=title,
                script=script,
                status=EpisodeStatus.DRAFT,
                lease_token=None,
                lease_until=None,
            )
            .returning(PodcastEpisode.source_id)
        )
        if source_id is None or len(parts) == 1:
            return

        following = [
            PodcastEpisode(
                workspace_id=claim.workspace_id,
                source_id=source_id,
                part=part,
                title=title,
                script=script,
                status=EpisodeStatus.DRAFT,
            )
            for part, (title, script) in enumerate(parts[1:], start=2)
        ]
        session.add_all(following)
        await session.flush()
        await extend_series_playlists(session, claim.workspace_id, claim.episode_id, [row.id for row in following])


async def _finish(claim, **values):
    async with SessionLocal.begin() as session:
        await session.execute(
            update(PodcastEpisode)
            .where(claim.condition())
            .values(
                lease_token=None,
                lease_until=None,
                **values,
            )
        )


async def _run_claim(claim):
    work = asyncio.create_task(_generate(claim))
    try:
        while True:
            done, _ = await asyncio.wait({work}, timeout=LEASE.total_seconds() / 3)
            if done:
                break
            if not await _renew(claim):
                return
        try:
            result = work.result()
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning("Podcast episode %s failed in %s (%s)", claim.episode_id, claim.status, type(exc).__name__)
            status, error = FAILURES[claim.status]
            await _finish(claim, status=status, error=error)
            return

        if result is None:
            return
        if claim.status == EpisodeStatus.GENERATING:
            await _save_scripts(claim, result)
        else:
            audio, seconds = result
            await _finish(claim, status=EpisodeStatus.READY, audio=audio, duration_seconds=seconds)
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)


async def process_next():
    claim = await _claim_next()
    if claim is None:
        return False
    try:
        await _run_claim(claim)
    except asyncio.CancelledError:
        try:
            async with asyncio.timeout(5):
                await _release(claim)
        except Exception as exc:
            logger.warning(
                "Podcast episode %s lease will expire after shutdown (%s)", claim.episode_id, type(exc).__name__
            )
        raise
    return True


async def run_worker():
    while True:
        try:
            if await process_next():
                continue
        except Exception as exc:
            logger.error("Podcast queue transaction failed (%s)", type(exc).__name__)
        await asyncio.sleep(POLL_SECONDS)
