import asyncio
from argparse import Namespace
from contextlib import AsyncExitStack
import json
import socket
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import deps
from app.db import get_session
from app.main import app
from app.services import study_chat as worker
from bench_study_chat import PROMPTS, benchmark, run_turn


@pytest.mark.asyncio
async def test_benchmark_over_loopback_production_routes(monkeypatch, db_session, test_workspace):
    token = "synthetic-study-bench-token"
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def isolated_session():
        async with factory() as session:
            yield session

    async def config():
        return {"provider": "fixture", "api_key": "fixture", "base_url": "http://fixture.invalid/v1",
                "model": "fixture-model"}

    class FixtureAdapter:
        last_usage = None
        last_error = None
        last_response_meta = {}

        async def chat_with(self, cfg, instruction, request, *, on_content, on_reasoning, **kwargs):
            await on_reasoning("fixture reasoning")
            await on_content("  ")
            await asyncio.sleep(0.03)
            await on_content(" 固定夹具正文")
            await asyncio.sleep(0.03)
            return {"content": "   固定夹具正文"}

    monkeypatch.setitem(app.dependency_overrides, get_session, isolated_session)
    monkeypatch.setattr(deps, "get_settings", lambda: SimpleNamespace(api_token=token, workspace_token_hashes={}))
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "get_llm_config", config)
    monkeypatch.setattr(worker, "AgentAdapter", FixtureAdapter)
    monkeypatch.setenv("CAREERPILOT_BENCH_TOKEN", token)
    monkeypatch.setenv("CAREERPILOT_BENCH_WORKSPACE", test_workspace.slug)

    async with AsyncExitStack() as stack:
        urls, servers, tasks = [], [], []
        for _ in range(2):
            listener = stack.enter_context(socket.socket())
            listener.bind(("127.0.0.1", 0))
            urls.append(f"http://127.0.0.1:{listener.getsockname()[1]}")
            server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None, access_log=False))
            servers.append(server)
            tasks.append(asyncio.create_task(server.serve(sockets=[listener])))
        try:
            async with asyncio.timeout(5):
                while not all(server.started for server in servers):
                    await asyncio.sleep(0.01)
            args = Namespace(baseline_url=urls[0], candidate_url=urls[1], kind="fixture", auth="native", mode="flash",
                             repeats=2, timeout=10, baseline_label="same_candidate_a", candidate_label="same_candidate_b")
            report = await benchmark(args)
            assert report["kind"] == "fixture"
            for side in ("baseline", "candidate"):
                assert report["summary"][side]["all"]["outcomes"] == {"ok": 4}
                assert report["summary"][side]["all"]["ttft_ms"]["n"] == 4
            assert all(value["pairs_completed"] == 2 for value in report["pairs"].values())
            assert [(sample["repeat"], sample["side"]) for sample in report["samples"] if sample["prompt"] == "explain"] == [
                (1, "baseline"), (1, "candidate"), (2, "candidate"), (2, "baseline"),
            ]
            assert all(0 <= sample["post_ms"] <= sample["ttft_ms"] < sample["duration_ms"] for sample in report["samples"])
            async with httpx.AsyncClient(base_url=urls[0], trust_env=False,
                                         headers={"X-Api-Token": token, "X-Workspace": test_workspace.slug}) as client:
                for sample in report["samples"]:
                    response = await client.get(f"/api/v1/study/sessions/{sample['session_id']}")
                    response.raise_for_status()
                    prompt, reply = response.json()["messages"]
                    assert prompt["content"] == PROMPTS[sample["prompt"]]
                    assert reply["status"] == "completed" and "固定夹具正文" in reply["content"]
                    assert reply["id"] == sample["message_id"]
                    latency = reply["trace"]["latency"]
                    assert latency["origin"] == "message_request"
                    order = ("accepted_ms", "worker_start_ms", "context_ready_ms", "provider_start_ms",
                             "first_visible_body_ms", "finalizing_ms")
                    assert [latency[key] for key in order] == sorted(latency[key] for key in order)
                    assert "fixture reasoning" not in json.dumps(reply)
            print("same-candidate HTTP fixture summary", json.dumps(report["summary"]))
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks)


@pytest.mark.asyncio
@pytest.mark.parametrize(("terminal", "outcome", "error"), [
    ("stall", "timeout", "stream_timeout"),
    ("failed", "failed", "failed"),
    ("eof", "failed", "missing_terminal"),
])
async def test_benchmark_keeps_failed_samples_and_ignores_nonbody_frames(terminal, outcome, error):
    paths = []

    class Nonbody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b': heartbeat\r\n\r\ndata: {"id": 2, "status": "generating", "content": " ", "reasoning": "fixture"}\r\n\r\n'
            if terminal == "failed":
                yield b'data: {"id": 2, "status": "failed", "content": ""}\n\n'
            while terminal == "stall":
                await asyncio.sleep(0.01)
                yield b': heartbeat\n\n'

    async def request(req):
        path = req.url.path
        paths.append(path)
        if path == "/sessions":
            return httpx.Response(201, json={"id": 1})
        if path.endswith("/messages"):
            return httpx.Response(202, json={"assistant_message_id": 2})
        if path.endswith("/interrupt"):
            return httpx.Response(200)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Nonbody())

    async with httpx.AsyncClient(base_url="http://fixture.invalid", transport=httpx.MockTransport(request)) as client:
        trial = await run_turn(client, "/sessions", "fixture prompt", 0.15, "auto")
    assert trial["outcome"] == outcome and trial["error"] == error
    assert trial["ttft_ms"] is None and trial["duration_ms"] >= 0
    assert paths[-1].endswith("/interrupt") == (terminal != "failed")
