"""Resolve repository credentials for a persisted workspace."""

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppSetting, Workspace
from app.services.local_credentials import tenant_workspace_ids
from app.services.secret_storage import SecretStorageError, open_setting


async def _stored_token(session: AsyncSession, key: str) -> str:
    from app.services.recovery_control import require_normal
    await require_normal(session)
    try:
        row = await session.get(AppSetting, key)
    except SQLAlchemyError:
        raise SecretStorageError() from None
    value = open_setting(key, row.value) if row else None
    token = value.get('token') if isinstance(value, dict) else None
    return token.strip() if isinstance(token, str) else ''


async def repository_credentials(session: AsyncSession, workspace_id: int) -> dict[str, str]:
    settings = get_settings()
    slug = await session.scalar(select(Workspace.slug).where(Workspace.id == workspace_id))
    isolated = await session.scalar(select(Workspace.id).where(Workspace.id == workspace_id, Workspace.id.in_(tenant_workspace_ids())))
    allow_owner_credentials = slug == settings.default_workspace_slug and isolated is None
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
