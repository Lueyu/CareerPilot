"""Allowlisted projection of one durable study-chat assistant message."""
import json
import math
from collections import Counter
from datetime import datetime

LIBRARY_FIELDS = {
    "questions": ("library_question", "题库命中"),
    "interview_sets": ("library_interview_set", "面经套题命中"),
    "notes": ("library_note", "学习资料命中"),
}
ERRORS = {
    "budget", "cancelled", "capability", "configuration", "generation", "http", "invalid_artifact",
    "invalid_response", "network", "stream", "timeout", "tool", "unavailable",
}
TIERS = {"flash", "strong"}
PROVIDERS = {"anthropic", "azure", "compatible", "deepseek", "gemini", "glm", "ollama", "openai", "openrouter", "qwen", "zhipu"}
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def object_from(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if isinstance(value, dict) else {}


def choice(value, options):
    return value if isinstance(value, str) and value in options else None


def count(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def milliseconds(value):
    if type(value) is float and math.isfinite(value) and 0 <= value <= 2**53 - 1:
        return value
    return count(value)


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat() if parsed.tzinfo is not None else None


def retrieval_projection(saved, sources_json):
    state = choice(saved.get("state"), {"searched", "not_requested"})
    evidence = None
    hits = dict.fromkeys(LIBRARY_FIELDS)
    returned = dict.fromkeys(LIBRARY_FIELDS)
    if state:
        evidence = "trace"
        hits = {key: count(object_from(saved.get("hits")).get(key)) for key in LIBRARY_FIELDS}
        returned = {key: count(object_from(saved.get("returned")).get(key)) for key in LIBRARY_FIELDS}
    elif sources_json:
        sources = json.loads(sources_json)
        sources = [item for item in sources if isinstance(item, dict)] if isinstance(sources, list) else []
        kinds = Counter(item["kind"] for item in sources if isinstance(item.get("kind"), str))
        summary = next((item for item in sources if item.get("kind") == "library_summary"), None)
        if summary is not None or any(kinds[kind] for kind, _ in LIBRARY_FIELDS.values()):
            evidence = "saved_sources"
            state = "searched"
            totals = object_from((summary or {}).get("text"))
            hits = {key: count(totals.get(label)) for key, (_, label) in LIBRARY_FIELDS.items()}
            returned = {key: kinds[kind] for key, (kind, _) in LIBRARY_FIELDS.items()}
    if state == "searched":
        numbers = [*hits.values(), *returned.values()]
        if any(numbers):
            state = "matched"
        elif all(total == 0 for total in hits.values()):
            state = "empty"
        else:
            state = "unknown"
    return {
        "state": state or "unknown",
        "evidence": evidence,
        "started_ms": milliseconds(saved.get("started_ms")) if evidence == "trace" else None,
        "finished_ms": milliseconds(saved.get("finished_ms")) if evidence == "trace" else None,
        "hits": hits,
        "returned": returned,
    }


def call_projection(call):
    usage = object_from(call.get("usage"))
    tokens = {key: count(usage.get(key)) for key in USAGE_FIELDS}
    status = count(call.get("http_status"))
    return {
        "attempt": count(call.get("attempt")),
        "generation": count(call.get("generation")),
        "provider": choice(call.get("provider"), PROVIDERS),
        "tier": choice(call.get("tier"), TIERS),
        "outcome": choice(call.get("outcome"), {"unknown", "completed", "failed"}),
        "error": choice(call.get("error"), ERRORS),
        "http_status": status if status is not None and 100 <= status <= 599 else None,
        "retry_kind": choice(call.get("retry_kind"), {"artifact_repair", "transport_failover"}),
        "fallback_blocked": choice(call.get("fallback_blocked"), {"answer_started"}),
        "provider_start_ms": milliseconds(call.get("provider_start_ms")),
        "provider_end_ms": milliseconds(call.get("provider_end_ms")),
        "elapsed_ms": milliseconds(call.get("elapsed_ms")),
        "first_visible_body_ms": milliseconds(call.get("first_visible_body_ms")),
        "usage": tokens if any(value is not None for value in tokens.values()) else None,
    }


def study_chat_projection(row):
    trace = object_from(row["trace"])
    timing = object_from(trace.get("latency"))
    attempt = row["attempt"]
    status = row["status"]
    is_library_inventory = status == "completed" and row["is_library_inventory"] is True
    stage = status if status != "generating" else choice(trace.get("stage"), {
        "queued", "starting", "retrieving", "routing", "generating", "escalating", "repairing",
    })
    raw_calls = trace.get("calls")
    calls = [call_projection(call) for call in raw_calls[-22:] if isinstance(call, dict)] if isinstance(raw_calls, list) else []
    if any(call["attempt"] == attempt for call in calls):
        provider_state = "recorded"
    elif is_library_inventory or stage == "queued" or milliseconds(timing.get("routing_finished_ms")) is not None:
        provider_state = "not_called"
    else:
        provider_state = "unknown"

    routing = {} if stage == "queued" else trace
    return {
        "message_id": row["id"],
        "reply_kind": "library_inventory" if is_library_inventory else None,
        "locale": choice(row["locale"], {"zh-CN", "en"}),
        "retrieval": retrieval_projection(object_from(trace.get("library_retrieval")), row["sources"]),
        "routing": {
            "timing_origin": choice(timing.get("origin"), {"message_request", "worker_start"}),
            "started_ms": milliseconds(timing.get("routing_started_ms")),
            "finished_ms": milliseconds(timing.get("routing_finished_ms")),
            "mode": choice(routing.get("mode"), TIERS | {"auto"}),
            "preferred_tier": choice(routing.get("preferred_tier"), TIERS),
            "initial_tier": choice(routing.get("initial_tier"), TIERS),
            "selected_tier": choice(routing.get("tier"), TIERS),
        },
        "provider": {
            "state": provider_state,
            "first_visible_body_ms": milliseconds(timing.get("first_visible_body_ms")),
            "body_release_stage": choice(timing.get("body_release_stage"), {"stream", "terminal"}),
            "calls": calls,
        },
        "persistence": {
            "status": status,
            "stage": stage,
            "attempt": attempt,
            "created_at": row["created_at"].isoformat(),
            "finalizing_ms": milliseconds(timing.get("finalizing_ms")),
            "terminal_write_at": timestamp(trace.get("terminal_write_at")),
            "error_kind": choice(trace.get("error_kind"), ERRORS),
        },
    }
