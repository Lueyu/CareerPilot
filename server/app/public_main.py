"""Dedicated application for the publicly registered edition of CareerPilot."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.api import public_auth, public_coach, public_library, public_memory
from app.db import Base, engine


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


@asynccontextmanager
async def lifespan(application):
    public_auth.validate_auth_config()
    await initialize_public_database()
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="CareerPilot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.middleware("http")(public_auth.public_origin_guard)
for routes in (public_auth.router, public_library.router, public_coach.router, public_memory.router):
    app.include_router(routes, prefix="/api/v1")


@app.middleware("http")
async def private_responses(request, call_next):
    response = await call_next(request)
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
    app.mount("/", StaticFiles(directory=directory, html=True), name="public-web")
