"""Bootstrap a private native workspace and owner without importing a bank."""

import asyncio
import os

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import User, Workspace, WorkspaceMember
from app.services.tenant_standard import tenant_standard


async def initialize() -> None:
    slug = get_settings().default_workspace_slug
    email = f"{slug}@tenant.invalid"
    role = os.environ.get("TENANT_DISPLAY_ROLE", "").strip()[:160]
    try:
        async with engine.begin() as conn:
            await conn.execute(
                insert(Workspace)
                .values(
                    slug=slug, name="个人学习空间", target_role=role,
                    target_level="", is_demo=False,
                )
                .on_conflict_do_nothing(index_elements=[Workspace.slug])
            )
            workspace_id = (
                await conn.execute(
                    select(Workspace.id).where(Workspace.slug == slug, Workspace.is_demo.is_(False))
                )
            ).scalar_one()
            await conn.execute(
                insert(User)
                .values(email=email, display_name="个人用户")
                .on_conflict_do_nothing(index_elements=[User.email])
            )
            user_id = (
                await conn.execute(select(User.id).where(User.email == email))
            ).scalar_one()
            await conn.execute(
                insert(WorkspaceMember)
                .values(workspace_id=workspace_id, user_id=user_id, role="owner")
                .on_conflict_do_nothing(index_elements=[WorkspaceMember.workspace_id, WorkspaceMember.user_id])
            )
        async with SessionLocal() as session, session.begin():
            ws = await session.get(Workspace, workspace_id)
            await tenant_standard(session, ws)
    finally:
        await engine.dispose()


def create_app():
    """Run the native API with exactly one selectable workspace."""
    from fastapi.responses import JSONResponse
    from app.main import app

    slug = get_settings().default_workspace_slug

    async def tenant_app(scope, receive, send):
        if scope["type"] == "http":
            requested_slug = dict(scope["headers"]).get(b"x-workspace", slug.encode())
            path = scope["path"].rstrip("/")
            if requested_slug != slug.encode():
                response = JSONResponse({"detail": "workspace not found"}, status_code=404)
                await response(scope, receive, send)
                return
            if (scope["method"], path) in {
                ("POST", "/api/v1/settings/workspaces/personal"),
                ("DELETE", "/api/v1/settings/workspace"),
            }:
                response = JSONResponse(
                    {"detail": "Manage the workspace from the signed-in account"},
                    status_code=409,
                )
                await response(scope, receive, send)
                return
        await app(scope, receive, send)

    return tenant_app


if __name__ == "__main__":
    asyncio.run(initialize())
