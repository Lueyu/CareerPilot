"""Select the native deployment from the verified registration workspace."""
import asyncio
import os
import re
import time
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx
from dataclasses import dataclass, field
from functools import lru_cache

from anyio import CancelScope
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Workspace


@dataclass(frozen=True)
class NativeTarget:
    base_url: str
    workspace: str
    api_token: str = field(repr=False)


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


_clients: dict[NativeTarget, dict[bool, httpx.AsyncClient]] = {}


def _new_client(limit: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=limit, max_keepalive_connections=limit, keepalive_expiry=30),
        timeout=httpx.Timeout(1810.0, connect=8.0), trust_env=False, follow_redirects=False,
        cookies=CookieJar(policy=_NoCookies()),
    )


def target_client(target: NativeTarget, streaming: bool) -> httpx.AsyncClient:
    if target.workspace != "personal":
        state = _sync.get(target.workspace)
        if state is None or state.target != target:
            raise HTTPException(503, "工作台地址已更新，请重试。")
    lanes = _clients.setdefault(target, {})
    if streaming not in lanes:
        lanes[streaming] = _new_client(2 if streaming else 6)
    return lanes[streaming]


async def _close_clients(target: NativeTarget):
    with CancelScope(shield=True):
        for client in _clients.pop(target, {}).values():
            await client.aclose()


def is_bound_owner(ws: Workspace) -> bool:
    # The randomly assigned slug is immutable across renames and recovery. Re-registering
    # the same username creates a different slug and cannot inherit the local workspace.
    binding = os.environ.get("SEED_NATIVE_OWNER_BINDING")
    return bool(binding and binding == f"{ws.id}:{ws.slug}" and re.fullmatch(r"seed-[0-9a-f]{32}", ws.slug))


def validate_native_config():
    binding = os.environ.get("SEED_NATIVE_OWNER_BINDING", "")
    token = os.environ.get("SEED_NATIVE_OWNER_API_TOKEN", "")
    if binding or token:
        if not re.fullmatch(r"[1-9][0-9]*:seed-[0-9a-f]{32}", binding) or not token:
            raise RuntimeError("The personal workspace needs both a pinned registration and its server credential")


@lru_cache(maxsize=1)
def local_runtime():
    if os.environ.get("SEED_NATIVE_LOCAL_TENANTS") != "1":
        raise HTTPException(503, "此部署尚未配置独立工作台。")
    from deploy.tenant.runtime import TenantRuntime

    return TenantRuntime()


class SyncState:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.generation = 0
        self.synced = -1
        self.refresh_after = 0.0
        self.target: NativeTarget | None = None
        self.check_after = 0.0


_sync: dict[str, SyncState] = {}


def invalidate_transfer(slug: str):
    if slug in _sync:
        _sync[slug].generation += 1


async def _transfer(runtime, db: AsyncSession, ws: Workspace):
    from app.services.native_workspace_transfer import transfer_native_workspace

    access = runtime.database(ws.slug, ws.target_role)
    url = await asyncio.to_thread(access.__enter__)
    try:
        engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://", 1), poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as destination, destination.begin():
                native_id = (await destination.scalars(select(Workspace.id).where(Workspace.slug == ws.slug))).one()
                await transfer_native_workspace(db, destination, ws.id, native_id)
        finally:
            await engine.dispose()
    finally:
        await asyncio.to_thread(access.__exit__, None, None, None)


# Leave pool capacity for authentication and deletes during multi-account cold starts.
_cold_slots = asyncio.Semaphore(2)


async def _prepare(state, db, ws):
    # Account deletion takes FOR UPDATE before removing its runtime. Hold this
    # compatible reader lock only for provisioning and transfer, never for proxy I/O.
    exists = await db.scalar(select(Workspace.id).where(
        Workspace.id == ws.id, Workspace.slug == ws.slug
    ).with_for_update(read=True, key_share=True))
    if exists is None:
        raise HTTPException(401, "账号会话已失效。")
    runtime = local_runtime()
    if state.target is None or state.check_after <= time.monotonic():
        tenant = await asyncio.to_thread(runtime.ensure, ws.slug, ws.target_role)
        previous = state.target
        state.target = NativeTarget(tenant.base_url, tenant.workspace_slug, tenant.api_token)
        if previous and previous != state.target:
            await _close_clients(previous)
        state.check_after = time.monotonic() + 30
    generation = state.generation
    if state.synced != generation or state.refresh_after <= time.monotonic():
        await _transfer(runtime, db, ws)
        state.synced = generation
        state.refresh_after = time.monotonic() + 120
    return state.target


async def native_target(db: AsyncSession, ws: Workspace) -> NativeTarget:
    # close() detaches the fully loaded identity and releases the auth transaction.
    await db.close()
    if is_bound_owner(ws):
        token = os.environ.get("SEED_NATIVE_OWNER_API_TOKEN")
        if not token:
            raise HTTPException(503, "原个人工作台尚未连接。")
        return NativeTarget("http://127.0.0.1:8000", "personal", token)
    if not re.fullmatch(r"seed-[0-9a-f]{32}", ws.slug) or ws.is_demo:
        raise HTTPException(403, "无效的注册工作空间。")
    state = _sync.setdefault(ws.slug, SyncState())
    try:
        async with state.lock:
            now = time.monotonic()
            if (state.target and state.check_after > now and state.synced == state.generation
                    and state.refresh_after > now and _sync.get(ws.slug) is state):
                return state.target
            async with _cold_slots:
                # A cancelled request cannot stop Docker's worker thread. Finish
                # the operation before releasing the database lock to a delete.
                async with db.begin():
                    work = asyncio.create_task(_prepare(state, db, ws))
                    try:
                        return await asyncio.shield(work)
                    except asyncio.CancelledError:
                        with CancelScope(shield=True):
                            await work
                        raise
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(503, "独立工作台暂时未就绪，请重试。") from exc

async def close_native_runtime():
    for target in list(_clients):
        await _close_clients(target)
    if local_runtime.cache_info().currsize:
        await asyncio.to_thread(local_runtime().close)
        local_runtime.cache_clear()
    _sync.clear()


async def remove_native_workspace(ws: Workspace):
    if is_bound_owner(ws):
        for target in list(_clients):
            if target.workspace == "personal":
                await _close_clients(target)
        return
    if os.environ.get("SEED_NATIVE_LOCAL_TENANTS") != "1":
        return
    if not re.fullmatch(r"seed-[0-9a-f]{32}", ws.slug):
        raise HTTPException(409, "无法确认独立工作台的归属。")
    try:
        await asyncio.to_thread(local_runtime().remove, ws.slug)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(503, "暂时无法清理独立工作台，账号仍保留。") from exc
    state = _sync.pop(ws.slug, None)
    if state and state.target:
        await _close_clients(state.target)
