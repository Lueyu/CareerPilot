"""Read-only repository access for a single isolated native tenant."""

import asyncio
import os
import re
from hmac import compare_digest
from ipaddress import IPv4Address, ip_address
from typing import Literal
from urllib.parse import quote, unquote

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROUTE = "/v1/repository/read"
MAX_RESPONSE = 16 * 1024 * 1024
ORIGINS = {
    "github": "https://api.github.com",
    "gitcode": "https://gitcode.com",
    "gitcode_raw": "https://raw.gitcode.com",
}
TOKEN = re.compile(r"[!-~]{1,2048}")
NAME = re.compile(r"[A-Za-z0-9_.-]{1,200}")


class RepoRead(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider: Literal["github", "gitcode", "gitcode_raw"]
    path: str = Field(max_length=4096)
    params: dict[str, str] = Field(max_length=3)
    token: str = Field(max_length=2048, repr=False)
    raw: bool


def _segment(value: str) -> bool:
    return bool(value and value not in {".", ".."} and not re.search(r"[%\\/?#\x00-\x1f\x7f]", value))


def _endpoint(read: RepoRead) -> tuple[str, dict[str, str]]:
    parts = unquote(read.path, errors="strict").split("/")
    prefix = {
        "github": ["", "repos"],
        "gitcode": ["", "api", "v5", "repos"],
        "gitcode_raw": [""],
    }[read.provider]
    owner_repo = parts[len(prefix):len(prefix) + 2]
    tail = parts[len(prefix) + 2:]
    if (
        parts[:len(prefix)] != prefix or len(owner_repo) != 2
        or not all(NAME.fullmatch(p) and _segment(p) for p in owner_repo)
        or not all(_segment(p) for p in tail)
        or (read.token and not TOKEN.fullmatch(read.token))
    ):
        raise ValueError

    allowed = set()
    raw_host = read.provider == "gitcode_raw"
    if raw_host:
        if len(tail) < 3 or tail[0] != "raw" or read.token or read.raw:
            raise ValueError
    elif tail[:2] == ["git", "trees"] and len(tail) >= 3:
        allowed = {"recursive"} if read.provider == "github" else {"recursive", "page", "per_page"}
        tail = [*tail[:2], "/".join(tail[2:])]
    elif tail == ["commits"]:
        allowed = {"per_page", "sha"}
    elif tail[:1] == ["commits"] and len(tail) >= 2:
        tail = ["commits", "/".join(tail[1:])]
    elif tail[:1] == ["contents"] and len(tail) >= 2:
        allowed = {"ref"}
    elif read.provider == "github" and tail == ["readme"]:
        allowed = {"ref"}
    elif read.provider == "github" and len(tail) == 2 and tail[0] == "compare":
        sha = r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
        if not re.fullmatch(sha + r"\.\.\." + sha, tail[1]):
            raise ValueError
        allowed = {"per_page"}
    elif tail not in ([], ["languages"]):
        raise ValueError

    if read.raw and tail[0:1] not in (["readme"], ["contents"]):
        raise ValueError
    if set(read.params) - allowed:
        raise ValueError
    for key, value in read.params.items():
        if key in {"page", "per_page"} and not re.fullmatch(r"[1-9][0-9]{0,6}", value):
            raise ValueError
        if key == "recursive" and value != "1":
            raise ValueError
        if key in {"ref", "sha"} and (len(value) > 500 or not all(_segment(p) for p in value.split("/"))):
            raise ValueError

    path = "/".join(quote(p, safe="") for p in [*prefix, *owner_repo, *tail])
    headers = {"User-Agent": "CareerPilot-Local"}
    if read.provider == "github":
        headers["Accept"] = "application/vnd.github.raw+json" if read.raw else "application/vnd.github+json"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if read.token:
            headers["Authorization"] = f"Bearer {read.token}"
    elif not raw_host:
        headers["Accept"] = "application/json"
        if read.token:
            headers["PRIVATE-TOKEN"] = read.token
    return ORIGINS[read.provider] + path, headers


def create_app(tenant_token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    if len(tenant_token) < 32 or not TOKEN.fullmatch(tenant_token):
        raise ValueError("Invalid repository relay token")
    expected = ("Bearer " + tenant_token).encode("ascii")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    slots = asyncio.Semaphore(8)

    @app.post(ROUTE)
    async def read_repository(request: Request):
        if request.scope.get("raw_path") != ROUTE.encode() or request.scope.get("query_string"):
            raise HTTPException(404)
        auth = request.headers.getlist("authorization")
        if len(auth) != 1 or not compare_digest(auth[0].encode("latin-1"), expected):
            raise HTTPException(401)
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > 16 * 1024:
                raise HTTPException(413)
            payload.extend(chunk)
        try:
            read = RepoRead.model_validate_json(payload)
            url, headers = _endpoint(read)
        except (ValidationError, ValueError):
            raise HTTPException(400, "Invalid repository read") from None

        try:
            async with asyncio.timeout(32), slots:
                async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False, timeout=28) as client:
                    async with client.stream("GET", url, params=read.params, headers=headers) as upstream:
                        if 300 <= upstream.status_code < 400:
                            raise HTTPException(502, "Provider redirect refused")
                        response_headers = {"Cache-Control": "no-store"}
                        for name in ("retry-after", "x-ratelimit-remaining", "x-ratelimit-reset"):
                            value = upstream.headers.get(name, "")
                            if re.fullmatch(r"[0-9]{1,15}", value):
                                response_headers[name] = value
                        if upstream.status_code != 200:
                            status = upstream.status_code if upstream.is_error else 502
                            raise HTTPException(status, "Repository read failed", headers=response_headers)
                        content = bytearray()
                        async for chunk in upstream.aiter_bytes():
                            if len(content) + len(chunk) > MAX_RESPONSE:
                                raise HTTPException(502, "Repository response too large")
                            content.extend(chunk)
                        if content_type := upstream.headers.get("content-type"):
                            response_headers["Content-Type"] = content_type
                        for name in ("x-total", "x-total-count"):
                            value = upstream.headers.get(name, "")
                            if re.fullmatch(r"[0-9]{1,15}", value):
                                response_headers[name] = value
                        for entry in upstream.headers.get("link", "").split(","):
                            if 'rel="last"' in entry and (page := re.search(r"[?&]page=([0-9]{1,15})[&>]", entry)):
                                response_headers["Link"] = f'<{url}?page={page[1]}>; rel="last"'
                                break
                        return Response(bytes(content), headers=response_headers)
        except (httpx.HTTPError, TimeoutError):
            raise HTTPException(502, "Repository provider unavailable") from None

    return app


def main() -> None:
    import uvicorn

    address = ip_address(os.environ["REPO_RELAY_BIND_IP"])
    if not isinstance(address, IPv4Address) or not address.is_private or address.is_loopback or address.is_unspecified:
        raise ValueError("Expected repository relay private IPv4 address")
    uvicorn.run(create_app(os.environ["REPO_RELAY_TOKEN"]), host=str(address), port=8081, access_log=False)


if __name__ == "__main__":
    main()
