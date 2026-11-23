"""Keep repository reads direct for personal mode and relay them in tenant containers."""

import os
from contextlib import asynccontextmanager

import httpx

_PROVIDERS = {
    "api.github.com": "github",
    "gitcode.com": "gitcode",
    "raw.gitcode.com": "gitcode_raw",
}


@asynccontextmanager
async def stream_repo(url: str, *, headers: dict, params: dict | None = None):
    relay = os.environ.get("REPO_RELAY_URL")
    if not relay:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", url, headers=headers, params=params) as response:
                yield response
        return

    upstream = httpx.URL(url)
    provider = _PROVIDERS.get(upstream.host)
    relay_token = os.environ.get("REPO_RELAY_TOKEN")
    if not provider or upstream.scheme != "https" or not relay_token or upstream.query or upstream.fragment:
        raise httpx.RequestError("Invalid repository relay configuration or request")
    body = {
        "provider": provider,
        "path": upstream.raw_path.decode("ascii"),
        "params": params or {},
        "token": (headers.get("Authorization", "").removeprefix("Bearer ") if provider == "github"
                  else headers.get("PRIVATE-TOKEN", "")),
        "raw": headers.get("Accept") == "application/vnd.github.raw+json",
    }
    async with httpx.AsyncClient(timeout=35.0, trust_env=False, follow_redirects=False) as client:
        async with client.stream("POST", relay.rstrip("/") + "/v1/repository/read",
                                 json=body, headers={"Authorization": f"Bearer {relay_token}"}) as response:
            yield response


async def get_repo(url: str, *, headers: dict, params: dict | None = None) -> httpx.Response:
    if not os.environ.get("REPO_RELAY_URL"):
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            return await client.get(url, headers=headers, params=params)
    async with stream_repo(url, headers=headers, params=params) as response:
        await response.aread()
        return response
