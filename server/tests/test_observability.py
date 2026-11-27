"""G1 / wave 0-B: structured logging, request correlation and rotation.

Pure unit checks — no database, no application lifespan, no network. The
middleware is driven directly as ASGI so the tests cannot accidentally touch a
personal instance or start a worker.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import logging.handlers
from contextlib import redirect_stdout

import pytest
import structlog

import app.observability as obs
from app.config import Settings


@pytest.fixture
def restored_logging():
    """Undo configure_logging()'s root/structlog mutations after each test."""
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    configured_before = obs._configured
    yield
    for handler in list(root.handlers):
        if handler not in handlers_before:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level_before)
    obs._configured = configured_before
    structlog.reset_defaults()


def _settings() -> Settings:
    return Settings(log_file="", log_level="INFO")


def _run_asgi(middleware_cls, headers, *, path="/api/v1/healthz", method="GET", body=b"{}"):
    """Drive a pure-ASGI middleware; return (status, response headers, state)."""
    messages = []
    state = {}

    async def receive_obj():
        return {"type": "http.request", "body": body, "more_body": False}

    async def downstream(scope, receive, send):
        # The middleware must pass receive through untouched: it never reads
        # request bodies (that is where the client idempotency request_id lives).
        state["receive_is_same"] = receive is receive_obj
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"token=secret&q=private",
        "root_path": "",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    asyncio.run(middleware_cls(downstream)(scope, receive_obj, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    return start["status"], response_headers, state


def test_safe_id_filter_accepts_only_strictly_safe_values():
    assert obs._accepted_id("abc-123.X_Y:z", generate=False) == "abc-123.X_Y:z"
    # Surrounding optional whitespace is normalized before validation.
    assert obs._accepted_id(" spaced ", generate=False) == "spaced"
    assert obs._accepted_id("has space", generate=False) == ""
    assert obs._accepted_id("'; DROP TABLE", generate=False) == ""
    assert obs._accepted_id("a" * 129, generate=False) == ""
    assert len(obs._accepted_id(None, generate=True)) == 32


def test_caller_request_id_is_accepted_and_echoed():
    status, headers, _ = _run_asgi(
        obs.RequestContextMiddleware,
        [("X-Request-ID", "caller-id-42"), ("X-Trace-ID", "trace-from-upstream")],
    )
    assert status == 204
    assert headers["x-request-id"] == "caller-id-42"


def test_request_id_is_generated_when_missing_or_unsafe():
    _, headers, _ = _run_asgi(obs.RequestContextMiddleware, [])
    assert len(headers["x-request-id"]) == 32
    assert obs._SAFE_ID.fullmatch(headers["x-request-id"])

    _, headers, _ = _run_asgi(
        obs.RequestContextMiddleware, [("X-Request-ID", "bad id with spaces")]
    )
    assert headers["x-request-id"] != "bad id with spaces"
    assert obs._SAFE_ID.fullmatch(headers["x-request-id"])


def test_middleware_never_reads_the_request_body():
    _, _, state = _run_asgi(obs.RequestContextMiddleware, [], body=b'{"request_id":"client-idem-99"}')
    assert state["receive_is_same"] is True


def test_json_logging_binds_context_for_structlog_and_stdlib(restored_logging):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        obs.configure_logging(_settings(), force=True)
        correlation_token = obs._correlation_id.set("corr-1")
        trace_token = obs._trace_id.set("trace-1")
        try:
            structlog.get_logger("careerpilot.test").info("sample.event", answer=42)
            logging.getLogger("legacy.stdlib").warning("legacy message %s", "arg")
        finally:
            obs._correlation_id.reset(correlation_token)
            obs._trace_id.reset(trace_token)

    events = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    by_event = {event["event"]: event for event in events}

    assert "sample.event" in by_event
    assert "legacy message arg" in by_event
    for event in by_event.values():
        assert event["correlation_id"] == "corr-1"
        assert event["trace_id"] == "trace-1"
        assert event["level"] in {"info", "warning"}
        assert "timestamp" in event
        assert "logger" in event
    assert by_event["sample.event"]["answer"] == 42


def test_access_log_is_json_and_leaks_no_sensitive_fields(restored_logging):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        obs.configure_logging(_settings(), force=True)
        _run_asgi(
            obs.RequestContextMiddleware,
            [("X-Request-ID", "server-corr-7")],
            path="/api/v1/study/chat",
            method="POST",
            body=b'{"request_id":"client-idem-99","api_key":"secret"}',
        )

    events = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    access = next(event for event in events if event["event"] == "http.request.completed")

    assert access["method"] == "POST"
    assert access["path"] == "/api/v1/study/chat"
    assert access["status"] == 204
    assert isinstance(access["duration_ms"], (int, float))
    assert access["correlation_id"] == "server-corr-7"
    # Reserved slot is present but never generated.
    assert access["trace_id"] == ""
    # No query string, headers, body, credentials, or client idempotency key.
    for forbidden in ("query", "token", "secret", "api_key", "request_id", "body", "headers"):
        assert forbidden not in access


def test_trace_id_is_propagated_but_never_generated(restored_logging):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        obs.configure_logging(_settings(), force=True)
        _run_asgi(
            obs.RequestContextMiddleware,
            [("X-Request-ID", "server-corr-8"), ("X-Trace-ID", "upstream-trace-3")],
        )
    events = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    access = next(event for event in events if event["event"] == "http.request.completed")
    assert access["trace_id"] == "upstream-trace-3"
    assert access["correlation_id"] == "server-corr-8"
    assert access["correlation_id"] != access["trace_id"]


def test_configure_logging_is_idempotent(restored_logging):
    settings = _settings()
    obs.configure_logging(settings, force=True)
    root = logging.getLogger()
    installed = sum(1 for h in root.handlers if getattr(h, "_careerpilot_handler", False))
    obs.configure_logging(settings)
    assert sum(1 for h in root.handlers if getattr(h, "_careerpilot_handler", False)) == installed


def test_rotating_file_handler_config(tmp_path):
    target = tmp_path / "nested" / "careerpilot.log"
    handler = obs.build_rotating_file_handler(target, max_bytes=4096, backup_count=3)
    try:
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == 4096
        assert handler.backupCount == 3
        assert handler.encoding == "utf-8"
        assert target.parent.is_dir()
    finally:
        handler.close()
