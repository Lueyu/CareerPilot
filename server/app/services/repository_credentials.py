"""Resolve repository credentials for a persisted workspace."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppSetting, Workspace


async def _stored_token(session: AsyncSession, key: str) -> str:
    row = await session.get(AppSetting, key)
    value = row.value if row else None
    token = value.get('token') if isinstance(value, dict) else None
    return token.strip() if isinstance(token, str) else ''


async def repository_credentials(session: AsyncSession, workspace_id: int) -> dict[str, str]:
    settings = get_settings()
    slug = await session.scalar(select(Workspace.slug).where(Workspace.id == workspace_id))
    allow_owner_credentials = slug == settings.default_workspace_slug and slug not in settings.workspace_token_hashes
    credentials = {'github_token': '', 'gitcode_token': ''}
    if slug is None:
        return credentials
    for provider in ('github', 'gitcode'):
        key = f'{provider}_token'
        token = await _stored_token(session, f'{provider}:{workspace_id}')
        if not token and allow_owner_credentials:
            token = await _stored_token(session, provider) or getattr(settings, key).strip()
        credentials[key] = token
    return credentials
