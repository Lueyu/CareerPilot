"""ASGI admission boundary using the existing private and registered identities."""
import logging
import re
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.routing import Match

from app.api.deps import get_workspace, require_auth, require_local_owner
from app.config import get_settings
from app.db import SessionLocal
from app.models import Workspace
from app.services.api_rate_limits import admit

logger = logging.getLogger(__name__)

# Generation-capable operations, including deferred work and cached responses.
# GET streams only observe durable results and deliberately do not appear here.
LLM_ROUTES = (
    ("GET", "/learning/tasks/{task_id}/content"),
    ("POST", "/study/sessions/{session_id}/messages"),
    ("POST", "/study/sessions/{session_id}/messages/{message_id}/retry"),
    ("POST", "/study/sessions/{session_id}/interview-question"),
    ("POST", "/study/sessions/{session_id}/note-jobs"),
    ("POST", "/study/note-jobs/{job_id}/retry"),
    ("POST", "/study/notes/{note_id}/cards"),
    ("POST", "/study/practices/{practice_id}/answers"),
    ("POST", "/memory/import-dialog"),
    ("POST", "/bank/imports"),
    ("POST", "/bank/imports/upload"),
    ("POST", "/bank/imports/{import_id}/interview-sets"),
    ("POST", "/bank/imports/{import_id}/items/{item_id}/answer"),
    ("POST", "/bank/questions/{short_code}/answer"),
    ("POST", "/bank/imports/{import_id}/items/{item_id}/review"),
    ("POST", "/bank/imports/{import_id}/approve"),
    ("POST", "/projects"),
    ("POST", "/projects/{project_id}/analyses"),
    ("POST", "/question-generation/{task_id}/run"),
    ("POST", "/projects/{project_id}/analyses/{analysis_id}/run"),
    ("POST", "/projects/{project_id}/ai-scan"),
    ("POST", "/projects/{project_id}/reanalyze"),
    ("POST", "/projects/{project_id}/check-updates"),
    ("POST", "/analysis-runs/{run_id}/resume"),
    ("PUT", "/project-autopilot/settings"),
    ("POST", "/exams/{exam_id}/submit"),
    ("POST", "/exams/{exam_id}/rescore"),
    ("POST", "/exams/{exam_id}/report/deep"),
    ("POST", "/learning/tasks/{task_id}/help"),
    ("POST", "/learning/activities/{activity_id}/submissions"),
    ("POST", "/learning/submissions/{submission_id}/retry"),
    ("POST", "/learning/activities/{activity_id}/project-submit"),
    ("POST", "/podcasts/import"),
    ("POST", "/podcasts/episodes/{episode_id}/synthesize"),
    ("POST", "/podcasts/episodes/{episode_id}/retry"),
    ("POST", "/career-assets/updates/run"),
    ("POST", "/settings/llm/test"),
    ("POST", "/library/directions"),
    ("POST", "/library/starter"),
    ("POST", "/library/imports/{import_id}/suggest"),
    ("POST", "/coach/threads/{thread_id}/turns"),
    ("POST", "/coach/threads/{thread_id}/turns/{turn_id}/retry"),
)
_LLM_PATTERNS = [(method, re.compile(re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"))
                 for method, path in LLM_ROUTES]


def categories_for(method: str, path: str) -> tuple:
    """Classify private paths and cookie gateway paths with the same route table."""
    path = path.removeprefix("/api/v1").rstrip("/")
    path = path.removeprefix("/native") if path.startswith("/native/") else path
    base = "read" if method in {"GET", "HEAD"} else "write"
    if any(method == verb and pattern.fullmatch(path) for verb, pattern in _LLM_PATTERNS):
        return base, "llm"
    return (base,)


def _dependencies(dependant) -> set:
    return {dependant.call} | {call for child in dependant.dependencies for call in _dependencies(child)}


def _matching_route(routes, scope):
    for candidate in routes:
        if candidate.matches(scope)[0] != Match.FULL:
            continue
        if isinstance(candidate, APIRoute):
            return candidate
        # FastAPI's included-router contexts hold the effective prefix and dependencies.
        if hasattr(candidate, "effective_route_contexts"):
            for route in candidate.effective_route_contexts():
                if route.matches(scope)[0] == Match.FULL:
                    return route
    return None


async def _subject(request: Request, route: APIRoute, session, registered: bool) -> str | None:
    calls = _dependencies(route.dependant)
    if not registered or calls & {get_workspace, require_auth, require_local_owner}:
        if not calls & {get_workspace, require_auth, require_local_owner}:
            return None
        scope = await require_auth(authorization=request.headers.get("authorization"),
                                   x_api_token=request.headers.get("x-api-token"))
        if require_local_owner in calls and not scope.is_owner:
            raise HTTPException(403, "local owner required")
        if get_workspace in calls:
            ws = await get_workspace(session=session, _auth=scope, x_workspace=request.headers.get("x-workspace"))
            return f"workspace:{ws.id}"
        if not scope.is_owner:
            workspace_id = await session.scalar(select(Workspace.id).where(Workspace.id == scope.workspace_id))
            if workspace_id is None:
                raise HTTPException(404, "workspace not found")
            return f"workspace:{workspace_id}"
        return scope.subject

    from app.api.public_auth import _session_account, current_workspace

    if not calls & {_session_account, current_workspace}:
        return None
    account = await _session_account(request, session)
    return f"workspace:{account.workspace_id}"


class ApiRateLimitMiddleware:
    def __init__(self, app, *, registered: bool = False):
        self.app = app
        self.registered = registered

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1/") or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        request = Request(scope, receive=receive)
        request_id = uuid4().hex
        request.state.request_id = request_id

        async def identified_send(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        route = _matching_route(scope["app"].router.routes, scope)
        if route is None:
            return await self.app(scope, receive, identified_send)
        categories = categories_for(scope["method"], scope["path"])
        subject = None
        try:
            async with SessionLocal() as session:
                subject = await _subject(request, route, session, self.registered)
            if subject is not None:
                async with SessionLocal() as session:
                    await admit(session, subject, categories, get_settings().api_rate_limits)
        except HTTPException as exc:
            if exc.status_code == 429:
                logger.info("api_quota_rejected request_id=%s subject=%s categories=%s route=%s retry_after=%s",
                            request_id, subject, categories, route.path, exc.headers["Retry-After"])
            return await JSONResponse({"detail": exc.detail, "request_id": request_id}, status_code=exc.status_code,
                                      headers=exc.headers)(scope, receive, identified_send)
        except SQLAlchemyError:
            logger.warning("api_quota_unavailable request_id=%s route=%s", request_id, route.path)
            return await JSONResponse({"detail": {"code": "API_QUOTA_UNAVAILABLE"}, "request_id": request_id},
                                      status_code=503)(scope, receive, identified_send)
        await self.app(scope, receive, identified_send)
