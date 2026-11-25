"""Podcast catalog visibility and short transactions for library structure."""

import hashlib
import json
from html import escape
from string import ascii_uppercase

from fastapi import HTTPException
from sqlalchemy import case, delete, func, or_, select, tuple_, update
from sqlalchemy.orm import lazyload

from app.models import Event, Question, Workspace
from app.models.podcast import (
    EpisodeStatus,
    PodcastEpisode,
    PodcastLanguage,
    PodcastPlaylist,
    PodcastPlaylistEpisode,
    PodcastSource,
)
from app.services.answer_guides import current_reference
from app.services.bank_practice import bank_list_question_clause
from app.services.experienced_bank import list_installed_pack
from app.services.learning_exposure import EVENT_KIND


async def source_filters(session, workspace_id):
    pack = await list_installed_pack(session)
    experienced = [question["id"] for domain in pack["domains"] for question in domain["questions"]]
    return (
        or_(bank_list_question_clause(workspace_id), Question.id.in_(experienced)),
        Question.answer_html.op("~")(r"[^[:space:]]"),
    )


async def list_sources(session, workspace_id, query, limit, offset):
    statement = select(Question.id, Question.short_code, Question.title, Question.topic).where(
        *await source_filters(session, workspace_id),
    )
    if query := query.strip():
        statement = statement.where(
            or_(
                Question.title.icontains(query, autoescape=True),
                Question.short_code.icontains(query, autoescape=True),
                Question.topic.icontains(query, autoescape=True),
            )
        )
    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = await session.execute(statement.order_by(Question.id).offset(offset).limit(limit))
    return {
        "items": [
            {"id": row.id, "question_code": row.short_code, "title": row.title, "topic": row.topic} for row in rows
        ],
        "total": total,
    }


async def lock_workspace(session, workspace_id):
    return await session.scalar(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())


def _reference_with_options(question, answer):
    if question.qtype != "mcq" or not question.options:
        return answer
    options = []
    for index, option in enumerate(question.options):
        label = ascii_uppercase[index] if index < len(ascii_uppercase) else str(index + 1)
        options.append(f"<li>{label}. {escape(option)}</li>")
    return "<p>选项</p><ul>" + "".join(options) + "</ul><p>参考答案</p>" + answer


async def import_sources(session, workspace_id, question_ids, series, playlist_id, *, language: PodcastLanguage = "zh"):
    rows = await session.scalars(
        select(Question)
        .options(lazyload("*"))
        .where(
            Question.id.in_(question_ids),
            *await source_filters(session, workspace_id),
        )
    )
    questions = {question.id: question for question in rows}
    snapshots = {}
    skipped_question_ids = []
    for question_id in dict.fromkeys(question_ids):
        question = questions.get(question_id)
        if question is None or not (answer := current_reference(question)) or not answer.strip():
            skipped_question_ids.append(question_id)
            continue
        answer = _reference_with_options(question, answer)
        version_fields = [question.short_code, question.title, question.topic, question.source_type, answer]
        if language != "zh":
            version_fields.append(language)
        version = hashlib.sha256(json.dumps(version_fields, ensure_ascii=False).encode()).hexdigest()
        snapshots[question_id, version] = PodcastSource(
            workspace_id=workspace_id,
            question_id=question_id,
            question_code=question.short_code,
            title=question.title,
            topic=question.topic,
            source_type=question.source_type,
            answer=answer,
            version=version,
            series=series,
            language=language,
        )

    if not snapshots:
        raise HTTPException(
            422,
            "None of the selected questions can be imported. Choose questions with a reference answer available in this workspace.",
        )
    if await lock_workspace(session, workspace_id) is None:
        raise HTTPException(404, "Workspace not found")
    playlist = await require_playlist(session, workspace_id, playlist_id) if playlist_id is not None else None

    rows = await session.execute(
        select(
            PodcastSource.question_id,
            PodcastSource.version,
            PodcastSource.id,
            PodcastEpisode.id,
        )
        .join(PodcastEpisode)
        .where(
            PodcastSource.workspace_id == workspace_id,
            PodcastSource.series == series,
            PodcastSource.language == language,
            tuple_(PodcastSource.question_id, PodcastSource.version).in_(list(snapshots)),
        )
        .order_by(PodcastEpisode.part)
    )
    existing = {}
    source_ids = {}
    for question_id, version, source_id, episode_id in rows:
        source_ids[question_id, version] = source_id
        existing.setdefault((question_id, version), []).append(episode_id)

    new = {key: snapshot for key, snapshot in snapshots.items() if key not in existing}
    if new:
        session.add_all(new.values())
        await session.flush()
        source_ids.update({key: snapshot.id for key, snapshot in new.items()})
        seeds = {
            key: PodcastEpisode(
                source_id=snapshot.id,
                workspace_id=workspace_id,
                part=1,
                title=snapshot.title,
                status=EpisodeStatus.GENERATING,
            )
            for key, snapshot in new.items()
        }
        session.add_all(seeds.values())
        await session.flush()
        existing.update({key: [episode.id] for key, episode in seeds.items()})

    episode_ids = [episode_id for key in snapshots for episode_id in existing[key]]
    if playlist is not None:
        members = set(await playlist_episode_ids(session, playlist))
        position = await session.scalar(
            select(func.max(PodcastPlaylistEpisode.position)).where(
                PodcastPlaylistEpisode.playlist_id == playlist.id,
                PodcastPlaylistEpisode.workspace_id == workspace_id,
            )
        )
        add_members(
            session,
            playlist.id,
            workspace_id,
            (eid for eid in episode_ids if eid not in members),
            start=0 if position is None else position + 1,
        )
    await session.commit()
    return {
        "episode_ids": episode_ids,
        "source_ids": [source_ids[key] for key in snapshots],
        "skipped_question_ids": skipped_question_ids,
    }


def episode_payload(episode, question_id, question_code, topic, title, language):
    return {
        "id": episode.id,
        "source_id": episode.source_id,
        "question_id": question_id,
        "question_code": question_code,
        "title": title,
        "topic": topic,
        "language": language,
        "part": episode.part,
        "status": episode.status,
        "duration_seconds": episode.duration_seconds,
        "position_seconds": episode.position_seconds,
        "familiarity": episode.familiarity,
        "error": episode.error,
    }


def episode_rows(workspace_id):
    exposed = select(Event.id).where(
        Event.workspace_id == workspace_id,
        Event.kind == EVENT_KIND,
        Event.payload["question_id"] == func.to_jsonb(PodcastSource.question_id),
        Event.payload.contains({"material_returned": True}),
    ).exists()
    title = case(
        (exposed, PodcastEpisode.title),
        (PodcastSource.series, func.concat(PodcastSource.title, " · 第 ", PodcastEpisode.part, " 集")),
        else_=PodcastSource.title,
    ).label("title")
    return (
        select(
            PodcastEpisode,
            PodcastSource.question_id,
            PodcastSource.question_code,
            PodcastSource.topic,
            title,
            PodcastSource.language,
        )
        .join(PodcastSource)
        .where(PodcastEpisode.workspace_id == workspace_id)
    )


async def require_playlist(session, workspace_id, playlist_id):
    playlist = await session.scalar(
        select(PodcastPlaylist).where(
            PodcastPlaylist.id == playlist_id,
            PodcastPlaylist.workspace_id == workspace_id,
        )
    )
    if playlist is None:
        raise HTTPException(404, "Playlist not found")
    return playlist


async def playlist_episode_ids(session, playlist):
    return list(
        await session.scalars(
            select(PodcastPlaylistEpisode.episode_id)
            .where(
                PodcastPlaylistEpisode.playlist_id == playlist.id,
                PodcastPlaylistEpisode.workspace_id == playlist.workspace_id,
            )
            .order_by(PodcastPlaylistEpisode.position, PodcastPlaylistEpisode.episode_id)
        )
    )


async def list_playlists(session, workspace_id, episode_ids):
    rows = await session.execute(
        select(PodcastPlaylist.id, PodcastPlaylist.name, PodcastPlaylistEpisode.episode_id)
        .outerjoin(PodcastPlaylistEpisode)
        .where(PodcastPlaylist.workspace_id == workspace_id)
        .order_by(PodcastPlaylist.id, PodcastPlaylistEpisode.position, PodcastPlaylistEpisode.episode_id)
    )
    result = {}
    for row in rows:
        playlist = result.setdefault(row.id, {"id": row.id, "name": row.name, "episode_ids": []})
        if row.episode_id in episode_ids:
            playlist["episode_ids"].append(row.episode_id)
    return list(result.values())


def add_members(session, playlist_id, workspace_id, episode_ids, *, start):
    session.add_all(
        PodcastPlaylistEpisode(
            playlist_id=playlist_id, workspace_id=workspace_id, episode_id=episode_id, position=position
        )
        for position, episode_id in enumerate(episode_ids, start=start)
    )


async def replace_members(session, playlist, episode_ids):
    unique = list(dict.fromkeys(episode_ids))
    visible = set(
        await session.scalars(
            select(PodcastEpisode.id).where(
                PodcastEpisode.workspace_id == playlist.workspace_id,
                PodcastEpisode.id.in_(unique),
            )
        )
    )
    if len(visible) != len(unique):
        raise HTTPException(404, "Episode not found")
    await session.execute(
        delete(PodcastPlaylistEpisode).where(
            PodcastPlaylistEpisode.playlist_id == playlist.id,
            PodcastPlaylistEpisode.workspace_id == playlist.workspace_id,
        )
    )
    add_members(session, playlist.id, playlist.workspace_id, unique, start=0)
    return unique


async def extend_series_playlists(session, workspace_id, first_id, following_ids):
    memberships = await session.execute(
        select(PodcastPlaylistEpisode.playlist_id, PodcastPlaylistEpisode.position).where(
            PodcastPlaylistEpisode.workspace_id == workspace_id, PodcastPlaylistEpisode.episode_id == first_id
        )
    )
    for playlist_id, position in memberships:
        await session.execute(
            update(PodcastPlaylistEpisode)
            .where(
                PodcastPlaylistEpisode.workspace_id == workspace_id,
                PodcastPlaylistEpisode.playlist_id == playlist_id,
                PodcastPlaylistEpisode.position > position,
            )
            .values(position=PodcastPlaylistEpisode.position + len(following_ids))
        )
        add_members(session, playlist_id, workspace_id, following_ids, start=position + 1)


async def delete_episode(session, workspace_id, episode_id):
    await lock_workspace(session, workspace_id)
    source_id = await session.scalar(
        delete(PodcastEpisode)
        .where(
            PodcastEpisode.id == episode_id,
            PodcastEpisode.workspace_id == workspace_id,
        )
        .returning(PodcastEpisode.source_id)
    )
    if source_id is None:
        raise HTTPException(404, "Episode not found")
    await session.execute(
        delete(PodcastSource).where(
            PodcastSource.id == source_id,
            PodcastSource.workspace_id == workspace_id,
            ~select(PodcastEpisode.id)
            .where(
                PodcastEpisode.source_id == source_id,
                PodcastEpisode.workspace_id == workspace_id,
            )
            .exists(),
        )
    )
    await session.commit()
