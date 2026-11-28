"""Model URL policy and TCP pinning. Logical URLs never become IP URLs."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import httpcore
import httpx
from fastapi import HTTPException
from httpcore._backends.anyio import AnyIOBackend

from app.config import get_settings

SAFE_MESSAGE = "模型连接被出站安全策略拒绝，请检查接口地址及部署配置。"
_DNS_TIMEOUT = 3.0
_DNS_SLOTS = threading.BoundedSemaphore(8)
_DNS_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="model-dns")


class OutboundPolicyError(HTTPException):
    code = category = "MODEL_OUTBOUND_POLICY"
    safe_for_user = True

    def __init__(self):
        super().__init__(422, {"code": self.code, "message": SAFE_MESSAGE})

    def __str__(self):
        return SAFE_MESSAGE


@dataclass(frozen=True)
class ModelOrigin:
    scheme: str
    host: str
    port: int


@dataclass(frozen=True)
class ModelEndpoint:
    url: str
    origin: ModelOrigin


def parse_endpoint(value: str) -> ModelEndpoint:
    try:
        if (not isinstance(value, str) or not 0 < len(value) <= 2048
                or not value.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in value)
                or any(c in value for c in ("\\", "?", "#"))):
            raise ValueError
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None or "%" in url.netloc):
            raise ValueError
        host = url.hostname.lower()
        try:
            address = ipaddress.ip_address(host)
            host = str(address)
        except ValueError:
            if (len(host) > 253 or not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*", host)
                    or re.fullmatch(r"[0-9.]+", host)
                    or any(label.startswith("0x") for label in host.split("."))):
                raise ValueError
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        authority = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        if url.netloc.lower() not in (authority.lower(), f"{authority}:{port}".lower()) or not 1 <= port <= 65535:
            raise ValueError
        return ModelEndpoint(value, ModelOrigin(url.scheme, host, port))
    except (ValueError, TypeError):
        raise OutboundPolicyError() from None


@dataclass(frozen=True)
class ModelOutboundPolicy:
    local_origins: frozenset[ModelOrigin]

    @classmethod
    def deployment(cls) -> ModelOutboundPolicy:
        origins = []
        for value in get_settings().model_local_origins:
            endpoint = parse_endpoint(value)
            if urlsplit(value).path:
                raise OutboundPolicyError()
            origins.append(endpoint.origin)
        return cls(frozenset(origins))

    def endpoint(self, value: str) -> ModelEndpoint:
        endpoint = parse_endpoint(value)
        if endpoint.origin.scheme == "http" and endpoint.origin not in self.local_origins:
            raise OutboundPolicyError()
        try:
            literal = ipaddress.ip_address(endpoint.origin.host)
        except ValueError:
            return endpoint
        self.check_address(endpoint.origin, literal)
        return endpoint

    def check_address(self, origin: ModelOrigin, address) -> None:
        # An exact local exception permits private/loopback, never metadata or
        # ambiguous special-purpose destinations (including mapped IPv6).
        if (address.is_link_local or address.is_multicast
                or (address.is_reserved and not (address.is_loopback and origin in self.local_origins))
                or address.is_unspecified or getattr(address, "ipv4_mapped", None)
                or (not address.is_global and origin not in self.local_origins)):
            raise OutboundPolicyError()


async def _resolve(host: str, port: int, timeout: float) -> tuple[str, ...]:
    # Timed-out OS DNS work retains its slot until the real thread completes.
    # Both running work and queued work remain bounded even during cancellation.
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise OutboundPolicyError()
    try:
        future = _DNS_POOL.submit(socket.getaddrinfo, host, port, socket.AF_UNSPEC,
                                  socket.SOCK_STREAM, socket.IPPROTO_TCP)
    except BaseException:
        slots.release()
        raise
    future.add_done_callback(lambda _: slots.release())
    wrapped = asyncio.wrap_future(future)
    wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        answers = await asyncio.wait_for(asyncio.shield(wrapped), timeout)
        if not answers or len(answers) > 64 or any(a[0] not in (socket.AF_INET, socket.AF_INET6) for a in answers):
            raise ValueError
        return tuple(dict.fromkeys(a[4][0] for a in answers))
    except (OSError, ValueError, TimeoutError):
        raise OutboundPolicyError() from None


@dataclass(frozen=True)
class VettedDialTarget:
    ip: str
    port: int


async def vetted_target(endpoint: ModelEndpoint, policy: ModelOutboundPolicy,
                        timeout: float = _DNS_TIMEOUT) -> VettedDialTarget:
    origin = endpoint.origin
    try:
        addresses = (ipaddress.ip_address(origin.host),)
    except ValueError:
        raw = await _resolve(origin.host, origin.port, min(timeout, _DNS_TIMEOUT))
        try:
            addresses = tuple(ipaddress.ip_address(ip) for ip in raw)
        except ValueError:
            raise OutboundPolicyError() from None
    if not addresses:
        raise OutboundPolicyError()
    for address in addresses:
        policy.check_address(origin, address)
    return VettedDialTarget(str(addresses[0]), origin.port)


async def validate_model_url(value: str) -> None:
    """Settings save checks DNS too. Actual connection always checks again."""
    policy = ModelOutboundPolicy.deployment()
    await vetted_target(policy.endpoint(value), policy)


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, endpoint: ModelEndpoint, policy: ModelOutboundPolicy):
        self.endpoint, self.policy = endpoint, policy
        self._backend = AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        origin = self.endpoint.origin
        try:
            host = str(ipaddress.ip_address(host))
        except ValueError:
            host = host.lower()
        if (host, port) != (origin.host, origin.port) or local_address is not None:
            raise OutboundPolicyError()
        started = time.monotonic()
        target = await vetted_target(self.endpoint, self.policy, _DNS_TIMEOUT if timeout is None else timeout)
        remaining = None if timeout is None else max(0, timeout - (time.monotonic() - started))
        return await self._backend.connect_tcp(target.ip, target.port, timeout=remaining,
                                                socket_options=socket_options)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise OutboundPolicyError()

    async def sleep(self, seconds):
        await self._backend.sleep(seconds)


class PinnedModelTransport(httpx.AsyncHTTPTransport):
    """Reuse HTTPX's stream/error bridge; only replace its httpcore pool seam."""

    def __init__(self, endpoint: ModelEndpoint, policy: ModelOutboundPolicy):
        # HTTPX has no public network_backend argument. Supported version range
        # is explicit in requirements; no request/HTTP/TLS implementation copied.
        self.endpoint = endpoint
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=_verified_tls(),
            network_backend=PinnedNetworkBackend(endpoint, policy), retries=0,
        )

    async def handle_async_request(self, request):
        if (parse_endpoint(str(request.url)).origin != self.endpoint.origin
                or "sni_hostname" in request.extensions
                or request.headers.get("host") != httpx.URL(self.endpoint.url).netloc.decode("ascii")):
            raise OutboundPolicyError()
        return await super().handle_async_request(request)


def model_client(value: str, **kwargs) -> httpx.AsyncClient:
    policy = ModelOutboundPolicy.deployment()
    endpoint = policy.endpoint(value)
    return httpx.AsyncClient(transport=PinnedModelTransport(endpoint, policy),
                             trust_env=False, follow_redirects=False, **kwargs)


@lru_cache(maxsize=1)
def _verified_tls():
    return httpx.create_ssl_context(verify=True, trust_env=False)
