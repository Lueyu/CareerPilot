"""API 依赖: 认证(MVP token) + 工作空间解析。

所有业务路由通过 get_workspace 拿到当前工作空间, 查询一律带 workspace_id 过滤。
"""
import hashlib
import hmac

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.config import get_settings
from app.models import Workspace


async def require_auth(
    authorization: str | None = Header(default=None),
    x_api_token: str | None = Header(default=None),
) -> str | None:
    """MVP 认证: Bearer <api_token> 或 X-Api-Token 头。生产换 JWT。"""
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_token:
        token = x_api_token
    cfg = get_settings()
    if token:
        digest = hashlib.sha256(token.encode()).hexdigest()
        for slug, expected in cfg.workspace_token_hashes.items():
            if hmac.compare_digest(digest, expected):
                return slug
        if (not cfg.workspace_token_hashes or cfg.api_token != "cp-dev-token") and hmac.compare_digest(
            token.encode(), cfg.api_token.encode()
        ):
            return None
    raise HTTPException(status_code=401, detail="invalid token")


async def require_local_owner(scope: str | None = Depends(require_auth)) -> None:
    if scope is not None:
        raise HTTPException(status_code=403, detail="local owner required")


async def get_workspace(
    session: AsyncSession = Depends(get_session),
    _auth: str | None = Depends(require_auth),
    x_workspace: str | None = Header(default=None),
) -> Workspace:
    """解析当前工作空间: X-Workspace 头(slug) 或默认 personal; 缺失时绝不回退 Demo。"""
    cfg = get_settings()
    scope = _auth if isinstance(_auth, str) else None
    slug = x_workspace or scope or cfg.default_workspace_slug
    if (scope and slug != scope) or (scope is None and slug in cfg.workspace_token_hashes):
        raise HTTPException(status_code=404, detail="workspace not found")
    ws = (
        await session.execute(select(Workspace).where(Workspace.slug == slug))
    ).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail=f"workspace '{slug}' not found")
    return ws
