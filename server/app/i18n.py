"""Per-request interface and generation language for the two workspace editions."""
import json
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Literal

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

Locale = Literal["zh-CN", "en"]
_language: ContextVar[Locale] = ContextVar("careerpilot_language", default="zh-CN")


def negotiated_language(header: str) -> Locale:
    selected: Locale = "zh-CN"
    best = 0.0
    for item in header.split(","):
        tag, *params = item.strip().lower().split(";")
        primary = tag.strip().partition("-")[0]
        if primary not in {"zh", "en"}:
            continue
        try:
            weight = float(next((p.strip()[2:] for p in params if p.strip().startswith("q=")), "1"))
        except ValueError:
            continue
        if best < weight <= 1:
            selected = "en" if primary == "en" else "zh-CN"
            best = weight
    return selected


class LanguageMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        language = negotiated_language(Headers(scope=scope).get("accept-language", ""))

        async def send_with_language(response):
            if response["type"] == "http.response.start" and scope["path"].startswith("/api/"):
                headers = MutableHeaders(scope=response)
                headers["Content-Language"] = language
                headers.add_vary_header("Accept-Language")
            await send(response)

        with using_language(language):
            await self.app(scope, receive, send_with_language)


@contextmanager
def using_language(language: Locale):
    token = _language.set(language)
    try:
        yield
    finally:
        _language.reset(token)


def request_language() -> Locale:
    return _language.get()


@lru_cache(maxsize=2)
def _catalog(language: Locale) -> dict[str, str]:
    path = Path(__file__).with_name("locales") / f"{language}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def message(key: str, language: Locale | None = None) -> str:
    return _catalog(language or request_language())[key]
