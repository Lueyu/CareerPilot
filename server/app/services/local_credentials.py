"""Explicit enrollment and fresh, fail-closed local authentication."""
import hashlib
from dataclasses import dataclass
from datetime import timedelta
from math import ceil
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.config import require_secret
from app.models import LocalAuthFailure, LocalCredential, LocalWorkspaceBoundary, Workspace
from app.services.recovery_control import require_normal


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    credential_id: str
    generation: str
    kind: Literal["OWNER", "WORKSPACE"]
    workspace_id: int | None

    def __post_init__(self):
        if self.kind not in {"OWNER", "WORKSPACE"} or (self.kind == "OWNER") != (self.workspace_id is None):
            raise ValueError("Invalid authenticated principal")

    @property
    def is_owner(self) -> bool:
        return self.kind == "OWNER"

    @property
    def subject(self) -> str:
        return "local-owner" if self.is_owner else f"workspace:{self.workspace_id}"


def tenant_workspace_ids():
    return select(Workspace.id).join(LocalWorkspaceBoundary, or_(
        Workspace.id == LocalWorkspaceBoundary.workspace_id, Workspace.slug == LocalWorkspaceBoundary.slug))


async def migrate_legacy_boundaries(session, slugs) -> None:
    """Explicit offline migration preserves every claim, even absent workspaces/tokens."""
    await require_normal(session)
    for slug in sorted(slugs):
        if not slug or len(slug) > 64 or slug != slug.strip():
            raise ValueError("Invalid legacy workspace claim")
        workspace_id = await session.scalar(select(Workspace.id).where(Workspace.slug == slug))
        await session.execute(insert(LocalWorkspaceBoundary).values(slug=slug, workspace_id=workspace_id).on_conflict_do_nothing())


def metadata(row: LocalCredential) -> dict:
    return {field: (value.isoformat() if hasattr(value, "isoformat") else value)
            for field in ("id", "kind", "workspace_id", "generation", "created_at", "expires_at", "last_used_at", "revoked_at")
            for value in (getattr(row, field),)}


async def enroll(session, raw: str, *, workspace: str | None = None, days: int = 30) -> LocalCredential:
    """Offline caller owns the transaction. No request path calls this function."""
    require_secret(raw, "access credential", minimum=32)
    if len(raw) > 512 or not 1 <= days <= 365:
        raise ValueError("Credential or lifetime outside supported bounds")
    generation = await require_normal(session)
    workspace_id = None
    if workspace is not None:
        workspace_id = await session.scalar(select(Workspace.id).where(Workspace.slug == workspace).with_for_update())
        if workspace_id is None:
            raise ValueError("Workspace must already exist before enrollment")
        await session.execute(insert(LocalWorkspaceBoundary).values(slug=workspace, workspace_id=workspace_id).on_conflict_do_nothing())
    digest = hashlib.sha256(raw.encode()).hexdigest()
    if await session.scalar(select(LocalCredential.id).where(LocalCredential.token_hash == digest)):
        raise ValueError("Credential was already enrolled; issue a fresh credential")
    now = await session.scalar(select(func.clock_timestamp()))
    row = LocalCredential(id=uuid4().hex, token_hash=digest, kind="OWNER" if workspace_id is None else "WORKSPACE",
                          workspace_id=workspace_id, generation=generation, created_at=now, expires_at=now + timedelta(days=days))
    session.add(row)
    await session.flush()
    return row


async def rotate(session, credential_id: str, raw: str, *, days: int = 30) -> LocalCredential:
    generation = await require_normal(session)
    row = await session.scalar(select(LocalCredential).where(LocalCredential.id == credential_id).with_for_update())
    now = await session.scalar(select(func.clock_timestamp()))
    if row is None or row.revoked_at is not None or row.expires_at <= now or row.generation != generation:
        raise ValueError("Only an active credential in this installation can rotate")
    workspace = None
    if row.workspace_id is not None:
        workspace = await session.scalar(select(Workspace.slug).where(Workspace.id == row.workspace_id))
        if workspace is None:
            raise ValueError("Credential workspace no longer exists")
    successor = await enroll(session, raw, workspace=workspace, days=days)
    row.revoked_at = now
    await session.flush()
    return successor


async def revoke(session, credential_id: str) -> LocalCredential:
    await require_normal(session)
    row = await session.scalar(select(LocalCredential).where(LocalCredential.id == credential_id).with_for_update())
    if row is None:
        raise ValueError("Credential not found")
    if row.revoked_at is None:
        row.revoked_at = await session.scalar(select(func.clock_timestamp()))
    await session.flush()
    return row


async def _failed_attempt(session) -> int:
    # One installation-wide bucket. No untrusted request value controls its identity.
    await session.execute(text("SELECT pg_advisory_xact_lock(2026112808)"))
    now = await session.scalar(select(func.clock_timestamp()))
    row = await session.get(LocalAuthFailure, 1)
    if row is None:
        row = LocalAuthFailure(id=1, tokens=10, updated_at=now)
        session.add(row)
    tokens = min(10, row.tokens + max(0, (now - row.updated_at).total_seconds()) / 6)
    retry = ceil((1 - tokens) * 6) if tokens < 1 else 0
    row.tokens = tokens if retry else tokens - 1
    row.updated_at = now
    return max(1, retry) if retry else 0


async def authenticate(raw: str | None) -> AuthenticatedPrincipal:
    from app import db

    principal = None
    retry = 0
    try:
        async with db.SessionLocal() as session, session.begin():
            generation = await require_normal(session)
            # Never hash arbitrarily large header input or store rejected token material.
            digest = hashlib.sha256(raw.encode()).hexdigest() if raw and len(raw) <= 512 and raw.isascii() else None
            row = await session.scalar(select(LocalCredential).where(LocalCredential.token_hash == digest).with_for_update()) if digest else None
            now = await session.scalar(select(func.clock_timestamp()))
            valid = row is not None and row.generation == generation and row.revoked_at is None and row.expires_at > now
            if valid and row.workspace_id is not None:
                valid = await session.scalar(select(Workspace.id).where(
                    Workspace.id == row.workspace_id, Workspace.id.in_(tenant_workspace_ids()))) is not None
            if valid:
                row.last_used_at = now
                principal = AuthenticatedPrincipal(row.id, row.generation, row.kind, row.workspace_id)
            else:
                retry = await _failed_attempt(session)
    except SQLAlchemyError:
        raise HTTPException(503, {"code": "AUTH_REGISTRY_UNAVAILABLE"}) from None
    if principal is not None:
        return principal
    if retry:
        raise HTTPException(429, {"code": "AUTH_RATE_LIMITED"}, headers={"Retry-After": str(retry)})
    raise HTTPException(401, "invalid token")
