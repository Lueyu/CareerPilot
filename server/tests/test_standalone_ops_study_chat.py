import json
import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from app import db
from app.api.v1 import study_chat
from app.models import Question, StudyMessage, StudySession
from app.services import study_chat as worker
from fastapi import BackgroundTasks
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_llm_stream_transport import DONE, Body, event, frame, install
from test_study_chat_attempts import generation as generation

ROUTE = "/api/v1/ops/study-chat/messages"
TOKEN = 'owner"</script><script>window.injected=true</script>'
AUTH = {"X-Ops-Token": TOKEN}
PRIVATE = "private-fixture-text"


@pytest.fixture
def ops(monkeypatch, db_session):
    source = Path(__file__).resolve().parents[2] / "deploy" / "ops" / "app.py"
    monkeypatch.syspath_prepend(str(source.parent))
    spec = spec_from_file_location("standalone_ops", source)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OPS_TOKEN", TOKEN)
    monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(db_session.bind, expire_on_commit=False))
    return module


def client(ops):
    return AsyncClient(transport=ASGITransport(app=ops.app), base_url="http://test")


@pytest.mark.asyncio
async def test_exact_lookup_requires_configured_header_auth(ops, monkeypatch):
    async with client(ops) as http:
        for credentials in ({}, {"headers": {"X-Ops-Token": "wrong"}}, {"params": {"token": TOKEN}}):
            assert (await http.get(f"{ROUTE}/1", **credentials)).status_code == 401
        assert (await http.get(f"{ROUTE}/2147483647", headers=AUTH)).status_code == 404
        for invalid in ("0", "-1", "2147483648", "1.2", PRIVATE):
            response = await http.get(f"{ROUTE}/{invalid}", headers=AUTH)
            assert response.status_code == 422
            assert PRIVATE not in response.text
        assert (await http.get(ROUTE, headers=AUTH)).status_code == 404
        assert (await http.post(f"{ROUTE}/1", headers=AUTH)).status_code == 405

        dashboard = await http.get("/", params={"token": TOKEN})
        assert dashboard.status_code == 200
        assert dashboard.headers["cache-control"] == "no-store"
        assert dashboard.headers["referrer-policy"] == "no-referrer"
        assert TOKEN not in dashboard.text
        assert "window.injected=true</script>" not in dashboard.text
        assert dashboard.text == (await http.get("/", headers=AUTH)).text

        monkeypatch.setattr(ops, "OPS_TOKEN", "")
        assert (await http.get(f"{ROUTE}/1", headers=AUTH)).status_code == 503
        assert (await http.get("/api/v1/ops/metrics")).status_code == 200
        assert "window.injected=true" not in (await http.get("/", params={"token": TOKEN})).text


@pytest.mark.asyncio
async def test_dashboard_refresh_keeps_auth_without_url_token(ops):
    async with client(ops) as http:
        assert (await http.get("/")).status_code == 401
        assert (await http.get("/", params={"token": "wrong"})).status_code == 401
        initial = await http.get("/", params={"token": TOKEN})
        assert initial.status_code == 200
        refreshed = await http.get("/")
        assert refreshed.status_code == 200
        assert refreshed.url.query == b""
        assert refreshed.text == initial.text
        assert "set-cookie" not in refreshed.headers
        assert refreshed.headers["cache-control"] == "no-store"
        assert refreshed.headers["referrer-policy"] == "no-referrer"
        cookie = initial.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert "Max-Age=900" in cookie
        assert TOKEN not in cookie
        assert (await http.get("/", params={"token": "wrong"})).status_code == 401
        assert (await http.get("/", headers={"X-Ops-Token": "wrong"})).status_code == 401
        assert (await http.get("/api/v1/ops/metrics")).status_code == 401
        assert (await http.get(f"{ROUTE}/1")).status_code == 401
        assert (await http.get(f"{ROUTE}/1", params={"token": TOKEN})).status_code == 401
        assert (await http.get(f"{ROUTE}/2147483647", headers=AUTH)).status_code == 404


@pytest.mark.asyncio
async def test_dashboard_session_expires_and_rejects_forgery(ops, monkeypatch):
    monkeypatch.setattr(ops, "time", lambda: 1_000)
    async with client(ops) as http:
        assert (await http.get("/", headers=AUTH)).status_code == 200
        session = http.cookies[ops.DASHBOARD_COOKIE]
        expires, signature = session.split(".")
        for forged in ("garbage", f"{int(expires) + 1}.{signature}", f"{expires}.{'0' * 64}"):
            http.cookies.clear()
            response = await http.get("/", headers={"Cookie": f"{ops.DASHBOARD_COOKIE}={forged}"})
            assert response.status_code == 401
            assert "set-cookie" not in response.headers
        http.cookies.set(ops.DASHBOARD_COOKIE, session)
        monkeypatch.setattr(ops, "time", lambda: 1_899)
        assert (await http.get("/")).status_code == 200
        monkeypatch.setattr(ops, "time", lambda: 1_900)
        assert (await http.get("/")).status_code == 401
        monkeypatch.setattr(ops, "time", lambda: 1_000)
        monkeypatch.setattr(ops, "OPS_TOKEN", "rotated-secret")
        assert (await http.get("/")).status_code == 401


@pytest.mark.asyncio
async def test_dashboard_cookie_is_secure_on_https(ops):
    async with AsyncClient(transport=ASGITransport(app=ops.app), base_url="https://test") as http:
        response = await http.get("/", headers=AUTH)
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert (await http.get("/")).status_code == 200


@pytest.mark.asyncio
async def test_lookup_projects_historical_sources_without_private_fields(ops, db_session, test_workspace):
    session = StudySession(workspace_id=test_workspace.id, title=PRIVATE, context={})
    db_session.add(session)
    await db_session.flush()
    user = StudyMessage(session_id=session.id, request_id=PRIVATE, role="user",
                        content=PRIVATE, action="explain", status="completed")
    reply = StudyMessage(
        session_id=session.id, request_id=PRIVATE, role="assistant", content=PRIVATE,
        action="explain", status="interrupted", model=PRIVATE, error=PRIVATE, attempt=2,
        sources=[
            {"kind": "library_summary", "text": json.dumps({
                "题库命中": 7, "面经套题命中": 3, "学习资料命中": 0, "检索词": [PRIVATE],
            })},
            {"kind": "library_question", "id": 8099, "label": PRIVATE, "text": PRIVATE, "hash": PRIVATE},
            {"kind": "library_interview_set", "id": PRIVATE, "label": PRIVATE, "text": PRIVATE},
        ],
        coach={"inventory": {"title": PRIVATE}, "trace": {
            "stage": "generating", "reason": PRIVATE, "error_kind": PRIVATE, "message_ids": [8099],
            "calls": [{
                "attempt": 1, "generation": 1, "provider": PRIVATE, "reported_model": PRIVATE,
                "requested_model": PRIVATE, "reasoning": PRIVATE, "saved_output": PRIVATE,
                "error": PRIVATE, "outcome": PRIVATE, "usage": {"total_tokens": 13, "prompt_tokens": PRIVATE},
            }],
        }},
    )
    db_session.add_all([user, reply])
    await db_session.commit()

    async with client(ops) as http:
        assert (await http.get(f"{ROUTE}/{user.id}", headers=AUTH)).status_code == 404
        response = await http.get(f"{ROUTE}/{reply.id}", headers=AUTH)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert PRIVATE not in response.text
    data = response.json()
    assert data["retrieval"] == {
        "state": "matched", "evidence": "saved_sources", "started_ms": None, "finished_ms": None,
        "hits": {"questions": 7, "interview_sets": 3, "notes": 0},
        "returned": {"questions": 1, "interview_sets": 1, "notes": 0},
    }
    assert data["locale"] is None
    assert data["persistence"]["status"] == "interrupted"
    assert data["persistence"]["attempt"] == 2
    assert data["persistence"]["terminal_write_at"] is None
    assert data["provider"]["state"] == "unknown"
    assert data["provider"]["calls"][0] == {
        "attempt": 1, "generation": 1, "provider": None, "tier": None, "outcome": None,
        "error": None, "http_status": None, "retry_kind": None, "fallback_blocked": None,
        "provider_start_ms": None, "provider_end_ms": None, "elapsed_ms": None, "first_visible_body_ms": None,
        "usage": {"total_tokens": 13, "prompt_tokens": None, "completion_tokens": None},
    }


async def submit(db, ws, content, *, mode="auto"):
    sid = (await study_chat.create(study_chat.StartSession(mode=mode), db, ws))["id"]
    tasks = BackgroundTasks()
    turn = await study_chat.send(
        sid, study_chat.NewMessage(request_id="lookup-fixture", content=content), tasks, db, ws,
    )
    return sid, turn["assistant_message_id"], tasks


@pytest.mark.asyncio
@pytest.mark.usefixtures("generation")
async def test_lookup_shows_empty_retrieval_and_completed_stream(ops, monkeypatch, db_session, test_workspace):
    usage = {"prompt_tokens": 17, "completion_tokens": 9, "total_tokens": 26}
    calls = install(monkeypatch, Body([
        frame(PRIVATE), frame(finish="stop"), event({"choices": [], "usage": usage}), DONE,
    ]))
    _, message_id, tasks = await submit(db_session, test_workspace, "show my library for zzemptyfixture")
    async with client(ops) as http:
        queued = (await http.get(f"{ROUTE}/{message_id}", headers=AUTH)).json()
        assert queued["persistence"]["stage"] == "queued"
        await tasks()
        response = await http.get(f"{ROUTE}/{message_id}", headers=AUTH)
    assert response.status_code == 200 and PRIVATE not in response.text
    assert len(calls) == 1
    data = response.json()
    assert data["locale"] == "zh-CN"
    retrieval, routing, provider, persistence = (
        data["retrieval"], data["routing"], data["provider"], data["persistence"],
    )
    assert retrieval["state"] == "empty" and retrieval["evidence"] == "trace"
    assert retrieval["hits"] == retrieval["returned"] == {"questions": 0, "interview_sets": 0, "notes": 0}
    assert 0 <= retrieval["started_ms"] <= retrieval["finished_ms"] <= routing["started_ms"] <= routing["finished_ms"]
    assert provider["state"] == "recorded" and provider["body_release_stage"] == "stream"
    (call,) = provider["calls"]
    assert routing["finished_ms"] <= call["provider_start_ms"] <= provider["first_visible_body_ms"]
    assert provider["first_visible_body_ms"] <= call["provider_end_ms"] <= persistence["finalizing_ms"]
    assert call["outcome"] == "completed" and call["usage"] == usage
    assert persistence["status"] == persistence["stage"] == "completed"
    assert re.match(r"\d{4}-\d{2}-\d{2}T", persistence["terminal_write_at"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("generation")
async def test_lookup_shows_provider_failure_and_manual_retry(ops, monkeypatch, db_session, test_workspace):
    requests = install(monkeypatch, Body([event({"error": {"message": PRIVATE}})]))
    sid, message_id, tasks = await submit(db_session, test_workspace, "Explain vector retrieval")
    await tasks()
    async with client(ops) as http:
        response = await http.get(f"{ROUTE}/{message_id}", headers=AUTH)
        assert PRIVATE not in response.text
        failed = response.json()
        retry_tasks = BackgroundTasks()
        await study_chat.retry(sid, message_id, retry_tasks, db_session, test_workspace)
        queued = (await http.get(f"{ROUTE}/{message_id}", headers=AUTH)).json()
    assert len(requests) == 1
    assert failed["retrieval"]["state"] == "not_requested"
    assert failed["provider"]["first_visible_body_ms"] is None
    (call,) = failed["provider"]["calls"]
    assert call["error"] == failed["persistence"]["error_kind"] == "invalid_response"
    assert call["usage"] is None
    assert failed["persistence"]["status"] == "failed"
    assert queued["persistence"]["attempt"] == 2 and queued["persistence"]["stage"] == "queued"
    assert queued["persistence"]["terminal_write_at"] is None
    assert queued["provider"]["calls"] == [call] and queued["provider"]["state"] == "not_called"
    assert queued["routing"]["started_ms"] is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("generation")
async def test_lookup_shows_transport_failover(ops, monkeypatch, db_session, test_workspace):
    settings = worker.get_settings().model_copy(update={
        "study_strong_model": "strong-fixture", "study_relay_same_upstream": False,
    })
    monkeypatch.setattr(worker, "get_settings", lambda: settings)

    async def transport(_request):
        if len(requests) == 1:
            return Response(503, text=PRIVATE)
        return Response(200, headers={"content-type": "text/event-stream"},
                        stream=Body([frame(PRIVATE), frame(finish="stop"), DONE]))

    requests = install(monkeypatch, None, handler=transport)
    _, message_id, tasks = await submit(db_session, test_workspace, "What is a vector?")
    await tasks()
    async with client(ops) as http:
        response = await http.get(f"{ROUTE}/{message_id}", headers=AUTH)
    assert response.status_code == 200 and PRIVATE not in response.text and len(requests) == 2
    data = response.json()
    first, second = data["provider"]["calls"]
    assert (first["tier"], first["error"], first["http_status"]) == ("flash", "http", 503)
    assert (second["tier"], second["retry_kind"], second["outcome"]) == ("strong", "transport_failover", "completed")
    assert data["persistence"]["status"] == "completed"


@pytest.mark.asyncio
async def test_current_counts_distinguish_hits_from_returned_samples(ops, db_session, test_workspace):
    topic = "zzinventoryfixture"
    db_session.add_all([
        Question(workspace_id=test_workspace.id, short_code=f"ops-{index}", title=f"{topic} {index}",
                 qtype="open", topic=topic)
        for index in range(13)
    ])
    await db_session.commit()
    _, message_id, _ = await submit(db_session, test_workspace, f"show my library for {topic}")
    async with client(ops) as http:
        response = await http.get(f"{ROUTE}/{message_id}", headers=AUTH)
    assert response.status_code == 200 and topic not in response.text
    retrieval = response.json()["retrieval"]
    assert retrieval["state"] == "matched" and retrieval["evidence"] == "trace"
    assert retrieval["hits"]["questions"] == 13 and retrieval["returned"]["questions"] == 12


@pytest.mark.asyncio
async def test_lookup_accepts_a_completed_inventory_without_a_provider(
    ops, db_session, test_workspace,
):
    _, message_id, _ = await submit(db_session, test_workspace, "show my library for zzemptyfixture")
    reply = await db_session.get(StudyMessage, message_id)
    reply.status = "completed"
    reply.content = PRIVATE
    reply.coach = {**reply.coach, "library_inventory": {"total": 1, "items": [{"title": PRIVATE}]}, "trace": {
        **reply.coach["trace"], "stage": "completed",
    }}
    await db_session.commit()
    async with client(ops) as http:
        response = await http.get(f"{ROUTE}/{message_id}", headers=AUTH)
    assert response.status_code == 200 and PRIVATE not in response.text
    data = response.json()
    assert data["reply_kind"] == "library_inventory"
    assert data["provider"]["state"] == "not_called" and data["provider"]["calls"] == []
    assert data["routing"]["started_ms"] is None
    assert data["persistence"]["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_timing", [True, False])
@pytest.mark.parametrize("hits, shown", [(0, 0), (21, 12)])
async def test_lookup_projects_question_only_inventory_counts(
    ops, monkeypatch, db_session, test_workspace, capture_timing, hits, shown,
):
    async def sources(*_args):
        return [{"kind": "library_summary", "text": json.dumps({"题库命中": hits, "题库展示": shown})}]

    monkeypatch.setattr(study_chat, "library_sources", sources)
    _, message_id, _ = await submit(db_session, test_workspace, "Show questions from the question bank")
    reply = await db_session.get(StudyMessage, message_id)
    trace = {**reply.coach["trace"], "stage": "completed"}
    if not capture_timing:
        del trace["library_retrieval"]
    reply.status = "completed"
    reply.coach = {**reply.coach, "library_inventory": {"total": hits, "items": []}, "trace": trace}
    await db_session.commit()

    async with client(ops) as http:
        response = await http.get(f"{ROUTE}/{message_id}", headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data["reply_kind"] == "library_inventory"
    assert data["provider"]["state"] == "not_called"
    retrieval = data["retrieval"]
    assert retrieval["state"] == ("matched" if hits else "empty")
    assert retrieval["evidence"] == ("trace" if capture_timing else "saved_sources")
    assert retrieval["hits"] == {"questions": hits, "interview_sets": None, "notes": None}
    assert retrieval["returned"] == {"questions": shown, "interview_sets": None, "notes": None}
    if capture_timing:
        assert 0 <= retrieval["started_ms"] <= retrieval["finished_ms"]
    else:
        assert retrieval["started_ms"] is retrieval["finished_ms"] is None
