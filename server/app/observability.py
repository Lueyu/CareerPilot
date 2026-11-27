"""Structured JSON logging, request correlation and log rotation (G1 / wave 0-B).

Naming contract — do not collapse these three ids
--------------------------------------------------
* ``correlation_id``  the SERVER's per-request correlation id, carried in the
  ``X-Request-ID`` HTTP header. Accepted from the caller when safe, generated
  otherwise. This is the only request id bound to every log line.
* ``trace_id``        a RESERVED slot for a future distributed tracing system.
  Never generated here; copied from ``X-Trace-ID`` when present, otherwise an
  empty string. The key is always present so downstream consumers can rely on
  it without a schema change when tracing lands.
* ``request_id``      the CLIENT's idempotency key in request bodies (study
  chat, study notes, bank import, public coach, ...). It is deliberately NOT
  read, bound, or logged here: it is business data with different lifetime and
  uniqueness scope, and must never be confused with ``correlation_id``.

Only method, path and status are logged for access records — never query
strings, headers, request bodies or credentials.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys
import time
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings, get_settings

REQUEST_ID_HEADER = "x-request-id"
TRACE_ID_HEADER = "x-trace-id"
RESPONSE_REQUEST_ID_HEADER = "X-Request-ID"

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SERVER_ROOT = Path(__file__).resolve().parents[1]

_correlation_id: ContextVar[str] = ContextVar("careerpilot_correlation_id", default="")
_trace_id: ContextVar[str] = ContextVar("careerpilot_trace_id", default="")

_configured = False


def new_correlation_id() -> str:
    """Generate a fresh server-side correlation id (uuid4 hex)."""
    return uuid4().hex


def current_correlation_id() -> str:
    return _correlation_id.get()


def current_trace_id() -> str:
    return _trace_id.get()


def _accepted_id(raw: str | None, *, generate: bool) -> str:
    """Accept only a strictly safe id; never let header content into logs."""
    candidate = (raw or "").strip()
    if candidate and _SAFE_ID.fullmatch(candidate):
        return candidate
    return new_correlation_id() if generate else ""


def _add_request_context(logger, method_name, event_dict):
    """Bind the server correlation id and the reserved trace id to every event."""
    event_dict["correlation_id"] = _correlation_id.get()
    event_dict["trace_id"] = _trace_id.get()
    return event_dict


def _pre_chain():
    # Shared by structlog-originated events and foreign stdlib LogRecords, so
    # both render with the same JSON shape. `remove_processors_meta` is left
    # out on purpose: it raises on foreign records (no `_record` key).
    return [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        _add_request_context,
    ]


def build_rotating_file_handler(
    log_file: str | Path,
    *,
    max_bytes: int,
    backup_count: int,
) -> logging.handlers.RotatingFileHandler:
    """Create the size-rotating JSON log handler used in production."""
    path = Path(log_file)
    if not path.is_absolute():
        path = _SERVER_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    return handler


def configure_logging(settings: Settings | None = None, *, force: bool = False) -> None:
    """Configure structlog JSON output plus non-blocking-safe rotating file.

    Idempotent by default. Only handlers installed by this function are ever
    removed, so pytest's own handlers and third-party handlers are preserved.
    """
    global _configured
    if _configured and not force:
        return
    settings = settings or get_settings()
    chain = _pre_chain()
    structlog.configure(
        processors=[*chain, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=chain,
    )

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_careerpilot_handler", False):
            root.removeHandler(existing)
            existing.close()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream._careerpilot_handler = True
    root.addHandler(stream)

    if settings.log_file:
        rotating = build_rotating_file_handler(
            settings.log_file,
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backup_count,
        )
        rotating.setFormatter(formatter)
        rotating._careerpilot_handler = True
        root.addHandler(rotating)

    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    _configured = True


class RequestContextMiddleware:
    """Bind server correlation ids and emit one structured access log per request.

    Pure-ASGI middleware (same shape as ``LanguageMiddleware``) so it works for
    both the private and the public application without FastAPI dependencies.
    """

    def __init__(self, app: ASGIApp, *, log_requests: bool = True):
        self.app = app
        self.log_requests = log_requests

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = Headers(scope=scope)
        correlation_id = _accepted_id(headers.get(REQUEST_ID_HEADER), generate=True)
        # Reserved: never generated, only propagated when explicitly supplied.
        trace_id = _accepted_id(headers.get(TRACE_ID_HEADER), generate=False)

        status_code = 0
        started = time.perf_counter()

        async def send_with_context(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                outgoing = MutableHeaders(scope=message)
                outgoing[RESPONSE_REQUEST_ID_HEADER] = correlation_id
            await send(message)

        correlation_token = _correlation_id.set(correlation_id)
        trace_token = _trace_id.set(trace_id)
        try:
            await self.app(scope, receive, send_with_context)
        finally:
            if self.log_requests:
                structlog.get_logger("careerpilot.request").info(
                    "http.request.completed",
                    method=scope.get("method", ""),
                    path=scope.get("path", ""),
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            _correlation_id.reset(correlation_token)
            _trace_id.reset(trace_token)
