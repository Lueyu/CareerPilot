"""Public identity configuration, credentials, and request throttling."""

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from ipaddress import IPv6Address, ip_address, ip_network
from math import ceil
from urllib.parse import urlsplit

from anyio import CapacityLimiter, to_thread
from fastapi import HTTPException, Request
from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import message as localized_message
from app.models.public_auth import PublicAuthThrottle

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_PLACEHOLDER_PREFIXES = (
    "changeme", "replaceme", "yourrandomsecret", "insecure", "example", "development",
    "defaultsecret", "secretkey", "cpdevtoken", "careerpilotdev", "0123456789", "1234567890",
)
_HASH_WORKERS = CapacityLimiter(2)
_DUMMY_PASSWORD_HASH = f"{'0' * 32}:{'0' * 64}"


@dataclass(frozen=True)
class AuthConfig:
    origin: str
    host: str
    secure_cookie: bool
    secret: bytes

    @property
    def cookie_name(self) -> str:
        return "__Host-seed_session" if self.secure_cookie else "seed_dev_session"


def auth_config() -> AuthConfig:
    return _parse_config(os.environ.get("SEED_PUBLIC_ORIGIN", ""), os.environ.get("SEED_SESSION_SECRET", ""))


@lru_cache(maxsize=1)
def _parse_config(origin: str, secret: str) -> AuthConfig:
    origin_error = "SEED_PUBLIC_ORIGIN must be a canonical HTTPS origin, or explicit HTTP loopback."
    try:
        parsed = urlsplit(origin)
        hostname = parsed.hostname or ""
        port = parsed.port
        if ":" in hostname:
            host = f"[{IPv6Address(hostname).compressed}]"
        elif re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname, re.ASCII):
            host = hostname
        else:
            raise ValueError
    except ValueError:
        raise RuntimeError(origin_error) from None

    secure = parsed.scheme == "https"
    if not secure and (parsed.scheme != "http" or hostname not in _LOOPBACK):
        raise RuntimeError(origin_error)
    if port is not None and port != (443 if secure else 80):
        host = f"{host}:{port}"
    if origin != f"{parsed.scheme}://{host}":
        raise RuntimeError(origin_error)

    simplified = re.sub(r"[^a-z0-9]", "", secret.lower())
    if (
        len(secret) < 32
        or secret.strip() != secret
        or len(set(secret)) < 8
        or simplified.startswith(_PLACEHOLDER_PREFIXES)
    ):
        raise RuntimeError("SEED_SESSION_SECRET must be an independent random secret of at least 32 characters.")
    return AuthConfig(origin=origin, host=host, secure_cookie=secure, secret=secret.encode())


def credential_digest(purpose: str, value: str) -> str:
    return hmac.digest(auth_config().secret, f"{purpose}:{value}".encode(), "sha256").hex()


def _password_digest(password: str, salt: bytes) -> str:
    # scrypt uses 64 MiB per worker with these parameters.
    return hashlib.scrypt(
        password.encode(), salt=salt, n=2**16, r=8, p=2, maxmem=96 * 1024 * 1024, dklen=32,
    ).hex()


async def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = await to_thread.run_sync(_password_digest, password, salt, limiter=_HASH_WORKERS)
    return f"{salt.hex()}:{digest}"


async def password_matches(password: str, stored_hash: str = _DUMMY_PASSWORD_HASH) -> bool:
    salt, expected = stored_hash.split(":")
    digest = await to_thread.run_sync(_password_digest, password, bytes.fromhex(salt), limiter=_HASH_WORKERS)
    return hmac.compare_digest(digest, expected)


@dataclass(frozen=True)
class AttemptLimit:
    scope: str
    subject: str
    count: int
    window: timedelta


def client_ip(request: Request) -> str:
    # request.client reflects the ASGI server's trusted proxy configuration.
    if request.client is None:
        return "unknown"
    try:
        address = ip_address(request.client.host)
    except ValueError:
        return "unknown"
    if isinstance(address, IPv6Address):
        return str(address.ipv4_mapped or ip_network((address, 64), strict=False))
    return str(address)


async def record_attempt(session: AsyncSession, *limits: AttemptLimit) -> None:
    """Charge each limit before verifying credentials, including on failure."""
    now = (await session.execute(select(func.now()))).scalar_one()
    throttle = PublicAuthThrottle
    for limit in limits:
        key = credential_digest(f"throttle:{limit.scope}", limit.subject)
        statement = insert(throttle).values(
            key=key,
            attempts=1,
            expires_at=now + limit.window,
        )
        expired = throttle.expires_at <= now
        updated = await session.scalar(
            statement.on_conflict_do_update(
                index_elements=[throttle.key],
                set_={
                    "attempts": case((expired, 1), else_=throttle.attempts + 1),
                    "expires_at": case((expired, statement.excluded.expires_at), else_=throttle.expires_at),
                },
                where=expired | (throttle.attempts < limit.count),
            ).returning(throttle.key)
        )
        if updated is None:
            deadline = (await session.execute(
                select(throttle.expires_at).where(throttle.key == key)
            )).scalar_one()
            await session.commit()
            retry_after = max(1, ceil((deadline - now).total_seconds()))
            raise HTTPException(
                status_code=429,
                detail=localized_message("errors.too_many_attempts_try_again_later"),
                headers={"Retry-After": str(retry_after)},
            )

    if secrets.randbelow(128) == 0:
        expired_keys = (
            select(throttle.key)
            .where(throttle.expires_at <= now)
            .order_by(throttle.expires_at)
            .limit(500)
            .with_for_update(skip_locked=True)
        )
        await session.execute(delete(throttle).where(throttle.key.in_(expired_keys)))
    await session.commit()
