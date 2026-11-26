"""Frozen podcast inputs, individual episodes, and ordered playlists."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import BigInteger, Double, LargeBinary, String, Text

from app.db import Base

PodcastLanguage = Literal["zh", "en", "bilingual"]


class EpisodeStatus(StrEnum):
    GENERATING = "generating"
    DRAFT = "draft"
    SYNTHESIZING = "synthesizing"
    READY = "ready"
    SCRIPT_FAILED = "script_failed"
    AUDIO_FAILED = "audio_failed"


class Familiarity(StrEnum):
    UNMARKED = "unmarked"
    FAMILIAR = "familiar"
    UNFAMILIAR = "unfamiliar"


class PodcastSource(Base):
    __tablename__ = "podcast_sources"
    __table_args__ = (
        UniqueConstraint("id", "workspace_id", name="podcast_sources_scope"),
        UniqueConstraint("workspace_id", "question_id", "version", "series", name="podcast_sources_version"),
        Index("ix_podcast_sources_question_id", "question_id"),
        CheckConstraint("language IN ('zh', 'en', 'bilingual')", name="podcast_sources_language"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    question_code: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(32))
    answer: Mapped[str] = mapped_column(Text, deferred=True)
    version: Mapped[str] = mapped_column(String(64))
    series: Mapped[bool]
    language: Mapped[PodcastLanguage] = mapped_column(String(9), server_default="zh")


class PodcastEpisode(Base):
    __tablename__ = "podcast_episodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "workspace_id"], ["podcast_sources.id", "podcast_sources.workspace_id"], ondelete="CASCADE"
        ),
        UniqueConstraint("id", "workspace_id", name="podcast_episodes_scope"),
        UniqueConstraint("source_id", "part", name="podcast_episodes_part"),
        CheckConstraint("part > 0", name="podcast_episodes_positive_part"),
        CheckConstraint(
            "status IN ('generating', 'draft', 'synthesizing', 'ready', 'script_failed', 'audio_failed')",
            name="podcast_episodes_status",
        ),
        CheckConstraint(
            "familiarity IN ('unmarked', 'familiar', 'unfamiliar')",
            name="podcast_episodes_familiarity",
        ),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL) "
            "AND (lease_token IS NULL OR status IN ('generating', 'synthesizing'))",
            name="podcast_episodes_lease",
        ),
        Index("ix_podcast_episodes_workspace_id", "workspace_id", "id"),
        Index("ix_podcast_episodes_queue", "status", "lease_until", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger)
    workspace_id: Mapped[int]
    part: Mapped[int]
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    script: Mapped[str | None] = mapped_column(Text, deferred=True)
    audio: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    duration_seconds: Mapped[float] = mapped_column(Double, server_default="0")
    position_seconds: Mapped[float] = mapped_column(Double, server_default="0")
    familiarity: Mapped[str] = mapped_column(String(16), server_default=Familiarity.UNMARKED)
    error: Mapped[str | None] = mapped_column(Text)
    lease_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PodcastPlaylist(Base):
    __tablename__ = "podcast_playlists"
    __table_args__ = (UniqueConstraint("id", "workspace_id", name="podcast_playlists_scope"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))


class PodcastPlaylistEpisode(Base):
    __tablename__ = "podcast_playlist_episodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["playlist_id", "workspace_id"],
            ["podcast_playlists.id", "podcast_playlists.workspace_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["episode_id", "workspace_id"],
            ["podcast_episodes.id", "podcast_episodes.workspace_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("position >= 0", name="podcast_playlist_episodes_position"),
        Index("ix_podcast_playlist_episodes_episode_id", "episode_id", "workspace_id"),
    )

    playlist_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    episode_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int]
    position: Mapped[int]
