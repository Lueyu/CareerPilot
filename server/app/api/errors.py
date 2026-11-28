"""HTTP error contract. Correlation IDs belong to transport, never business input."""
import logging
import os
from collections import Counter
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi.exceptions import RequestValidationError
from fastapi.utils import is_body_allowed_for_status_code
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response

from app.i18n import negotiated_language

logger = logging.getLogger(__name__)
_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
          405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE",
          422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 500: "INTERNAL_ERROR",
          502: "BAD_GATEWAY", 503: "SERVICE_UNAVAILABLE", 504: "GATEWAY_TIMEOUT"}


def request_id(scope) -> str:
    state = scope.setdefault("state", {})
    if "request_id" not in state:
        state["request_id"] = uuid4().hex
    return state["request_id"]


def error_payload(scope, status: int, detail) -> dict:
    code = _CODES.get(status, "HTTP_ERROR")
    english = negotiated_language(Headers(scope=scope).get("accept-language", "")) == "en"
    message = "Request failed." if english else "请求未能完成。"
    if status == 422:
        message = "Invalid request." if english else "请求参数格式不正确。"
    elif status == 500:
        message = "Internal server error. Please try again later." if english else "服务暂时出错，请稍后重试。"
    elif isinstance(detail, str):
        message = detail
    elif isinstance(detail, dict):
        code = detail.get("code", code)
        message = detail.get("message", message)
    return {"detail": detail, "code": code, "message": message, "request_id": request_id(scope)}


def error_response(scope, status: int, detail, headers=None) -> JSONResponse:
    return JSONResponse(error_payload(scope, status, detail), status_code=status, headers=headers)


async def http_error(request: Request, exc: HTTPException):
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=exc.headers)
    return error_response(request.scope, exc.status_code, exc.detail, exc.headers)


async def validation_error(request: Request, exc: RequestValidationError):
    # Keep FastAPI's list shape/locations/messages, without echoing input or ctx values.
    detail = []
    for item in exc.errors()[:50]:
        kind = str(item.get("type", "validation_error"))[:80]
        location = [part if isinstance(part, int) else str(part)[:80] for part in item.get("loc", ())[:16]]
        message = {
            "missing": "Field required", "int_parsing": "Input should be a valid integer",
            "float_parsing": "Input should be a valid number", "bool_parsing": "Input should be a valid boolean",
            "json_invalid": "Invalid JSON", "string_type": "Input should be a valid string",
            "list_type": "Input should be a valid list", "dict_type": "Input should be a valid dictionary",
        }.get(kind, "Invalid value")
        detail.append({"type": kind, "loc": location, "msg": message})
    return error_response(request.scope, 422, detail)


class ErrorCounts:
    def __init__(self):
        self.started_at = datetime.now(UTC).isoformat()
        self._counts = Counter()
        self._lock = Lock()

    def add(self, category):
        with self._lock:
            self._counts[category] += 1

    def snapshot(self):
        with self._lock:
            return {"scope": "application_process", "pid": os.getpid(), "started_at": self.started_at,
                    "reset": "process restart", "durable": False, "counts": dict(self._counts)}


class ResponsePhase(Enum):
    BEFORE_START = "before_start"
    STARTED = "started"
    COMPLETED = "completed"


class StreamAborted(RuntimeError):
    """Fixed safe failure for the ASGI server to close an incomplete response."""


class ErrorBoundary:
    def __init__(self, app, *, counts, response_policy):
        self.app = app
        self.counts = counts
        self.response_policy = response_policy

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        phase = ResponsePhase.BEFORE_START
        disconnected = False
        transport_failed = False

        async def observed_receive():
            nonlocal disconnected, transport_failed
            try:
                message = await receive()
            except OSError:
                disconnected = True
                transport_failed = True
                raise
            disconnected |= message["type"] == "http.disconnect"
            return message

        async def observed_send(message):
            nonlocal phase, disconnected, transport_failed
            if message["type"] == "http.response.start":
                phase = ResponsePhase.STARTED
            try:
                await send(message)
            except OSError:
                disconnected = True
                transport_failed = True
                raise
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                phase = ResponsePhase.COMPLETED

        abort_stream = False
        abort_disconnect = False
        try:
            await self.app(scope, observed_receive, observed_send)
        except ClientDisconnect:
            abort_disconnect = True
        except Exception as exc:
            if disconnected:
                abort_disconnect = True
            if not transport_failed:
                # No repr, message, URL, body, headers, traceback text, locals or source lines.
                location = exc.__traceback__
                while location is not None and location.tb_next is not None:
                    location = location.tb_next
                filename = Path(location.tb_frame.f_code.co_filename).name if location else "unknown"
                line = location.tb_lineno if location else 0
                fault_phase = "disconnected" if disconnected else phase.value
                logger.error("http_unexpected_error request_id=%s phase=%s exception_type=%s source=%s line=%d",
                             request_id(scope), fault_phase, type(exc).__name__, filename, line)
                self.counts.add("unexpected_" + fault_phase)
                if not disconnected and phase is ResponsePhase.BEFORE_START:
                    detail = {"code": "INTERNAL_ERROR", "message": error_payload(scope, 500, None)["message"]}
                    response = self.response_policy(error_response(scope, 500, detail))
                    await response(scope, receive, send)
                elif not disconnected and phase is ResponsePhase.STARTED:
                    abort_stream = True
            # A completed response (e.g. failing BackgroundTask) is already immutable.
        if abort_disconnect:
            raise ClientDisconnect("HTTP client disconnected; request_id=" + request_id(scope))
        if abort_stream:
            # Outside the except block: no original exception context reaches uvicorn.
            raise StreamAborted("HTTP stream aborted; request_id=" + request_id(scope))


class RequestCorrelation:
    def __init__(self, app, *, counts):
        self.app = app
        self.counts = counts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})["request_id"] = uuid4().hex

        async def identified_send(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id(scope)
                status = message["status"]
                if 400 <= status <= 599:
                    self.counts.add("http_" + str(status))
            await send(message)

        await self.app(scope, receive, identified_send)


def install_error_handlers(app):
    app.state.http_error_counts = ErrorCounts()
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
