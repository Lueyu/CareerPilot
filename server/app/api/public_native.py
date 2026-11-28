"""Expose the original product on the registered origin, with a server-selected workspace."""
import json
import os
from pathlib import Path
from typing import Annotated

import httpx
from anyio import CancelScope
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import error_response
from app.api.public_auth import current_workspace
from app.db import get_session
from app.i18n import message as localized_message
from app.models import Workspace
from app.services.public_native_runtime import NativeTarget, is_bound_owner, native_target, target_client

router = APIRouter(prefix="/native")
Member = Annotated[Workspace, Depends(current_workspace)]
Database = Annotated[AsyncSession, Depends(get_session)]


def reject_json_constant(_value):
    raise ValueError("Non-finite JSON constant")


@router.get("/access")
async def native_access(ws: Member):
    return {"personal_owner": is_bound_owner(ws)}


@router.get("/bank/legacy-data")
async def native_legacy_bank(ws: Member, db: Database):
    await db.close()
    if not is_bound_owner(ws):
        return {"topics": [], "questions": []}
    path = Path(os.environ.get("SEED_NATIVE_OWNER_BANK", ""))
    if not path.is_file():
        raise HTTPException(503, localized_message("errors.the_original_local_question_bank_is_not_connected"))
    return FileResponse(path, media_type="application/json")


class NativeStream(StreamingResponse):
    def __init__(self, upstream, headers, close):
        super().__init__(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers)
        self.close_upstream = close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.close_upstream()


async def forward(request: Request, path: str, target: NativeTarget):
    parts = path.split("/")
    if (not path or any(part in {".", ".."} for part in parts) or "\\" in path or "%" in path
            or any(ord(ch) < 32 for ch in path)):
        raise HTTPException(400, localized_message("errors.invalid_workspace_path"))
    headers = {"X-Api-Token": target.api_token, "X-Workspace": target.workspace, "Accept-Encoding": "identity"}
    for name in ("Content-Type", "Accept", "Accept-Language", "Last-Event-ID"):
        if name in request.headers:
            headers[name] = request.headers[name]
    reserved = {"expected_workspace_id", "access_token", "api_token", "x-api-token", "authorization",
                "x-workspace", "x-expected-workspace"}
    query = [(key, value) for key, value in request.query_params.multi_items() if key.lower() not in reserved]
    url = httpx.URL(target.base_url).copy_with(path="/api/v1/" + path)
    streaming = path.rstrip("/").endswith("/stream") or path == "stream" or "text/event-stream" in request.headers.get("accept", "").lower()
    client = target_client(target, streaming)
    body = request.stream() if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None
    try:
        upstream = await client.send(client.build_request(request.method, url, params=query, headers=headers, content=body), stream=True)
    except httpx.RequestError:
        raise HTTPException(503, localized_message("errors.the_workspace_service_is_temporarily_unavailable_try_again_later")) from None

    async def close():
        with CancelScope(shield=True):
            await upstream.aclose()

    if 300 <= upstream.status_code < 400:
        await close()
        raise HTTPException(502, localized_message("errors.the_workspace_returned_an_unexpected_redirect"))
    if path == "settings/workspaces" and upstream.is_success:
        # The native owner service also knows about demo and other local workspaces.
        try:
            result = json.loads(await upstream.aread())
            return JSONResponse({"default_slug": target.workspace, "workspaces": [
                item for item in result["workspaces"] if item["slug"] == target.workspace
            ]})
        finally:
            await close()
    response_headers = {name: upstream.headers[name] for name in (
        "Content-Type", "Content-Disposition", "Content-Encoding", "Content-Length", "Retry-After", "WWW-Authenticate"
    ) if name in upstream.headers}
    if upstream.status_code >= 400:
        # This is a second HTTP hop. Use the gateway's transport ID in its envelope
        # and header, preserving the upstream business detail and status.
        try:
            if upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in {"", "application/json"}:
                raise HTTPException(502, "Invalid workspace error response.")
            body = bytearray()
            async for chunk in upstream.aiter_bytes():
                if len(body) + len(chunk) > 64 * 1024:
                    raise HTTPException(502, "Workspace error response exceeded the limit.")
                body.extend(chunk)
            try:
                detail = json.loads(body, parse_constant=reject_json_constant)["detail"]
                return error_response(request.scope, upstream.status_code, detail,
                                      {key: value for key, value in response_headers.items()
                                       if key.lower() in {"retry-after", "content-disposition", "www-authenticate"}})
            except (ValueError, KeyError, TypeError, RecursionError):
                raise HTTPException(502, "Invalid workspace error response.") from None
        finally:
            await close()
    return NativeStream(upstream, response_headers, close)


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def native_request(path: str, request: Request, db: Database, ws: Member) -> Response:
    if request.method != "GET" and "x-expected-workspace" not in request.headers:
        raise HTTPException(409, detail={"code": "workspace_changed", "message": localized_message("errors.refresh_the_page_before_submitting")})
    if path.strip("/") == "product-feedback":
        raise HTTPException(404)
    target = await native_target(db, ws)
    return await forward(request, path, target)
