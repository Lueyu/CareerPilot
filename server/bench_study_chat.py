"""Compare study chat over HTTP on two isolated, synthetic CareerPilot instances."""

import argparse
import asyncio
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
from time import perf_counter
from uuid import uuid4

import httpx


PROMPTS = {
    "explain": "请只用纯文字解释什么是向量检索，举一个例子，控制在 150 字以内。",
    "tradeoff": "请只用纯文字比较关键词检索和向量检索的取舍，并说明各适合哪种场景，控制在 200 字以内。",
}
TERMINAL = {"completed", "failed", "interrupted"}
BLOCK_FENCE = re.compile(r"(?:^|\n)```study-blocks\s*(?:\n|$)")
THINKING_CHOICES = ("unspecified", "enabled", "disabled")
MIN_PAIRED_SAMPLES = 5
CLAIM_POLICY = {
    "scope": "paired HTTP trials on fixed prompts and declared configurations only",
    "rule": "Report latency, errors, tokens and answer quality together; never infer an overall speedup from one metric.",
    "thinking": "Declare STUDY_FLASH_THINKING for both sides; an HTTP client cannot read server-side settings.",
    "quality": "Answer quality is not machine-scored; body_chars/body_sha256 are anchors for blinded human review.",
}


class BenchFailure(Exception):
    pass


def env(side, key):
    suffix = f"_{key}"
    return os.environ.get(f"CAREERPILOT_BENCH_{side.upper()}{suffix}") or os.environ.get(f"CAREERPILOT_BENCH{suffix}")


async def connect(client, side, auth):
    if auth == "native":
        token, workspace = env(side, "TOKEN"), env(side, "WORKSPACE")
        if not token or not workspace:
            raise BenchFailure("missing_native_credentials")
        client.headers.update({"X-Api-Token": token, "X-Workspace": workspace})
        return "/api/v1/study/sessions"

    username, password = env(side, "USERNAME"), env(side, "PASSWORD")
    if not username or not password:
        raise BenchFailure("missing_registered_credentials")
    client.headers["Origin"] = str(client.base_url).rstrip("/")
    response = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    client.headers["X-Expected-Workspace"] = str(response.json()["workspace_id"])
    return "/api/v1/native/study/sessions"


async def snapshots(response):
    data = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                frame = json.loads("\n".join(data))
                if not isinstance(frame, dict):
                    raise BenchFailure("invalid_snapshot")
                yield frame
                data.clear()
        elif line.startswith("data:") or line == "data":
            data.append(line[5:].removeprefix(" "))


def visible_body(frame):
    content = frame.get("content")
    if not isinstance(content, str):
        raise BenchFailure("invalid_content")
    # StudyText hides the structured block payload until the turn finishes.
    fence = BLOCK_FENCE.search(content) if frame.get("status") == "generating" else None
    return bool((content[:fence.start()] if fence else content).strip())


def elapsed_ms(started):
    return round((perf_counter() - started) * 1000, 2)


async def run_turn(client, path, prompt, timeout, mode):
    result = dict(outcome="failed", error=None, ttft_ms=None, duration_ms=None, post_ms=None,
                  stream_headers_ms=None, terminal=None, model=None, session_id=None, message_id=None,
                  total_tokens=None, call_count=None, thinking_modes=None, tiers=None,
                  body_chars=None, body_sha256=None, trace_status=None)
    base = None
    reply_id = None
    request_id = str(uuid4())
    started = perf_counter()
    phase = "create"
    try:
        async with asyncio.timeout(timeout):
            response = await client.post(path, json={"source_type": "free", "mode": mode, "topic": prompt})
            response.raise_for_status()
            result["session_id"] = int(response.json()["id"])
            base = f'{path}/{result["session_id"]}/messages'
        started = perf_counter()
        async with asyncio.timeout(timeout):
            phase = "submit"
            response = await client.post(base, json={"content": prompt, "mode": mode, "request_id": request_id})
            response.raise_for_status()
            reply_id = result["message_id"] = int(response.json()["assistant_message_id"])
            result["post_ms"] = elapsed_ms(started)
            phase = "stream"
            async with client.stream("GET", f"{base}/{reply_id}/stream", headers={"Accept": "text/event-stream"}) as response:
                result["stream_headers_ms"] = elapsed_ms(started)
                response.raise_for_status()
                if response.headers.get("content-type", "").split(";", 1)[0] != "text/event-stream":
                    raise BenchFailure("expected_sse")
                async for frame in snapshots(response):
                    if frame.get("id") != reply_id or frame.get("status") not in TERMINAL | {"generating"}:
                        raise BenchFailure("invalid_snapshot")
                    if visible_body(frame) and result["ttft_ms"] is None:
                        result["ttft_ms"] = elapsed_ms(started)
                    status = frame["status"]
                    if status in TERMINAL:
                        result.update(terminal=status, model=frame.get("model"), duration_ms=elapsed_ms(started))
                        if status != "completed":
                            raise BenchFailure(status)
                        if not visible_body(frame):
                            raise BenchFailure("empty_completion")
                        result["outcome"] = "ok"
                        break
                else:
                    raise BenchFailure("missing_terminal")
    except (TimeoutError, httpx.TimeoutException):
        result.update(outcome="timeout", error=f"{phase}_timeout")
    except httpx.HTTPStatusError as exc:
        result["error"] = f"{phase}_http_{exc.response.status_code}"
    except httpx.RequestError:
        result["error"] = f"{phase}_transport"
    except BenchFailure as exc:
        result["error"] = str(exc)
    except (ValueError, KeyError, TypeError):
        result["error"] = f"{phase}_invalid_response"
    if result["duration_ms"] is None:
        result["duration_ms"] = elapsed_ms(started)
    # Closing SSE deliberately leaves the generator running. Interrupt a failed sample
    # before the next pair so it doesn't keep using provider capacity in the background.
    if base and result["terminal"] is None:
        try:
            async with asyncio.timeout(3):
                if reply_id is None:
                    response = await client.get(f'{path}/{result["session_id"]}')
                    response.raise_for_status()
                    reply_id = next((m["id"] for m in response.json()["messages"]
                                     if m.get("role") == "assistant" and m.get("request_id") == request_id), None)
                if reply_id is not None:
                    result["message_id"] = reply_id
                    response = await client.post(f"{base}/{reply_id}/interrupt")
                    result["cleanup"] = response.status_code
                else:
                    result["cleanup"] = "reply_not_found"
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError):
            result["cleanup"] = "unconfirmed"
    return result


def usage_tokens(usage):
    if not isinstance(usage, dict):
        return None
    total = usage.get("total_tokens")
    if type(total) is int and total >= 0:
        return total
    parts = [usage.get(key) for key in ("prompt_tokens", "completion_tokens")]
    if all(type(value) is int and value >= 0 for value in parts):
        return sum(parts)
    return None


def body_fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def enrich_samples(client, path, samples):
    """Best-effort durable trace lookup; never rewrites a sample's outcome."""
    for sample in samples:
        if not sample.get("session_id") or not sample.get("message_id"):
            sample["trace_status"] = "no_reply"
            continue
        try:
            async with asyncio.timeout(3):
                response = await client.get(f'{path}/{sample["session_id"]}')
                response.raise_for_status()
                messages = response.json()["messages"]
            reply = next((item for item in messages if item.get("id") == sample["message_id"]), None)
            if not isinstance(reply, dict):
                sample["trace_status"] = "reply_not_found"
                continue
            calls = [call for call in ((reply.get("trace") or {}).get("calls") or []) if isinstance(call, dict)]
            tokens = [value for value in (usage_tokens(call.get("usage")) for call in calls) if value is not None]
            sample["call_count"] = len(calls)
            sample["tiers"] = [call.get("tier") for call in calls]
            sample["thinking_modes"] = [call.get("thinking_mode") for call in calls]
            sample["total_tokens"] = sum(tokens) if tokens else None
            body = reply.get("content") or ""
            sample["body_chars"] = len(body.strip())
            sample["body_sha256"] = body_fingerprint(body)
            sample["trace_status"] = reply.get("status")
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            sample["trace_status"] = "unavailable"
    return samples


def stats(values):
    ordered = sorted(value for value in values if value is not None)
    n = len(ordered)
    return {"n": n, "min": ordered[0] if n else None, "max": ordered[-1] if n else None,
            **{f"p{p}": ordered[math.ceil(n * p / 100) - 1] if n else None for p in (50, 90, 95)}}


def summarize(samples):
    success = [sample for sample in samples if sample["outcome"] == "ok"]
    return {
        "n": len(samples), "outcomes": dict(Counter(s["outcome"] for s in samples)),
        "failure_reasons": dict(Counter(s["error"] for s in samples if s["error"])),
        "ttft_ms": stats(s["ttft_ms"] for s in success),
        "duration_ms": stats(s["duration_ms"] for s in success),
        "ttft_all_visible_ms": stats(s["ttft_ms"] for s in samples),
        "duration_all_ms": stats(s["duration_ms"] for s in samples),
        "total_tokens": stats(s.get("total_tokens") for s in samples),
        "body_chars": stats(s.get("body_chars") for s in samples),
        "call_count": stats(s.get("call_count") for s in samples),
        "trace_status": dict(Counter(s.get("trace_status") or "missing" for s in samples)),
    }


def paired_summary(samples):
    result = {}
    for prompt in PROMPTS:
        pairs = {}
        for sample in samples:
            if sample["prompt"] == prompt:
                pairs.setdefault(sample["repeat"], {})[sample["side"]] = sample
        matched, discarded = [], []
        for pair in pairs.values():
            if len(pair) != 2:
                discarded.append("missing_side")
                continue
            failures = [trial["outcome"] for trial in pair.values() if trial["outcome"] != "ok"]
            if failures:
                discarded.extend(f"outcome_{value}" for value in failures)
                continue
            matched.append((pair["baseline"], pair["candidate"]))
        result[prompt] = {
            "pairs_attempted": len(pairs), "pairs_completed": len(matched),
            "pairs_discarded": len(pairs) - len(matched),
            "discarded_reasons": dict(Counter(discarded)),
            "candidate_minus_baseline_ttft_ms": stats(round(b["ttft_ms"] - a["ttft_ms"], 2) for a, b in matched),
            "candidate_minus_baseline_duration_ms": stats(round(b["duration_ms"] - a["duration_ms"], 2) for a, b in matched),
            "candidate_minus_baseline_total_tokens": stats(
                b["total_tokens"] - a["total_tokens"] for a, b in matched
                if type(a.get("total_tokens")) is int and type(b.get("total_tokens")) is int),
        }
    return result


def claim_assessment(report):
    """Guard against turning one favourable percentile into an overall speedup claim."""
    reasons = []
    thinking = report["thinking_config"]
    if any(value == "unspecified" for value in thinking.values()):
        reasons.append("thinking_config_unspecified")
    if thinking["baseline"] != thinking["candidate"]:
        reasons.append("thinking_config_mismatch")
    failed = sum(count for side in report["summary"].values()
                 for outcome, count in side["all"]["outcomes"].items() if outcome != "ok")
    if failed:
        reasons.append("failed_or_timed_out_samples")
    directions = set()
    for value in report["pairs"].values():
        if value["pairs_completed"] < MIN_PAIRED_SAMPLES:
            reasons.append("too_few_completed_pairs")
        for key in ("candidate_minus_baseline_ttft_ms", "candidate_minus_baseline_duration_ms"):
            median = value[key]["p50"]
            if median is None:
                reasons.append("missing_paired_median")
            else:
                directions.add(1 if median > 0 else -1 if median < 0 else 0)
    if len(directions) > 1:
        reasons.append("mixed_metric_direction")
    return {
        "status": "insufficient_evidence" if reasons else "paired_direction_only",
        "reasons": sorted(set(reasons)),
        "candidate_direction": ({1: "slower", -1: "faster", 0: "unchanged"}.get(next(iter(directions)))
                                if len(directions) == 1 else "mixed" if directions else "unknown"),
        "note": ("Only the paired fixed prompts under declared configurations are described. "
                 "This is not evidence of an overall speedup: report token/cost deltas alongside "
                 "latency and errors, and have answer quality reviewed blind before any default change."),
    }


async def benchmark(args):
    async with (
        httpx.AsyncClient(base_url=args.baseline_url, timeout=args.timeout, trust_env=False) as baseline,
        httpx.AsyncClient(base_url=args.candidate_url, timeout=args.timeout, trust_env=False) as candidate,
    ):
        clients = {"baseline": baseline, "candidate": candidate}
        paths = {side: await connect(client, side, args.auth) for side, client in clients.items()}
        samples = []
        for repeat in range(args.repeats):
            for prompt_id, prompt in PROMPTS.items():
                order = list(clients) if repeat % 2 == 0 else list(reversed(clients))
                for side in order:
                    sample = await run_turn(clients[side], paths[side], prompt, args.timeout, args.mode)
                    sample.update(side=side, prompt=prompt_id, repeat=repeat + 1, pair_order=order.index(side) + 1)
                    samples.append(sample)
                    print(f"{side} {prompt_id} #{repeat + 1} {sample['outcome']}", flush=True)
        for side, client in clients.items():
            await enrich_samples(client, paths[side], [item for item in samples if item["side"] == side])
    summary = {}
    for side in clients:
        trials = [sample for sample in samples if sample["side"] == side]
        summary[side] = {"all": summarize(trials), "prompts": {
            key: summarize([trial for trial in trials if trial["prompt"] == key]) for key in PROMPTS}}
    report = {"kind": args.kind, "mode": args.mode, "auth": args.auth, "repeats_per_prompt": args.repeats,
              "timeout_seconds": args.timeout, "percentile_method": "nearest_rank",
              "labels": {"baseline": args.baseline_label, "candidate": args.candidate_label},
              "thinking_config": {"baseline": getattr(args, "baseline_thinking", "unspecified"),
                                  "candidate": getattr(args, "candidate_thinking", "unspecified")},
              "claim_policy": CLAIM_POLICY, "prompts": PROMPTS, "summary": summary,
              "pairs": paired_summary(samples), "samples": samples}
    report["claim_assessment"] = claim_assessment(report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-url", required=True)
    parser.add_argument("--candidate-url", required=True)
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--candidate-label", default="candidate")
    parser.add_argument("--kind", choices=("fixture", "real"), required=True)
    parser.add_argument("--auth", choices=("native", "registered"), default="native")
    parser.add_argument("--mode", choices=("auto", "flash", "strong"), default="auto")
    parser.add_argument("--baseline-thinking", choices=THINKING_CHOICES, default="unspecified",
                        help="STUDY_FLASH_THINKING configured on the baseline instance")
    parser.add_argument("--candidate-thinking", choices=THINKING_CHOICES, default="unspecified",
                        help="STUDY_FLASH_THINKING configured on the candidate instance")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("repeats and timeout must be positive")
    urls = [httpx.URL(value) for value in (args.baseline_url, args.candidate_url)]
    if urls[0] == urls[1] or any(url.scheme not in ("http", "https") or not url.host or url.userinfo
                                or url.path != "/" or url.query or url.fragment for url in urls):
        parser.error("provide two distinct HTTP origins without credentials, paths, or queries")
    try:
        report = asyncio.run(benchmark(args))
    except BenchFailure as exc:
        parser.exit(2, f"Benchmark setup failed ({exc}).\n")
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Benchmark setup failed ({type(exc).__name__}).\n")
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assessment = report["claim_assessment"]
    print(f'claim: {assessment["status"]} ({", ".join(assessment["reasons"]) or "no blockers"})', flush=True)
    return int(any(sample["outcome"] != "ok" for sample in report["samples"]))


if __name__ == "__main__":
    raise SystemExit(main())
