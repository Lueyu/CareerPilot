"""Authenticated podcast library, playback state, and material delivery."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, raiseload

from app.api.deps import get_workspace
from app.db import get_session
from app.models import Question, Workspace
from app.models.podcast import (
    EpisodeStatus,
    Familiarity,
    PodcastEpisode,
    PodcastLanguage,
    PodcastPlaylist,
    PodcastSource,
)
from app.services import podcast_library as library
from app.services.learning_exposure import record_reference_exposure

router = APIRouter(prefix="/podcasts")
WorkspaceDep = Annotated[Workspace, Depends(get_workspace)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ResourceId = Annotated[int, Path(gt=0, le=2**63 - 1)]
QuestionIds = Annotated[list[Annotated[int, Field(gt=0, le=2**31 - 1)]], Field(min_length=1, max_length=5000)]
EpisodeIds = Annotated[list[Annotated[int, Field(gt=0, le=2**63 - 1)]], Field(max_length=5000)]
PlaylistName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160, pattern=r"^[^\x00]+$")
]
PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class ImportRequest(BaseModel):
    question_ids: QuestionIds
    series: bool = False
    language: PodcastLanguage = "zh"
    playlist_id: Annotated[int, Field(gt=0, le=2**63 - 1)] | None = None


class EpisodeUpdate(BaseModel):
    familiarity: Familiarity | None = None
    position_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


class PlaylistCreate(BaseModel):
    name: PlaylistName
    episode_ids: EpisodeIds = Field(default_factory=list)


class PlaylistUpdate(BaseModel):
    name: PlaylistName | None = None
    episode_ids: EpisodeIds | None = None


@router.get("")
async def get_library(ws: WorkspaceDep, session: SessionDep, response: Response):
    rows = await session.execute(
        library.episode_rows(ws.id).order_by(
            PodcastEpisode.source_id.desc(),
            PodcastEpisode.part,
        )
    )
    episodes = [library.episode_payload(*row) for row in rows]
    playlists = await library.list_playlists(session, ws.id, {episode["id"] for episode in episodes})
    response.headers.update(PRIVATE_HEADERS)
    return {"episodes": episodes, "playlists": playlists}


@router.get("/sources")
async def get_sources(
    ws: WorkspaceDep,
    session: SessionDep,
    response: Response,
    q: Annotated[str, Query(max_length=400)] = "",
    limit: Annotated[int, Query(ge=1, le=5000)] = 60,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
):
    if "\x00" in q:
        raise HTTPException(422, "Invalid search query")
    response.headers.update(PRIVATE_HEADERS)
    return await library.list_sources(session, ws.id, q, limit, offset)


@router.post("/import")
async def import_questions(body: ImportRequest, ws: WorkspaceDep, session: SessionDep):
    return await library.import_sources(
        session, ws.id, body.question_ids, body.series, body.playlist_id, language=body.language
    )


async def _deliver_material(session, ws, episode_id, field, source):
    if await library.lock_workspace(session, ws.id) is None:
        raise HTTPException(404, "Workspace not found")
    row = (
        await session.execute(
            select(Question, PodcastEpisode.title, field)
            .select_from(PodcastEpisode)
            .join(PodcastSource)
            .join(Question, PodcastSource.question_id == Question.id)
            .options(
                load_only(Question.id, Question.workspace_id, Question.canonical_question_id, raiseload=True),
                raiseload("*"),
            )
            .where(
                PodcastEpisode.id == episode_id,
                PodcastEpisode.workspace_id == ws.id,
                or_(Question.workspace_id.is_(None), Question.workspace_id == ws.id),
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Episode not found")
    question, title, material = row
    if not material:
        raise HTTPException(409, "Podcast material is not ready")
    await record_reference_exposure(session, ws, question, source=source)
    await session.commit()
    return title, material


@router.get("/episodes/{episode_id}/script")
async def get_script(episode_id: ResourceId, ws: WorkspaceDep, session: SessionDep, response: Response):
    title, script = await _deliver_material(session, ws, episode_id, PodcastEpisode.script, "podcast_script")
    response.headers.update(PRIVATE_HEADERS)
    return {"title": title, "script": script}


@router.get("/episodes/{episode_id}/audio", response_class=Response, responses={200: {"content": {"audio/mpeg": {}}}})
async def get_audio(episode_id: ResourceId, ws: WorkspaceDep, session: SessionDep):
    _, audio = await _deliver_material(session, ws, episode_id, PodcastEpisode.audio, "podcast_audio")
    return Response(audio, media_type="audio/mpeg", headers=PRIVATE_HEADERS)


async def _queue_episode(session, workspace_id, episode_id, transitions, intact):
    owned = (PodcastEpisode.id == episode_id) & (PodcastEpisode.workspace_id == workspace_id)
    for old, new in transitions.items():
        queued = await session.scalar(
            update(PodcastEpisode)
            .where(
                owned,
                PodcastEpisode.status == old,
            )
            .values(status=new, error=None)
            .returning(PodcastEpisode.status)
        )
        if queued is not None:
            await session.commit()
            return {"status": queued}
    status = await session.scalar(select(PodcastEpisode.status).where(owned))
    if status is None:
        raise HTTPException(404, "Episode not found")
    if status not in intact:
        raise HTTPException(409, "Episode cannot be queued in its current state")
    return {"status": status}


@router.post("/episodes/{episode_id}/synthesize")
async def synthesize(episode_id: ResourceId, ws: WorkspaceDep, session: SessionDep):
    status = EpisodeStatus
    return await _queue_episode(
        session,
        ws.id,
        episode_id,
        {status.DRAFT: status.SYNTHESIZING},
        {
            status.SYNTHESIZING,
            status.READY,
        },
    )


@router.post("/episodes/{episode_id}/retry")
async def retry(episode_id: ResourceId, ws: WorkspaceDep, session: SessionDep):
    status = EpisodeStatus
    return await _queue_episode(
        session,
        ws.id,
        episode_id,
        {
            status.SCRIPT_FAILED: status.GENERATING,
            status.AUDIO_FAILED: status.SYNTHESIZING,
        },
        {status.GENERATING, status.SYNTHESIZING, status.DRAFT, status.READY},
    )


@router.patch("/episodes/{episode_id}")
async def update_episode(episode_id: ResourceId, body: EpisodeUpdate, ws: WorkspaceDep, session: SessionDep):
    fields = body.model_dump(exclude_none=True)
    if body.position_seconds is not None:
        fields["position_seconds"] = func.least(body.position_seconds, PodcastEpisode.duration_seconds)
    if fields:
        await session.execute(
            update(PodcastEpisode)
            .where(
                PodcastEpisode.id == episode_id,
                PodcastEpisode.workspace_id == ws.id,
            )
            .values(**fields)
        )
    row = (await session.execute(library.episode_rows(ws.id).where(PodcastEpisode.id == episode_id))).one_or_none()
    if row is None:
        raise HTTPException(404, "Episode not found")
    if fields:
        await session.commit()
    return library.episode_payload(*row)


@router.delete("/episodes/{episode_id}", status_code=204)
async def delete_episode(episode_id: ResourceId, ws: WorkspaceDep, session: SessionDep):
    await library.delete_episode(session, ws.id, episode_id)


@router.post("/playlists")
async def create_playlist(body: PlaylistCreate, ws: WorkspaceDep, session: SessionDep):
    if await library.lock_workspace(session, ws.id) is None:
        raise HTTPException(404, "Workspace not found")
    playlist = PodcastPlaylist(workspace_id=ws.id, name=body.name)
    session.add(playlist)
    await session.flush()
    episode_ids = await library.replace_members(session, playlist, body.episode_ids)
    await session.commit()
    return {"id": playlist.id, "name": playlist.name, "episode_ids": episode_ids}


@router.patch("/playlists/{playlist_id}")
async def update_playlist(playlist_id: ResourceId, body: PlaylistUpdate, ws: WorkspaceDep, session: SessionDep):
    await library.lock_workspace(session, ws.id)
    playlist = await library.require_playlist(session, ws.id, playlist_id)
    if body.name is not None:
        playlist.name = body.name
    if body.episode_ids is not None:
        episode_ids = await library.replace_members(session, playlist, body.episode_ids)
    else:
        episode_ids = await library.playlist_episode_ids(session, playlist)
    await session.commit()
    return {"id": playlist.id, "name": playlist.name, "episode_ids": episode_ids}


@router.delete("/playlists/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: ResourceId, ws: WorkspaceDep, session: SessionDep):
    await library.lock_workspace(session, ws.id)
    deleted = await session.scalar(
        delete(PodcastPlaylist)
        .where(
            PodcastPlaylist.id == playlist_id,
            PodcastPlaylist.workspace_id == ws.id,
        )
        .returning(PodcastPlaylist.id)
    )
    if deleted is None:
        raise HTTPException(404, "Playlist not found")
    await session.commit()
