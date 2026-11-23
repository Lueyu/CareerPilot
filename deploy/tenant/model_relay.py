"""Text-only OpenAI-compatible sidecar for one isolated native tenant."""

import asyncio
import os
import re
from dataclasses import dataclass, field
from hmac import compare_digest
from ipaddress import IPv4Address, ip_address, ip_network
from typing import Annotated, Literal

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import Response, StreamingResponse

ROUTE = "/v1/chat/completions"
STRONG_ROUTE = "/strong" + ROUTE
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
TOTAL_TIMEOUT = 1800
PRIVATE_NETWORKS = tuple(ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
HEADER_TOKEN = re.compile(r"[!-~]+")


@dataclass(frozen=True)
class RelayConfig:
    upstream_base_url: str
    upstream_model: str
    upstream_api_key: str = field(repr=False)
    tenant_token: str = field(repr=False)

    def endpoint(self) -> httpx.URL:
        url = httpx.URL(self.upstream_base_url)
        if (
            url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment
            or not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*/?", url.raw_path.decode("ascii"))
            or url.host.rstrip(".").lower() == "localhost"
        ):
            raise ValueError("Expected an operator-approved external HTTPS model API base")
        try:
            address = ip_address(url.host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError("Model API IP must be public")
        if (
            len(self.tenant_token) < 32 or not HEADER_TOKEN.fullmatch(self.tenant_token)
            or not HEADER_TOKEN.fullmatch(self.upstream_api_key)
            or compare_digest(self.tenant_token, self.upstream_api_key)
            or not self.upstream_model.strip()
        ):
            raise ValueError("Invalid model, tenant token, or upstream key configuration")
        return url.copy_with(path=url.path.rstrip("/") + "/chat/completions")


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Message(StrictInput):
    role: Literal["system", "user", "assistant", "developer"]
    content: str


class StreamOptions(StrictInput):
    include_usage: bool


class ResponseFormat(StrictInput):
    type: Literal["text", "json_object"]


class Thinking(StrictInput):
    type: Literal["enabled", "disabled"]


class Completion(StrictInput):
    model: str
    messages: Annotated[list[Message], Field(min_length=1, max_length=256)]
    stream: bool = False
    stream_options: StreamOptions | None = None
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    max_tokens: Annotated[int, Field(ge=1, le=131072)] | None = None
    response_format: ResponseFormat | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    thinking: Thinking | None = None


class RelayStream(StreamingResponse):
    def __init__(self, chunks, close):
        super().__init__(chunks, media_type="text/event-stream", headers={"Cache-Control": "no-store"})
        self._close_upstream = close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    await self._close_upstream()


async def _read_input(request: Request) -> Completion:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Expected application/json")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "Request exceeds the size limit")
        body.extend(chunk)
    try:
        return Completion.model_validate_json(body)
    except ValidationError:
        raise HTTPException(400, "Invalid completion request") from None


def create_app(config: RelayConfig, *, flash_model: str = "", strong_model: str = "",
               strong_connection: RelayConfig | None = None,
               transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    endpoint = config.endpoint()
    approved = {(ROUTE, model): (endpoint, config.upstream_api_key)
                for model in (config.upstream_model, flash_model, strong_model) if model}
    if strong_connection is not None:
        approved[(STRONG_ROUTE, strong_connection.upstream_model)] = (
            strong_connection.endpoint(), strong_connection.upstream_api_key)
    expected_auth = ("Bearer " + config.tenant_token).encode("ascii")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    slots = asyncio.Semaphore(4)

    @app.post(ROUTE)
    @app.post(STRONG_ROUTE)
    async def complete(request: Request):
        path = request.scope["path"]
        if request.scope.get("raw_path", b"") != path.encode() or request.scope.get("query_string"):
            raise HTTPException(404, "Unknown model route")
        auth = request.headers.getlist("authorization")
        if len(auth) != 1 or not compare_digest(auth[0].encode("latin-1"), expected_auth):
            raise HTTPException(401, "Invalid relay token")
        if slots.locked():
            raise HTTPException(429, "Relay is busy")
        await slots.acquire()

        client = httpx.AsyncClient(
            transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(TOTAL_TIMEOUT, connect=10),
        )
        upstream = None
        handed_off = False
        closed = False

        async def close():
            nonlocal closed
            if closed:
                return
            closed = True
            try:
                with anyio.CancelScope(shield=True):
                    try:
                        if upstream is not None:
                            await upstream.aclose()
                    finally:
                        await client.aclose()
            finally:
                slots.release()

        async def response_chunks(deadline):
            size = 0
            try:
                async with asyncio.timeout_at(deadline):
                    async for chunk in upstream.aiter_bytes(chunk_size=None):
                        size += len(chunk)
                        if size > MAX_RESPONSE_BYTES:
                            raise ValueError("Model response exceeds the size limit")
                        yield chunk
            finally:
                await close()

        deadline = asyncio.get_running_loop().time() + TOTAL_TIMEOUT
        try:
            async with asyncio.timeout_at(deadline):
                payload = await _read_input(request)
                destination = approved.get((path, payload.model))
                if destination is None or (payload.stream_options and not payload.stream):
                    raise HTTPException(400, "Unsupported completion request")
                url, key = destination
                upstream = await client.send(
                    client.build_request(
                        "POST", url,
                        json=payload.model_dump(exclude_none=True, exclude_unset=True),
                        headers={
                            "Authorization": "Bearer " + key,
                            "Accept": "text/event-stream" if payload.stream else "application/json",
                            "Accept-Encoding": "identity",
                        },
                    ),
                    stream=True,
                )
                if upstream.status_code == 429:
                    raise HTTPException(429, "Upstream model is rate limited")
                media_type = "text/event-stream" if payload.stream else "application/json"
                if (
                    upstream.status_code != 200
                    or upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower() != media_type
                    or upstream.headers.get("content-encoding", "identity").lower() != "identity"
                ):
                    raise HTTPException(502, "Unexpected upstream model response")
                if payload.stream:
                    # Headers and cookies from the provider never become relay response headers.
                    response = RelayStream(response_chunks(deadline), close)
                    handed_off = True
                    return response
                body = bytearray()
                async for chunk in response_chunks(deadline):
                    body.extend(chunk)
                body = bytes(body)
                return Response(body, media_type=media_type, headers={"Cache-Control": "no-store"})
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise HTTPException(502, "Model request failed") from None
        finally:
            if not handed_off:
                await close()

    return app


def main():
    import uvicorn

    bind_ip = ip_address(os.environ["MODEL_RELAY_BIND_IP"])
    if not isinstance(bind_ip, IPv4Address) or not any(bind_ip in network for network in PRIVATE_NETWORKS):
        raise ValueError("MODEL_RELAY_BIND_IP must be the relay's private tenant IPv4 address")
    token = os.environ["MODEL_RELAY_TOKEN"]
    strong = os.environ.get("MODEL_STRONG_MODEL", "")
    other = None
    if os.environ.get("MODEL_STRONG_BASE_URL"):
        other = RelayConfig(os.environ["MODEL_STRONG_BASE_URL"], strong, os.environ["MODEL_STRONG_API_KEY"], token)
    app = create_app(RelayConfig(
        upstream_base_url=os.environ["MODEL_UPSTREAM_BASE_URL"],
        upstream_model=os.environ["MODEL_UPSTREAM_MODEL"],
        upstream_api_key=os.environ["MODEL_UPSTREAM_API_KEY"],
        tenant_token=token,
    ), flash_model=os.environ.get("MODEL_FLASH_MODEL", ""), strong_model="" if other else strong,
        strong_connection=other)
    uvicorn.run(
        app, host=str(bind_ip), port=int(os.environ.get("MODEL_RELAY_PORT", "8080")),
        proxy_headers=False, access_log=False, server_header=False,
    )


if __name__ == "__main__":
    main()
