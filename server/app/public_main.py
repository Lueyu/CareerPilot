"""Dedicated application for the publicly registered edition of CareerPilot."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

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
from app.api.v1 import ops, telemetry
from app.db import Base, engine
from app.i18n import LanguageMiddleware
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
app.middleware("http")(public_auth.public_origin_guard)
app.add_middleware(LanguageMiddleware)
for routes in (public_auth.router, public_library.router, public_coach.router, public_memory.router,
               public_dashboard.router, public_learning.router, public_native.router, public_feedback.router,
               telemetry.router, ops.router):
    app.include_router(routes, prefix="/api/v1")


@app.middleware("http")
async def private_responses(request, call_next):
    response = await call_next(request)
    slug = getattr(request.state, "registered_workspace_slug", None)
    if (slug and response.status_code < 300 and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and not request.url.path.startswith("/api/v1/native/") and request.url.path != "/api/v1/product-feedback"):
        invalidate_transfer(slug)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/healthz")
async def health():
    return {"ok": True, "service": "careerpilot-public"}


if static_dir := os.environ.get("SEED_STATIC_DIR"):
    directory = Path(static_dir).resolve()
    if not (directory / "index.html").is_file():
        raise RuntimeError("Build the public web entry before setting SEED_STATIC_DIR")
    app.mount("/static-assets", StaticFiles(directory=directory / "static-assets"), name="web-assets")

    @app.get("/{page:path}", include_in_schema=False)
    async def workspace_page(page: str):
        # Match browser navigation without serving index.html for missing API or asset requests.
        pages = {"", "bank", "study", "exam", "memory", "learning", "settings", "onboarding",
                 "profile", "benchmark", "assets", "observatory", "evolution", "beta", "account"}
        if page.split("/", 1)[0] not in pages or page.startswith("assets/") and Path(page).suffix:
            raise HTTPException(404)
        return FileResponse(directory / "index.html", headers={"Cache-Control": "no-cache"})
