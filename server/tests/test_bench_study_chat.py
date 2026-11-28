import asyncio
import json
import socket
from argparse import Namespace
from collections import Counter
from contextlib import AsyncExitStack

import httpx
import pytest
import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_session
from app.main import app
from app.services import study_chat as worker
from bench_study_chat import (
    PROMPTS,
    benchmark,
    body_fingerprint,
    claim_assessment,
    enrich_samples,
    paired_summary,
    run_turn,
    summarize,
    usage_tokens,
)


@pytest.mark.asyncio
async def test_benchmark_over_loopback_production_routes(monkeypatch, db_session, test_workspace, synthetic_generation_quota):
    from secrets import token_urlsafe

    from credential_fixtures import enroll_token
    token = token_urlsafe(32)
    await enroll_token(db_session, token)
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
            assert all(0 <= sample["post_ms"] <= sample["ttft_ms"] <= sample["duration_ms"] for sample in report["samples"]), [
                {key: sample[key] for key in ("post_ms", "ttft_ms", "duration_ms")}
                for sample in report["samples"] if not 0 <= sample["post_ms"] <= sample["ttft_ms"] <= sample["duration_ms"]
            ]
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


def sample(side, prompt, repeat, *, outcome="ok", ttft=100.0, duration=200.0,
           total_tokens=None, body_chars=None):
    return dict(side=side, prompt=prompt, repeat=repeat, outcome=outcome, error=None,
                ttft_ms=ttft, duration_ms=duration, total_tokens=total_tokens,
                body_chars=body_chars, call_count=1, trace_status="completed")


def paired_samples(ttft_delta=-10, duration_delta=-20):
    return [sample(side, prompt, repeat, **({"ttft": 100.0 + ttft_delta, "duration": 200.0 + duration_delta}
                                            if side == "candidate" else {}))
            for repeat in range(1, 6) for prompt in PROMPTS for side in ("baseline", "candidate")]


def minimal_report(samples, *, baseline_thinking="disabled", candidate_thinking="disabled"):
    summary = {}
    for side in ("baseline", "candidate"):
        trials = [item for item in samples if item["side"] == side]
        summary[side] = {"all": {"outcomes": dict(Counter(item["outcome"] for item in trials))}}
    return {"thinking_config": {"baseline": baseline_thinking, "candidate": candidate_thinking},
            "summary": summary, "pairs": paired_summary(samples)}


def test_claim_assessment_allows_only_a_paired_direction():
    assessment = claim_assessment(minimal_report(paired_samples()))
    assert assessment["status"] == "paired_direction_only"
    assert assessment["candidate_direction"] == "faster"
    assert "not evidence of an overall speedup" in assessment["note"]


def test_claim_assessment_blocks_undeclared_thinking_and_failed_samples():
    samples = paired_samples()
    blocked = claim_assessment(minimal_report(samples, baseline_thinking="unspecified"))
    assert blocked["status"] == "insufficient_evidence"
    assert {"thinking_config_unspecified", "thinking_config_mismatch"} <= set(blocked["reasons"])

    with_timeout = [{**item, "outcome": "timeout"} if item["side"] == "candidate" and item["repeat"] == 1
                    else item for item in samples]
    failed = claim_assessment(minimal_report(with_timeout))
    assert failed["status"] == "insufficient_evidence"
    assert {"failed_or_timed_out_samples", "too_few_completed_pairs"} <= set(failed["reasons"])


def test_claim_assessment_blocks_mixed_metric_directions():
    assessment = claim_assessment(minimal_report(paired_samples(ttft_delta=-10, duration_delta=20)))
    assert assessment["status"] == "insufficient_evidence"
    assert "mixed_metric_direction" in assessment["reasons"]
    assert assessment["candidate_direction"] == "mixed"


def test_paired_summary_reports_discarded_pairs():
    samples = [sample("baseline", "explain", 1), sample("candidate", "explain", 1, outcome="timeout"),
               sample("baseline", "tradeoff", 1)]
    pairs = paired_summary(samples)
    assert pairs["explain"]["pairs_attempted"] == 1
    assert pairs["explain"]["pairs_completed"] == 0
    assert pairs["explain"]["pairs_discarded"] == 1
    assert pairs["explain"]["discarded_reasons"] == {"outcome_timeout": 1}
    assert pairs["tradeoff"]["discarded_reasons"] == {"missing_side": 1}


def test_summarize_reports_tokens_and_body_anchors():
    summary = summarize([sample("baseline", "explain", index, total_tokens=100 + index,
                                body_chars=50 + index) for index in range(3)])
    assert summary["total_tokens"]["n"] == 3 and summary["total_tokens"]["p50"] == 101
    assert summary["body_chars"]["p50"] == 51
    assert summary["trace_status"] == {"completed": 3}


def test_usage_tokens_prefers_total_and_falls_back_to_parts():
    assert usage_tokens({"total_tokens": 120, "prompt_tokens": 999}) == 120
    assert usage_tokens({"prompt_tokens": 30, "completion_tokens": 20}) == 50
    assert usage_tokens(None) is None
    assert usage_tokens({"total_tokens": "120"}) is None


@pytest.mark.asyncio
async def test_enrich_samples_reads_durable_usage_without_changing_outcome():
    body = "固定夹具正文"
    calls = [{"tier": "flash", "thinking_mode": "disabled", "usage": {"total_tokens": 120}},
             {"tier": "strong", "thinking_mode": None, "usage": {"prompt_tokens": 30, "completion_tokens": 20}}]

    def handler(request):
        return httpx.Response(200, json={"messages": [
            {"id": 1, "role": "user", "content": "question"},
            {"id": 2, "role": "assistant", "status": "completed", "content": body, "trace": {"calls": calls}},
        ]})

    trial = {"session_id": 7, "message_id": 2, "outcome": "ok"}
    async with httpx.AsyncClient(base_url="http://fixture.invalid", transport=httpx.MockTransport(handler)) as client:
        await enrich_samples(client, "/sessions", [trial])
    assert trial["total_tokens"] == 170
    assert trial["thinking_modes"] == ["disabled", None]
    assert trial["tiers"] == ["flash", "strong"]
    assert trial["body_chars"] == len(body.strip())
    assert trial["body_sha256"] == body_fingerprint(body)
    assert trial["trace_status"] == "completed"
    assert trial["outcome"] == "ok"


@pytest.mark.asyncio
async def test_enrich_samples_marks_unavailable_on_http_error():
    trial = {"session_id": 7, "message_id": 2, "outcome": "timeout"}
    async with httpx.AsyncClient(base_url="http://fixture.invalid",
                                 transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        await enrich_samples(client, "/sessions", [trial])
    assert trial["trace_status"] == "unavailable"
    assert trial["outcome"] == "timeout"

pytestmark = pytest.mark.usefixtures("mock_private_billing")
