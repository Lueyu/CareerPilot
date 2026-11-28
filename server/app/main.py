"""CareerPilot 后端入口: app 组装 + CORS + lifespan 建表。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app import personal_startup as _personal_startup  # noqa: F401 (preflight before runtime imports)
from app.api.input_boundary import JsonInputBoundary, input_validation_error
from app.api.rate_limits import ApiRateLimitMiddleware
from app.api.recovery_guard import RecoveryGuardMiddleware
from app.api.v1 import (
    assessment_feedback,
    bank,
    benchmark,
    career_assets,
    dashboard,
    evolution,
    exams,
    feedback,
    learning,
    learning_feedback,
    learning_memory,
    learning_projects,
    memory,
    observatory,
    ops,
    podcasts,
    profile,
    project_analysis,
    project_autopilot,
    project_dossier,
    project_resume,
    projects,
    public_content,
    question_generation,
    retest_mappings,
    settings,
    study_chat,
    study_notes,
    study_practice,
    telemetry,
)
from app.config import get_settings
from app.db import engine
from app.i18n import LanguageMiddleware
from app.models import Base  # noqa: F401  (注册全部模型)
from app.services.podcast_jobs import run_worker as run_podcast_worker
from app.services.recovery_control import RecoveryBlocked, require_normal
from app.services.study_chat import recover_interrupted as recover_study_chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().require_secure_personal_startup()
    # 模型定义用于启动建表；部署入口也会执行 migrations/*.sql。
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    podcast_worker = None
    try:
        await require_normal()
    except RecoveryBlocked:
        pass  # Health remains available, but no recovered work is resumed.
    else:
        await question_generation.recover_interrupted()
        await project_analysis.recover_interrupted()
        await recover_study_chat()
        podcast_worker = asyncio.create_task(run_podcast_worker())
    try:
        yield
    finally:
        if podcast_worker is not None:
            podcast_worker.cancel()
            with suppress(asyncio.CancelledError):
                await podcast_worker
        await engine.dispose()


app = FastAPI(
    title="CareerPilot API",
    version="0.1.0",
    description="Evidence-driven career growth platform for AI engineers",
    lifespan=lifespan,
)

app.add_middleware(JsonInputBoundary)
app.add_middleware(LanguageMiddleware)
app.add_exception_handler(RequestValidationError, input_validation_error)
app.add_middleware(ApiRateLimitMiddleware)
app.add_middleware(RecoveryGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"http://(?:localhost|127\.0\.0\.1)(?::\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 认证在 get_workspace 依赖链内(require_auth), /healthz 开放
for router in (study_chat.router, study_notes.router, study_practice.router, dashboard.router, benchmark.router, profile.router, exams.router, bank.router, learning.router, memory.router, observatory.router, ops.router, public_content.router, settings.router, projects.router, career_assets.router, evolution.router, telemetry.router):
    app.include_router(router, prefix="/api/v1")
app.include_router(podcasts.router, prefix="/api/v1")
app.include_router(question_generation.router, prefix="/api/v1")
app.include_router(project_analysis.router, prefix="/api/v1")
app.include_router(project_autopilot.router, prefix="/api/v1")
app.include_router(project_dossier.router, prefix="/api/v1")
app.include_router(project_resume.router, prefix="/api/v1")
app.include_router(assessment_feedback.router, prefix="/api/v1")
app.include_router(learning_feedback.router, prefix="/api/v1")
app.include_router(learning_projects.router, prefix="/api/v1")
app.include_router(learning_memory.router, prefix="/api/v1")
app.include_router(retest_mappings.router, prefix="/api/v1")
app.include_router(feedback.router, prefix="/api/v1")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "careerpilot-api", "scorer_model": get_settings().scorer_model_version}


# 可选静态托管（生产部署：STATIC_DIR 指向前端构建产物目录，如 web/dist）。
# 未设置时行为与本地开发完全一致（不挂载任何静态路由）。
static_dir = get_settings().static_dir
if static_dir:
    _static_root = Path(static_dir).resolve()
    _static_files = StaticFiles(directory=_static_root, check_dir=False)
    _has_assets = (_static_root / "assets").is_dir()

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa_fallback(request: Request, full_path: str):
        namespace = full_path.partition("/")[0]
        if namespace == "api":
            raise HTTPException(status_code=404, detail="Not Found")

        try:
            return await _static_files.get_response(full_path, request.scope)
        except HTTPException as exc:
            if exc.status_code != 404 or (_has_assets and full_path.startswith("assets/")):
                raise

        return await _static_files.get_response("index.html", request.scope)
