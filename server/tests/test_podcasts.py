import asyncio
import hashlib
import json
import sys
from types import ModuleType
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Event, Question, Workspace
from app.models.podcast import PodcastEpisode, PodcastSource
from app.services.learning_exposure import EVENT_KIND, exposed_families

ROOT = "/api/v1/podcasts"
MP3 = b"ID3\x04fixture"


@pytest_asyncio.fixture(autouse=True)
async def isolated_api_engine(monkeypatch):
    from app import db, main
    from app.api.v1 import project_analysis, question_generation
    from app.services import podcast_jobs, study_chat

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    for module in (db, podcast_jobs, project_analysis, question_generation, study_chat):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(main, "engine", engine)
    yield
    await engine.dispose()


@pytest.fixture
def content(monkeypatch):
    module = ModuleType("app.services.podcast_content")

    async def make_scripts(session, workspace_id, *, title, answer, topic, series, source):
        assert answer and topic and source and workspace_id
        scripts = [{"title": title, "script": f"Answer to {title}"}]
        if series:
            scripts.append({"title": f"{title} continued", "script": "A second answer"})
        return scripts

    async def synthesize_mp3(script):
        return MP3, 62.25

    module.make_scripts = make_scripts
    module.synthesize_mp3 = synthesize_mp3
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def client(workspace=None):
    from app.main import app

    headers = {"X-Workspace": workspace.slug, "X-Api-Token": get_settings().api_token} if workspace else {}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers)


async def question(db, workspace, *, title="Podcast source", answer="<p>Frozen answer</p>", **fields):
    row = Question(
        workspace_id=workspace.id if workspace else None,
        short_code="pod-" + uuid4().hex[:20],
        title=title,
        topic="rag",
        answer_html=answer,
        **fields,
    )
    db.add(row)
    await db.commit()
    return row


async def import_questions(api, *ids, series=False, playlist_id=None, skipped=()):
    response = await api.post(
        ROOT + "/import",
        json={
            "question_ids": ids,
            "series": series,
            "playlist_id": playlist_id,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["skipped_question_ids"] == list(skipped)
    return result["episode_ids"]


async def library(api):
    response = await api.get(ROOT)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_sources_are_safe_visible_and_partial_import_is_idempotent(db_session, test_workspace):
    other = Workspace(slug="test-" + uuid4().hex, name="Private workspace")
    db_session.add(other)
    await db_session.commit()
    own = await question(db_session, test_workspace, title="Exact podcast 100%")
    await question(db_session, test_workspace, title="Exact podcast 100x")
    hidden = await question(db_session, other, title=own.title)
    practice_only = await question(
        db_session,
        test_workspace,
        title=own.title,
        generation_meta={
            "assessment_use": "PRACTICE_ONLY",
            "origin": "unapproved",
        },
    )
    no_answer = await question(db_session, test_workspace, title=own.title, answer=None)
    variant = await question(db_session, test_workspace, title=own.title, canonical_question_id=own.id)
    async with client() as anonymous, client(test_workspace) as api, client(other) as outsider:
        assert (await anonymous.get(ROOT + "/sources")).status_code == 401
        result = await api.get(ROOT + "/sources", params={"q": "100%", "limit": 1})
        assert result.json() == {
            "items": [
                {
                    "id": own.id,
                    "question_code": own.short_code,
                    "title": own.title,
                    "topic": own.topic,
                }
            ],
            "total": 1,
        }
        assert (await api.get(ROOT + "/sources", params={"q": own.short_code, "offset": 1})).json() == {
            "items": [],
            "total": 1,
        }
        imports = await asyncio.gather(
            import_questions(
                api,
                own.id,
                hidden.id,
                practice_only.id,
                no_answer.id,
                variant.id,
                own.id,
                hidden.id,
                skipped=[hidden.id, practice_only.id, no_answer.id, variant.id],
            ),
            import_questions(api, own.id),
        )
        assert imports[0] == imports[1]
        (episode_id,) = imports[0]
        assert [row["status"] for row in (await library(api))["episodes"]] == ["generating"]
        assert (await api.get(f"{ROOT}/episodes/{episode_id}/script")).status_code == 409
        assert (await outsider.patch(f"{ROOT}/episodes/{episode_id}", json={"position_seconds": 5})).status_code == 404
        assert (
            await outsider.post(ROOT + "/playlists", json={"name": "private", "episode_ids": [episode_id]})
        ).status_code == 404
        unavailable = await api.post(ROOT + "/import", json={"question_ids": [hidden.id], "series": False})
        missing = await api.post(ROOT + "/import", json={"question_ids": [2**31 - 1], "series": False})
        assert unavailable.status_code == missing.status_code == 422
        assert unavailable.json() == missing.json()
        assert "reference answer" in unavailable.json()["detail"]
    assert not await exposed_families(db_session, test_workspace.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("exposure_source", ["bank_detail", "podcast_script"])
async def test_saved_series_titles_require_exposure_to_the_original_question(
    db_session, test_workspace, track_public_question, exposure_source
):
    from app.services.learning_exposure import record_reference_exposure

    original = track_public_question(await question(db_session, None, title="How do retries affect delivery?"))
    variant = track_public_question(await question(db_session, None, canonical_question_id=original.id))
    other = Workspace(slug="test-" + uuid4().hex, name="Other workspace")
    db_session.add(other)
    await db_session.flush()
    await record_reference_exposure(db_session, test_workspace, variant, source="bank_detail")
    await record_reference_exposure(db_session, other, original, source="bank_detail")
    version = hashlib.sha256(
        json.dumps(
            [original.short_code, original.title, original.topic, original.source_type, original.answer_html],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    source = PodcastSource(
        workspace_id=test_workspace.id,
        question_id=original.id,
        question_code=original.short_code,
        title=original.title,
        topic=original.topic,
        source_type=original.source_type,
        answer=original.answer_html,
        version=version,
        series=True,
    )
    db_session.add(source)
    await db_session.flush()
    first = PodcastEpisode(
        workspace_id=test_workspace.id,
        source_id=source.id,
        part=1,
        title="Deduplicate using the operation key",
        status="draft",
        script="Start with a stable operation key.",
    )
    second = PodcastEpisode(
        workspace_id=test_workspace.id,
        source_id=source.id,
        part=2,
        title="Persist the result before acknowledging",
        status="draft",
        script="Send the acknowledgement after persisting the result.",
    )
    db_session.add_all([first, second])
    await db_session.commit()

    async with client(test_workspace) as api:
        assert await import_questions(api, original.id, series=True) == [first.id, second.id]
        before = (await library(api))["episodes"]
        assert [(row["part"], row["title"]) for row in before] == [
            (1, f"{original.title} · 第 1 集"),
            (2, f"{original.title} · 第 2 集"),
        ]
        changed = await api.patch(f"{ROOT}/episodes/{second.id}", json={"familiarity": "unfamiliar"})
        assert changed.status_code == 200
        assert changed.json()["title"] == before[1]["title"]
        assert changed.json()["familiarity"] == "unfamiliar"
        if exposure_source == "bank_detail":
            reference = await api.get(f"/api/v1/bank/{original.short_code}")
            assert reference.status_code == 200
            assert reference.json()["answer_html"] == original.answer_html
            assert [row["title"] for row in (await library(api))["episodes"]] == [first.title, second.title]
        script = await api.get(f"{ROOT}/episodes/{first.id}/script")
        assert script.status_code == 200
        assert script.json() == {"title": first.title, "script": "Start with a stable operation key."}
        assert [row["title"] for row in (await library(api))["episodes"]] == [first.title, second.title]
    exposure = await db_session.scalar(
        select(Event).where(
            Event.workspace_id == test_workspace.id,
            Event.kind == EVENT_KIND,
            Event.payload["question_id"].as_integer() == original.id,
        )
    )
    assert exposure.payload["source"] == exposure_source
    await db_session.delete(other)
    await db_session.commit()


@pytest.mark.asyncio
async def test_mcq_options_are_frozen_and_versioned_without_leaking_metadata(db_session, test_workspace, monkeypatch):
    from app.config import Settings
    from app.services import podcast_content, podcast_jobs

    original = await question(
        db_session,
        test_workspace,
        title="Which retry policy prevents duplicate delivery?",
        answer='<p>Use the <a href="https://example.test/retries">retry contract</a>.</p>',
        qtype="mcq",
        options=["Drop <requests>", "Keep the same operation key", "Resend every request"],
    )
    config = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_base_url="https://provider.test/v1",
        llm_api_key="fixture-key",
        llm_model="fixture-model",
    )
    monkeypatch.setattr(podcast_content, "get_settings", lambda: config)
    sent = []

    async def chat_with(self, config, prompt, request, **kwargs):
        sent.append(json.loads(request))
        return {"content": '{"episodes":[]}'}

    monkeypatch.setattr(podcast_content.AgentAdapter, "chat_with", chat_with)

    async with client(test_workspace) as api:
        (first,) = await import_questions(api, original.id)
        assert await import_questions(api, original.id) == [first]
        original.options = [original.options[1], original.options[0], "Retry indefinitely"]
        await db_session.commit()
        (second,) = await import_questions(api, original.id)
        assert second != first
        assert await import_questions(api, original.id) == [second]

        source_list = await api.get(ROOT + "/sources", params={"q": original.short_code})
        assert source_list.json() == {
            "items": [{
                "id": original.id,
                "question_code": original.short_code,
                "title": original.title,
                "topic": original.topic,
            }],
            "total": 1,
        }
        rows = {row["id"]: row for row in (await library(api))["episodes"]}
        for episode_id in (first, second):
            assert rows[episode_id]["title"] == original.title
            assert not {"answer", "options", "script"} & rows[episode_id].keys()
        for secret in ("Keep the same operation key", "Resend every request", "Retry indefinitely", "retry contract"):
            assert secret not in json.dumps(rows)
        assert not await exposed_families(db_session, test_workspace.id)

    assert await podcast_jobs.process_next()
    assert await podcast_jobs.process_next()
    assert [item["title"] for item in sent] == [original.title, original.title]
    assert [item["allowed_source_urls"] for item in sent] == [["https://example.test/retries"]] * 2
    first_answer, second_answer = [item["answer"] for item in sent]
    assert [line for line in first_answer.splitlines() if line.startswith(("A. ", "B. ", "C. "))] == [
        "A. Drop <requests>", "B. Keep the same operation key", "C. Resend every request",
    ]
    assert [line for line in second_answer.splitlines() if line.startswith(("A. ", "B. ", "C. "))] == [
        "A. Keep the same operation key", "B. Drop <requests>", "C. Retry indefinitely",
    ]
    assert all("Use the retry contract." in answer for answer in (first_answer, second_answer))


@pytest.mark.asyncio
async def test_series_partial_retry_playlist_audio_and_exposure(db_session, test_workspace, content, monkeypatch):
    from app.services import podcast_jobs

    original = await question(db_session, test_workspace)
    async with client(test_workspace) as api:
        playlist_response = await api.post(ROOT + "/playlists", json={"name": "Commute"})
        playlist_id = playlist_response.json()["id"]
        (first,) = await import_questions(api, original.id, series=True, playlist_id=playlist_id)
        assert await podcast_jobs.process_next()
        data = await library(api)
        by_part = {row["part"]: row for row in data["episodes"]}
        second = by_part[2]["id"]
        assert by_part[1]["id"] == first
        assert all(row["status"] == "draft" and "script" not in row for row in by_part.values())
        assert data["playlists"] == [{"name": "Commute", "id": playlist_id, "episode_ids": [first, second]}]
        assert await import_questions(api, original.id, series=True, playlist_id=playlist_id) == [first, second]
        assert not await exposed_families(db_session, test_workspace.id)
        script_url = f"{ROOT}/episodes/{first}/script"
        scripts = await asyncio.gather(api.get(script_url), api.get(script_url))
        assert [result.json() for result in scripts] == [
            {"title": original.title, "script": f"Answer to {original.title}"},
        ] * 2

        attempts = []

        async def synthesize(script):
            attempts.append(script)
            if script == "A second answer" and attempts.count(script) == 1:
                raise RuntimeError("private provider error")
            return MP3, 62.25

        monkeypatch.setattr(content, "synthesize_mp3", synthesize)
        await asyncio.gather(*(api.post(f"{ROOT}/episodes/{first}/synthesize") for _ in range(2)))
        assert (await api.post(f"{ROOT}/episodes/{second}/synthesize")).status_code == 200
        assert await podcast_jobs.process_next()
        assert await podcast_jobs.process_next()
        data = await library(api)
        assert {row["id"]: row["status"] for row in data["episodes"]} == {first: "ready", second: "audio_failed"}
        failed = next(row for row in data["episodes"] if row["id"] == second)
        assert failed["error"] and "private provider error" not in failed["error"]
        assert (await api.post(f"{ROOT}/episodes/{second}/retry")).status_code == 200
        assert await podcast_jobs.process_next()
        assert attempts == [f"Answer to {original.title}", "A second answer", "A second answer"]
        assert not await podcast_jobs.process_next()

        response = await api.get(f"{ROOT}/episodes/{second}/audio")
        assert response.content == MP3
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.headers["cache-control"] == "private, no-store"
        patched = await api.patch(
            f"{ROOT}/episodes/{second}",
            json={
                "position_seconds": 80,
                "familiarity": "unfamiliar",
            },
        )
        assert patched.json()["position_seconds"] == patched.json()["duration_seconds"] == 62.25
        assert patched.json()["familiarity"] == "unfamiliar"
        edited = await api.patch(
            f"{ROOT}/playlists/{playlist_id}",
            json={
                "name": "Listening",
                "episode_ids": [second, first, second],
            },
        )
        assert edited.json() == {"id": playlist_id, "name": "Listening", "episode_ids": [second, first]}
        other_list = (await api.post(ROOT + "/playlists", json={"name": "Again", "episode_ids": [second]})).json()
        assert (await api.delete(f"{ROOT}/playlists/{playlist_id}")).status_code == 204
        assert (await api.delete(f"{ROOT}/episodes/{second}")).status_code == 204
        assert (await api.get(f"{ROOT}/episodes/{second}/audio")).status_code == 404
        assert (await library(api))["playlists"] == [{**other_list, "episode_ids": []}]
        assert await import_questions(api, original.id, series=True) == [first]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.workspace_id == test_workspace.id,
                Event.kind == EVENT_KIND,
            )
        )
        == 1
    )
    assert original.id in await exposed_families(db_session, test_workspace.id)


@pytest.mark.asyncio
async def test_expired_claim_cannot_overwrite_successor_or_restore_deleted_audio(
    db_session,
    test_workspace,
    content,
    monkeypatch,
):
    from app.services import podcast_jobs

    source = await question(db_session, test_workspace)
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def stalled(*args, **kwargs):
        entered.set()
        await resume.wait()
        return [{"title": "Expired worker", "script": "Stale result"}]

    async def successor(*args, **kwargs):
        return [{"title": "Current worker", "script": "Current result"}]

    monkeypatch.setattr(content, "make_scripts", stalled)
    async with client(test_workspace) as api:
        (episode_id,) = await import_questions(api, source.id)
        stale = asyncio.create_task(podcast_jobs.process_next())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert not await podcast_jobs.process_next()
            await db_session.execute(
                update(PodcastEpisode)
                .where(PodcastEpisode.id == episode_id)
                .values(
                    lease_until=func.now() - podcast_jobs.LEASE,
                )
            )
            await db_session.commit()
            monkeypatch.setattr(content, "make_scripts", successor)
            assert await podcast_jobs.process_next()
        finally:
            resume.set()
            await asyncio.wait_for(stale, 5)
        assert (await library(api))["episodes"][0]["title"] == source.title
        assert (await api.get(f"{ROOT}/episodes/{episode_id}/script")).json() == {
            "title": "Current worker", "script": "Current result",
        }

        resume.clear()
        entered.clear()

        async def audio(script):
            entered.set()
            await resume.wait()
            return MP3, 60

        monkeypatch.setattr(content, "synthesize_mp3", audio)
        assert (await api.post(f"{ROOT}/episodes/{episode_id}/synthesize")).status_code == 200
        deleted = asyncio.create_task(podcast_jobs.process_next())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert (await asyncio.wait_for(api.delete(f"{ROOT}/episodes/{episode_id}"), 5)).status_code == 204
        finally:
            resume.set()
            await asyncio.wait_for(deleted, 5)
        assert (await api.get(f"{ROOT}/episodes/{episode_id}/audio")).status_code == 404
        (newer,) = await import_questions(api, source.id)
        assert newer != episode_id


@pytest.mark.asyncio
async def test_experienced_catalog_visibility_for_workspace_token(
    db_session,
    test_workspace,
    track_public_question,
    monkeypatch,
):
    from hashlib import sha256

    from app.services import experienced_bank

    domain = experienced_bank.load_catalog()[0]
    item = domain.questions[0]
    meta = {
        "assessment_use": experienced_bank.PRACTICE_ONLY,
        "origin": experienced_bank.ORIGIN,
        "pack": experienced_bank.PACK,
        "domain_key": domain.key,
    }
    installed = track_public_question(
        Question(
            short_code=item.code,
            title=item.title,
            topic=domain.key,
            answer_html="<p>Authored reference</p>",
            generation_meta=meta,
        )
    )
    decoy = track_public_question(
        Question(
            short_code="pod-" + uuid4().hex[:20],
            title=item.title,
            topic=domain.key,
            answer_html=installed.answer_html,
            generation_meta=meta,
        )
    )
    db_session.add_all([installed, decoy])
    await db_session.commit()
    token = "test-token-" + uuid4().hex
    monkeypatch.setattr(
        get_settings(),
        "workspace_token_hashes",
        {
            test_workspace.slug: sha256(token.encode()).hexdigest(),
        },
    )

    async with client(test_workspace) as api:
        api.headers["X-Api-Token"] = token
        sources = await api.get(ROOT + "/sources", params={"q": item.code})
        assert [source["id"] for source in sources.json()["items"]] == [installed.id]
        assert (await api.get(ROOT + "/sources", params={"q": decoy.short_code})).json()["total"] == 0
        (episode,) = await import_questions(api, installed.id, decoy.id, skipped=[decoy.id])
        assert (await library(api))["episodes"][0]["question_id"] == installed.id
        assert (await api.get(f"{ROOT}/episodes/{episode}/script", headers={"X-Workspace": "demo"})).status_code == 404


@pytest.mark.asyncio
async def test_real_lifespan_retries_frozen_script_and_delivers_audio_first(
    db_session,
    test_workspace,
    content,
    monkeypatch,
):
    from app import main
    from app.services import podcast_jobs

    original = await question(db_session, test_workspace)
    unaffected = await question(db_session, test_workspace, title="Other source", answer="Other answer")
    received = []

    async def make_scripts(session, workspace_id, *, title, answer, topic, series, source):
        received.append(answer)
        if len(received) == 1:
            raise RuntimeError("script callback failed")
        return [{"title": title, "script": answer}]

    monkeypatch.setattr(content, "make_scripts", make_scripts)
    monkeypatch.setattr(podcast_jobs, "POLL_SECONDS", 0.02)

    async with client(test_workspace) as api:
        original_id, other_id = await import_questions(api, original.id, unaffected.id)
        assert (await api.post(f"{ROOT}/episodes/{original_id}/synthesize")).status_code == 409
        original.answer_html = "<p>New answer version</p>"
        await db_session.commit()
        (updated_id,) = await import_questions(api, original.id)
        assert updated_id != original_id

        async def wait_for(episode_id, status):
            async with asyncio.timeout(10):
                while True:
                    data = {row["id"]: row for row in (await library(api))["episodes"]}
                    if data[episode_id]["status"] == status:
                        return data[episode_id]
                    await asyncio.sleep(0.05)

        async with main.app.router.lifespan_context(main.app):
            assert (await wait_for(original_id, "script_failed"))["error"]
            await wait_for(other_id, "draft")
            await wait_for(updated_id, "draft")
            assert (await api.post(f"{ROOT}/episodes/{original_id}/retry")).status_code == 200
            await wait_for(original_id, "draft")
            assert received == [
                "<p>Frozen answer</p>",
                "Other answer",
                "<p>New answer version</p>",
                "<p>Frozen answer</p>",
            ]
            assert not await exposed_families(db_session, test_workspace.id)

            assert (await api.post(f"{ROOT}/episodes/{original_id}/synthesize")).status_code == 200
            await wait_for(original_id, "ready")
            assert not await exposed_families(db_session, test_workspace.id)
            assert (await api.get(f"{ROOT}/episodes/{original_id}/audio")).content == MP3
            event = await db_session.scalar(
                select(Event).where(
                    Event.workspace_id == test_workspace.id,
                    Event.kind == EVENT_KIND,
                )
            )
            assert event.payload["source"] == "podcast_audio"
            assert event.payload["question_id"] == original.id
            assert (await api.get(f"{ROOT}/episodes/{original_id}/script")).json()["script"] == "<p>Frozen answer</p>"
            await db_session.execute(delete(Workspace).where(Workspace.id == test_workspace.id))
            await db_session.commit()
            assert await db_session.scalar(select(PodcastEpisode.id).where(PodcastEpisode.id == original_id)) is None
