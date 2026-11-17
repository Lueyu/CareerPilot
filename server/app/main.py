"""CareerPilot 后端入口: app 组装 + CORS + lifespan 建表。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import bank, benchmark, career_assets, dashboard, evolution, exams, learning, memory, observatory, profile, projects, public_content, settings
from app.api.v1 import question_generation, project_analysis, project_autopilot, assessment_feedback
from app.api.v1 import learning_feedback, learning_projects, learning_memory
from app.config import get_settings
from app.db import engine
from app.models import Base  # noqa: F401  (注册全部模型)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 开发环境自动建表(生产走 Alembic 迁移)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await question_generation.recover_interrupted()
    await project_analysis.recover_interrupted()
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
for router in (dashboard.router, benchmark.router, profile.router, exams.router, bank.router, learning.router, memory.router, observatory.router, public_content.router, settings.router, projects.router, career_assets.router, evolution.router):
    app.include_router(router, prefix="/api/v1")
app.include_router(question_generation.router, prefix="/api/v1")
app.include_router(project_analysis.router, prefix="/api/v1")
app.include_router(project_autopilot.router, prefix="/api/v1")
app.include_router(assessment_feedback.router, prefix="/api/v1")
app.include_router(learning_feedback.router, prefix="/api/v1")
app.include_router(learning_projects.router, prefix="/api/v1")
app.include_router(learning_memory.router, prefix="/api/v1")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "careerpilot-api", "scorer_model": get_settings().scorer_model_version}
