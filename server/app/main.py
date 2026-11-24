"""CareerPilot 后端入口: app 组装 + CORS + lifespan 建表。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import (
    assessment_feedback,
    bank,
    benchmark,
    career_assets,
    dashboard,
    evolution,
    exams,
    learning,
    learning_feedback,
    learning_memory,
    learning_projects,
    memory,
    observatory,
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
)
from app.config import get_settings
from app.db import engine
from app.models import Base  # noqa: F401  (注册全部模型)
from app.services.study_chat import recover_interrupted as recover_study_chat


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 开发环境自动建表(生产走 Alembic 迁移)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await question_generation.recover_interrupted()
    await project_analysis.recover_interrupted()
    await recover_study_chat()
    yield
    await engine.dispose()


app = FastAPI(
    title="CareerPilot API",
    version="0.1.0",
    description="Evidence-driven career growth platform for AI engineers",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"http://(?:localhost|127\.0\.0\.1)(?::\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 认证在 get_workspace 依赖链内(require_auth), /healthz 开放
for router in (study_chat.router, study_notes.router, study_practice.router, dashboard.router, benchmark.router, profile.router, exams.router, bank.router, learning.router, memory.router, observatory.router, public_content.router, settings.router, projects.router, career_assets.router, evolution.router):
    app.include_router(router, prefix="/api/v1")
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


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "careerpilot-api", "scorer_model": get_settings().scorer_model_version}


# 可选静态托管（生产部署：STATIC_DIR 指向前端构建产物目录，如 web/dist）。
# 未设置时行为与本地开发完全一致（不挂载任何静态路由）。
static_dir = os.environ.get("STATIC_DIR")
if static_dir:
    from pathlib import Path as _Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _static_root = _Path(static_dir).resolve()
    _assets_dir = _static_root / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not Found")
        candidate = _static_root / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = _static_root / "index.html"
        if index.is_file():
            return FileResponse(index)
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not Found")
