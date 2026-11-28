"""Dedicated application for the publicly registered edition of CareerPilot."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from starlette.datastructures import MutableHeaders

from app.api import (
    public_auth,
    public_coach,
    public_dashboard,
    public_feedback,
    public_learning,
    public_library,
    public_memory,
    public_native,
)
from app.api.errors import ErrorBoundary, RequestCorrelation, install_error_handlers
from app.api.rate_limits import ApiRateLimitMiddleware
from app.api.recovery_guard import RecoveryGuardMiddleware
from app.api.v1 import ops, telemetry
from app.db import Base, engine
from app.i18n import LanguageMiddleware
from app.readiness import readiness_response
from app.services.public_native_runtime import close_native_runtime, invalidate_transfer, validate_native_config


async def initialize_public_database():
    configured = os.environ.get("DATABASE_URL")
    if not configured or make_url(configured) != engine.url:
        raise RuntimeError("Public mode requires an explicit, dedicated DATABASE_URL")
    if not engine.url.password or engine.url.password in {"careerpilot_dev", "postgres"}:
        raise RuntimeError("Configure independent public database credentials")
    async with engine.begin() as db:
        await db.execute(text("SELECT pg_advisory_xact_lock(725806111)"))
        tables = await db.run_sync(lambda conn: inspect(conn).get_table_names())
        if "careerpilot_seed_install" not in tables:
            if tables:
                raise RuntimeError("Public mode requires a fresh database or an existing public installation")
            await db.execute(text("CREATE TABLE careerpilot_seed_install (version integer PRIMARY KEY)"))
            await db.execute(text("INSERT INTO careerpilot_seed_install VALUES (1)"))
        elif await db.scalar(text("SELECT version FROM careerpilot_seed_install")) != 1:
            raise RuntimeError("Unsupported public database version")
        await db.run_sync(Base.metadata.create_all)
        recovery_schema = Path(__file__).resolve().parents[1] / "migrations" / "20261128_recovery_control.sql"
        await db.exec_driver_sql(recovery_schema.read_text(encoding="utf-8"))
        if "public_coach_turns" in tables:
            await db.execute(text("ALTER TABLE public_coach_turns ADD COLUMN IF NOT EXISTS "
                                  "response_language VARCHAR(8) NOT NULL DEFAULT 'zh-CN'"))


@asynccontextmanager
async def lifespan(application):
    public_auth.validate_auth_config()
    validate_native_config()
    await initialize_public_database()
    try:
        yield
    finally:
        await close_native_runtime()
        await engine.dispose()


app = FastAPI(title="CareerPilot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
install_error_handlers(app)
app.add_middleware(ApiRateLimitMiddleware, registered=True)
app.add_middleware(public_auth.PublicOriginGuard)
app.add_middleware(RecoveryGuardMiddleware)
app.add_middleware(LanguageMiddleware)
for routes in (public_auth.router, public_library.router, public_coach.router, public_memory.router,
               public_dashboard.router, public_learning.router, public_native.router, public_feedback.router,
               telemetry.router, ops.router):
    app.include_router(routes, prefix="/api/v1")


class PrivateResponses:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                path = scope["path"]
                slug = scope.get("state", {}).get("registered_workspace_slug")
                if (slug and message["status"] < 300 and scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}
                        and not path.startswith("/api/v1/native/") and path != "/api/v1/product-feedback"):
                    invalidate_transfer(slug)
                headers = MutableHeaders(scope=message)
                if path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "same-origin"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        await self.app(scope, receive, private_send)


def error_response_policy(responder):
    return PrivateResponses(LanguageMiddleware(responder))


app.add_middleware(PrivateResponses)
app.add_middleware(ErrorBoundary, counts=app.state.http_error_counts, response_policy=error_response_policy)
app.add_middleware(RequestCorrelation, counts=app.state.http_error_counts)


@app.get("/healthz")
async def health():
    return {"ok": True, "service": "careerpilot-public"}


@app.get("/readyz")
async def ready():
    return await readiness_response("careerpilot-public")


if static_dir := os.environ.get("SEED_STATIC_DIR"):
    directory = Path(static_dir).resolve()
    if not (directory / "index.html").is_file():
        raise RuntimeError("Build the public web entry before setting SEED_STATIC_DIR")
    app.mount("/static-assets", StaticFiles(directory=directory / "static-assets"), name="web-assets")

    @app.get("/{page:path}", include_in_schema=False)
    async def workspace_page(page: str):
        # Match browser navigation without serving index.html for missing API or asset requests.
        if page in {"podcast-offline.html", "podcast-sw.js"}:
            path = directory / page
            if not path.is_file():
                raise HTTPException(404)
            return FileResponse(path, headers={"Cache-Control": "no-cache"})
        pages = {"", "bank", "podcasts", "study", "exam", "memory", "learning", "settings", "onboarding",
                 "profile", "benchmark", "assets", "observatory", "evolution", "beta", "account"}
        if page.split("/", 1)[0] not in pages or page.startswith("assets/") and Path(page).suffix:
            raise HTTPException(404)
        return FileResponse(directory / "index.html", headers={"Cache-Control": "no-cache"})
