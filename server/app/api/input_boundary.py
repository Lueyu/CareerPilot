"""Bound actual received bytes before handing scoped JSON requests to FastAPI."""
import json
import re

from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_SCOPES = (
    ('POST', re.compile(r'/api/v1/telemetry/events/?$'), 128 * 1024),
    ('POST', re.compile(r'/api/v1/bank/imports(?:/[^/]+/approve)?/?$'), 2 * 1024 * 1024),
    ('POST', re.compile(r'/api/v1/learning/activities/[^/]+/(?:submissions|project-submit)/?$'), 512 * 1024),
    ('PUT', re.compile(r'/api/v1/public-content/rules/?$'), 512 * 1024),
)


def json_input_limit(scope: Scope):
    return next((cap for method, path, cap in _SCOPES
                 if scope['type'] == 'http' and scope['method'] == method
                 and path.fullmatch(scope['path'])), None)


async def input_validation_error(request: Request, error: RequestValidationError):
    if json_input_limit(request.scope) is None:
        return await request_validation_exception_handler(request, error)
    # Raw invalid JSON may include NaN/Infinity, which JSONResponse cannot encode.
    # Preserve the 422 field locations and messages without echoing entire answers.
    errors = [{key: item[key] for key in ('type', 'loc', 'msg')} for item in error.errors()]
    return JSONResponse({'detail': errors}, status_code=422)


def _too_deep(body: bytearray) -> bool:
    try:
        text = body.decode(json.detect_encoding(body))
    except UnicodeDecodeError:
        return False  # FastAPI retains its normal invalid-encoding response.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in ('[', '{'):
            depth += 1
            if depth > 32:
                return True
        elif char in (']', '}'):
            depth -= 1
    return False


class JsonInputBoundary:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        limit = json_input_limit(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > limit:
                await JSONResponse({'detail': 'request body too large'}, status_code=413)(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        if _too_deep(body):
            await JSONResponse({'detail': 'JSON nesting exceeds 32'}, status_code=422)(scope, receive, send)
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
