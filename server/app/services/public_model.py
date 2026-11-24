"""Workspace-scoped model calls for the isolated public API."""
import asyncio
import hashlib
import ipaddress
import json
import logging
import math
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation, localcontext
from functools import lru_cache
from ssl import SSLContext
from urllib.parse import SplitResult, urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.i18n import message as localized_message
from app.db import SessionLocal
from app.models.public_model import PublicModelCall, PublicModelMonth

logger = logging.getLogger(__name__)
_CHINA = timezone(timedelta(hours=8))
_MAX_INPUT_BYTES = 96 * 1024
_MAX_MESSAGES = 64
_MAX_OUTPUT_TOKENS = 8192
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_RESULT_CHARS = 128 * 1024
_PROVIDER_DEADLINE_SECONDS = 120
_NANO_CNY = 1_000_000_000
_LEDGER_LOCK = select(func.pg_advisory_xact_lock(20261123, 7401))


class PublicModelError(HTTPException):
    """Safe for HTTP responses or conversion to a terminal SSE error."""


@dataclass(frozen=True)
class _Meter:
    input_rate: int  # Millionths of a CNY per million tokens.
    output_rate: int
    monthly_limit: int  # Nano CNY.
    daily_limit: int

    def cost(self, prompt_tokens: int, completion_tokens: int) -> int:
        return (prompt_tokens * self.input_rate + completion_tokens * self.output_rate + 999) // 1000


@dataclass(frozen=True)
class _Provider:
    url: str
    key: str = field(repr=False)
    model: str
    meter: _Meter | None
    thinking: str | None


def _loopback_http(url: SplitResult) -> bool:
    if url.scheme != "http" or not url.hostname or "%" in url.hostname:
        return False
    try:
        return ipaddress.ip_address(url.hostname).is_loopback
    except ValueError:
        return False


def _decimal_env(name: str, default: str = "", *, allow_zero: bool = False) -> Decimal:
    raw = os.environ.get(name, default).strip()
    if not 0 < len(raw) <= 64:
        raise ValueError(name)
    value = Decimal(raw)
    if not value.is_finite() or value > 1_000_000 or value < 0 or (not allow_zero and value == 0):
        raise ValueError(name)
    return value


def _billing_env(name: str, scale: int, *, default: str = "", budget: bool = False) -> int:
    value = _decimal_env(name, default, allow_zero=budget)
    rounding = ROUND_FLOOR if budget else ROUND_CEILING
    with localcontext() as context:
        context.prec = 80
        units = int((value * scale).to_integral_value(rounding=rounding))
    return units if budget else max(1, units)


def _provider_config() -> _Provider:
    try:
        base = os.environ.get("SEED_MODEL_BASE_URL", "").strip().rstrip("/")
        key = os.environ.get("SEED_MODEL_KEY", "").strip()
        model = os.environ.get("SEED_MODEL_NAME", "").strip()
        thinking = os.environ.get("SEED_MODEL_THINKING", "").strip() or None
        url = urlsplit(base)
        if (
            not url.hostname or url.username is not None or "?" in base or "#" in base
            or any(char.isspace() for char in base) or url.port == 0
            or (url.scheme != "https" and not _loopback_http(url))
            or not key or not all(33 <= ord(char) <= 126 for char in key)
            or not 0 < len(model) <= 200
            or thinking not in (None, "enabled", "disabled")
        ):
            raise ValueError
        endpoint = str(httpx.URL(f"{base}/chat/completions"))
        meter = None
        if os.environ.get("SEED_LOCAL_UNMETERED", "").strip().lower() == "true":
            raw_origin = os.environ.get("SEED_PUBLIC_ORIGIN", "").strip()
            origin = urlsplit(raw_origin)
            if (
                not _loopback_http(origin) or origin.username is not None or origin.path
                or "?" in raw_origin or "#" in raw_origin or origin.port == 0
                or any(char.isspace() for char in raw_origin)
            ):
                raise ValueError
        else:
            meter = _Meter(
                _billing_env("SEED_INPUT_CNY_PER_MILLION", 1_000_000),
                _billing_env("SEED_OUTPUT_CNY_PER_MILLION", 1_000_000),
                _billing_env("SEED_MONTHLY_CNY", _NANO_CNY, default="50", budget=True),
                _billing_env("SEED_DAILY_USER_CNY", _NANO_CNY, budget=True),
            )
    except (ValueError, InvalidOperation, httpx.InvalidURL):
        raise PublicModelError(503, localized_message("errors.the_model_service_is_not_configured_yet_try_again")) from None
    return _Provider(endpoint, key, model, meter, thinking)


def _prepare(
    request_key: str, messages: list[dict], max_tokens: int
) -> tuple[str, list[dict[str, str]], int]:
    try:
        if (
            not isinstance(request_key, str) or not 0 < len(request_key) <= 256
            or not isinstance(messages, list) or not 0 < len(messages) <= _MAX_MESSAGES
            or type(max_tokens) is not int or not 0 < max_tokens <= _MAX_OUTPUT_TOKENS
        ):
            raise ValueError

        clean = []
        byte_count = 0
        for message in messages:
            if not isinstance(message, dict):
                raise ValueError
            role, content = message.get("role"), message.get("content")
            if role not in ("system", "user", "assistant") or not isinstance(content, str):
                raise ValueError
            if len(content) > _MAX_INPUT_BYTES:
                raise ValueError
            byte_count += len(content.encode("utf-8"))
            if byte_count > _MAX_INPUT_BYTES:
                raise ValueError
            clean.append({"role": role, "content": content})
        digest = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
    except (ValueError, UnicodeError):
        raise PublicModelError(422, localized_message("errors.the_model_request_is_invalid_or_too_long")) from None

    # Bound byte-level tokenization and allow for the provider's chat template.
    input_ceiling = byte_count + 64 * len(clean) + 256
    return digest, clean, input_ceiling


def _now() -> datetime:
    return datetime.now(_CHINA)


def _next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _quota_error(now: datetime, reset: date) -> PublicModelError:
    recovery = datetime.combine(reset, datetime.min.time(), tzinfo=_CHINA)
    retry_after = max(1, math.ceil((recovery - now).total_seconds()))
    return PublicModelError(
        429,
        f"模型额度已用完，预计北京时间 {recovery:%Y-%m-%d %H:%M} 恢复。",
        headers={"Retry-After": str(retry_after)},
    )


async def _claim(
    workspace_id: int, digest: str, input_ceiling: int, max_tokens: int
) -> _Provider | str:
    try:
        async with SessionLocal.begin() as db:
            # One transaction lock serializes site admission and settlement across workers.
            await db.execute(_LEDGER_LOCK)
            previous = await db.get(PublicModelCall, (workspace_id, digest))
            if previous is not None:
                if previous.result is None:
                    raise PublicModelError(409, localized_message("errors.this_request_was_already_submitted_there_is_no_reply"))
                return previous.result

            provider = _provider_config()
            now = _now()
            day = now.date()
            reserved = None
            if provider.meter is not None:
                meter = provider.meter
                reserved = meter.cost(input_ceiling, max_tokens)
                month_start = day.replace(day=1)
                month = await db.get(PublicModelMonth, month_start)
                site_spent = month.charged_nano_cny if month is not None else 0
                workspace_spent = await db.scalar(
                    select(func.coalesce(func.sum(PublicModelCall.charged_nano_cny), 0)).where(
                        PublicModelCall.workspace_id == workspace_id, PublicModelCall.day == day
                    )
                )
                if site_spent + reserved > meter.monthly_limit:
                    raise _quota_error(now, _next_month(day))
                if workspace_spent + reserved > meter.daily_limit:
                    raise _quota_error(now, day + timedelta(days=1))
                if month is None:
                    db.add(PublicModelMonth(month=month_start, charged_nano_cny=reserved))
                else:
                    month.charged_nano_cny += reserved

            db.add(PublicModelCall(
                workspace_id=workspace_id,
                key_digest=digest,
                day=day,
                reserved_nano_cny=reserved,
                charged_nano_cny=reserved,
            ))
    except SQLAlchemyError:
        raise PublicModelError(503, localized_message("errors.the_model_quota_service_is_temporarily_unavailable_try_again")) from None
    return provider


def _usage(raw: object, *, has_output: bool) -> tuple[int, int] | None:
    if not isinstance(raw, dict):
        return None
    prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
    if any(type(value) is not int or not 0 <= value <= 10_000_000 for value in (prompt, completion)):
        return None
    if prompt == 0 or (has_output and completion == 0):
        return None
    total = raw.get("total_tokens")
    if total is not None and (type(total) is not int or total != prompt + completion):
        return None
    return prompt, completion


async def _finish(
    workspace_id: int,
    digest: str,
    meter: _Meter | None,
    result: str | None,
    usage: tuple[int, int] | None,
) -> None:
    try:
        async with SessionLocal.begin() as db:
            await db.execute(_LEDGER_LOCK)
            call = await db.get(PublicModelCall, (workspace_id, digest))
            if call is None:
                raise PublicModelError(409, localized_message("errors.this_model_request_has_expired"))
            call.result = result
            if usage is None:
                return
            call.prompt_tokens, call.completion_tokens = usage
            if meter is not None:
                actual = meter.cost(*usage)
                month = await db.get(PublicModelMonth, call.day.replace(day=1))
                if actual > call.reserved_nano_cny:
                    logger.warning("Public model usage exceeded its reservation")
                month.charged_nano_cny += actual - call.charged_nano_cny
                call.charged_nano_cny = actual
    except SQLAlchemyError:
        raise PublicModelError(503, localized_message("errors.the_model_quota_service_is_temporarily_unavailable_try_again")) from None


def _provider_error() -> PublicModelError:
    return PublicModelError(502, localized_message("errors.the_model_returned_no_usable_reply_try_again_later"))


@lru_cache(maxsize=1)
def _verified_tls() -> SSLContext:
    return httpx.create_ssl_context(verify=True, trust_env=False)


@asynccontextmanager
async def _response(
    provider: _Provider, messages: list[dict[str, str]], max_tokens: int, *, stream: bool
) -> AsyncIterator[tuple[httpx.Response, float]]:
    payload = {"model": provider.model, "messages": messages, "max_tokens": max_tokens, "stream": stream}
    if provider.thinking:
        payload["thinking"] = {"type": provider.thinking}
    if stream:
        payload["stream_options"] = {"include_usage": True}

    deadline = asyncio.get_running_loop().time() + _PROVIDER_DEADLINE_SECONDS
    try:
        verify = _verified_tls() if provider.url.startswith("https://") else False
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0), trust_env=False, follow_redirects=False, verify=verify
        ) as client:
            request = client.build_request(
                "POST",
                provider.url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {provider.key}",
                    "Accept": "text/event-stream" if stream else "application/json",
                    "Accept-Encoding": "identity",
                },
            )
            async with asyncio.timeout_at(deadline):
                response = await client.send(request, stream=True)
            try:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise _provider_error()
                yield response, deadline
            finally:
                await response.aclose()
    except (httpx.TimeoutException, TimeoutError):
        raise PublicModelError(504, localized_message("errors.the_model_timed_out_try_again_later")) from None
    except (httpx.HTTPError, OSError):
        raise _provider_error() from None


async def _body(response: httpx.Response, deadline: float) -> AsyncIterator[bytes]:
    size = 0
    chunks = response.aiter_bytes()
    while True:
        try:
            async with asyncio.timeout_at(deadline):
                chunk = await anext(chunks)
        except StopAsyncIteration:
            return
        size += len(chunk)
        if size > _MAX_RESPONSE_BYTES:
            raise _provider_error()
        yield chunk


def _decode(raw: bytes | bytearray) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise _provider_error() from None
    if not isinstance(value, dict) or "error" in value:
        raise _provider_error()
    return value


def _content(event: dict, field_name: str) -> str:
    choices = event.get("choices")
    if choices == []:
        return ""
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise _provider_error()
    part = choices[0].get(field_name)
    if not isinstance(part, dict):
        raise _provider_error()
    value = part.get("content")
    if value is None:
        return ""
    if not isinstance(value, str) or "\x00" in value:
        raise _provider_error()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _provider_error() from None
    return value


async def _sse(response: httpx.Response, deadline: float) -> AsyncIterator[bytes]:
    pending = bytearray()
    data: list[bytes] = []
    async for chunk in _body(response, deadline):
        pending.extend(chunk)
        start = 0
        while (end := pending.find(b"\n", start)) != -1:
            line = bytes(pending[start:end]).removesuffix(b"\r")
            start = end + 1
            if not line and data:
                yield b"\n".join(data)
                data.clear()
            elif line.startswith(b"data:"):
                data.append(line[5:].removeprefix(b" "))
        if start:
            del pending[:start]


async def complete_text(
    workspace_id: int, request_key: str, messages: list[dict], *, max_tokens: int = 1200
) -> str:
    """Return answer text. A completed workspace/key replays across both call modes."""
    digest, payload, input_ceiling = _prepare(request_key, messages, max_tokens)
    claim = await _claim(workspace_id, digest, input_ceiling, max_tokens)
    if isinstance(claim, str):
        return claim

    async with _response(claim, payload, max_tokens, stream=False) as (response, deadline):
        body = bytearray()
        async for chunk in _body(response, deadline):
            body.extend(chunk)
    event = _decode(body)
    result = _content(event, "message")
    usable = bool(result.strip()) and len(result) <= _MAX_RESULT_CHARS
    usage = _usage(event.get("usage"), has_output=bool(result))
    await _finish(workspace_id, digest, claim.meter, result if usable else None, usage)
    if not usable:
        raise _provider_error()
    return result


async def stream_text(
    workspace_id: int, request_key: str, messages: list[dict], *, max_tokens: int = 1200
) -> AsyncIterator[str]:
    """Preflight on first iteration. A completed key replays as a single fragment."""
    digest, payload, input_ceiling = _prepare(request_key, messages, max_tokens)
    claim = await _claim(workspace_id, digest, input_ceiling, max_tokens)
    if isinstance(claim, str):
        yield claim
        return

    parts = []
    size = 0
    raw_usage = None
    done = False
    async with _response(claim, payload, max_tokens, stream=True) as (response, deadline):
        async for data in _sse(response, deadline):
            if data == b"[DONE]":
                done = True
                break
            event = _decode(data)
            if event.get("usage") is not None:
                raw_usage = event["usage"]
            fragment = _content(event, "delta")
            if fragment:
                size += len(fragment)
                if size > _MAX_RESULT_CHARS:
                    raise _provider_error()
                parts.append(fragment)
                yield fragment

    if not done:
        raise _provider_error()
    result = "".join(parts)
    usable = bool(result.strip())
    usage = _usage(raw_usage, has_output=bool(result))
    await _finish(workspace_id, digest, claim.meter, result if usable else None, usage)
    if not usable:
        raise _provider_error()
