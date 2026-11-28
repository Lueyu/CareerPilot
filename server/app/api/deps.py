"""Authenticated local principals and persisted workspace isolation."""
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Workspace
from app.services.local_credentials import AuthenticatedPrincipal, authenticate, tenant_workspace_ids


async def require_auth(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
) -> AuthenticatedPrincipal:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_token:
        token = x_api_token
    return await authenticate(token)


async def require_local_owner(scope: AuthenticatedPrincipal = Depends(require_auth)) -> None:
    if not scope.is_owner:
        raise HTTPException(status_code=403, detail="local owner required")


async def get_workspace(
    session: AsyncSession = Depends(get_session),
    _auth: AuthenticatedPrincipal = Depends(require_auth),
    x_workspace: str | None = Header(default=None),
) -> Workspace:
    statement = select(Workspace)
    if _auth.is_owner:
        slug = x_workspace or get_settings().default_workspace_slug
        statement = statement.where(Workspace.slug == slug, Workspace.id.not_in(tenant_workspace_ids()))
    else:
        statement = statement.where(Workspace.id == _auth.workspace_id)
        if x_workspace:
            statement = statement.where(Workspace.slug == x_workspace)
    ws = (await session.execute(statement)).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail="workspace not found")
    return ws
