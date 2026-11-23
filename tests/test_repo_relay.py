import hashlib
from pathlib import Path
import sys

import httpx
import pytest

from deploy.tenant.repo_relay import ROUTE, create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from app.services import project_autopilot_sources, project_sources, repo_transport

TENANT = "tenant-a-fixture-secret-with-at-least-32-chars"
AUTH = {"Authorization": f"Bearer {TENANT}"}
SHA = hashlib.sha1(b"fixture commit").hexdigest()
PAYLOAD = {"provider": "github", "path": "/repos/fixture/project", "params": {}, "token": "fixture-pat", "raw": False}


@pytest.mark.asyncio
async def test_relay_rejects_out_of_scope_reads_and_redirects():
    calls = []

    def redirect(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.invalid", "Set-Cookie": "secrets"})

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(TENANT, transport=httpx.MockTransport(redirect))),
                                 base_url="http://relay") as relay:
        assert (await relay.post(ROUTE, json=PAYLOAD, headers={"Authorization": "Bearer other-tenant"})).status_code == 401
        assert (await relay.get(ROUTE, headers=AUTH)).status_code == 405
        for replacement in (
            {"provider": "https://elsewhere.invalid"},
            {"path": "/user"},
            {"path": "//elsewhere.invalid/repos/fixture/project"},
            {"path": "/repos/fixture/project/hooks"},
            {"path": "/repos/fixture/project/contents/%252e%252e/%252e%252e/user"},
            {"path": "/repos/fixture/project/contents/%2e%2e/hooks"},
            {"path": "/repos/fixture/project/contents/%5cuser"},
            {"path": "/repos/fixture/project/commits/%0d%0a"},
            {"path": "/repos/fixture/project/pulls"},
            {"params": {"access_token": "other-token"}},
            {"provider": "gitcode_raw", "path": "/fixture/project/raw/main/README.md"},
        ):
            assert (await relay.post(ROUTE, json=PAYLOAD | replacement, headers=AUTH)).status_code == 400
        assert not calls
        response = await relay.post(ROUTE, json=PAYLOAD, headers=AUTH)
        assert response.status_code == 502
        assert "location" not in response.headers and "set-cookie" not in response.headers
        assert len(calls) == 1
        assert calls[0].url == "https://api.github.com/repos/fixture/project"
        assert calls[0].method == "GET"
        assert calls[0].headers.get("authorization") == "Bearer fixture-pat"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["github", "gitcode"])
async def test_native_sources_read_pinned_manifest_and_file(kind, monkeypatch):
    token = f"{kind}-fixture-pat"
    base = "/repos/fixture/project" if kind == "github" else "/api/v5/repos/fixture/project"
    host = "api.github.com" if kind == "github" else "gitcode.com"
    file_path = "src/main.py"
    text = "print('fixture')\n"
    seen = []

    def provider(request):
        seen.append(request)
        path = request.url.path
        assert request.method == "GET"
        assert "cookie" not in request.headers
        if request.url.host == "raw.gitcode.com":
            assert kind == "gitcode"
            assert "private-token" not in request.headers and "authorization" not in request.headers
            assert path == f"/fixture/project/raw/{SHA}/{file_path}"
            return httpx.Response(200, text=text)
        assert request.url.host == host
        if kind == "github":
            assert request.headers["authorization"] == f"Bearer {token}"
            assert "private-token" not in request.headers
        else:
            assert request.headers["private-token"] == token
            assert "authorization" not in request.headers
        if path == base:
            return httpx.Response(200, json={"id": 1, "default_branch": "feature/fixture"})
        if path == base + "/commits/feature/fixture":
            assert request.url.raw_path.endswith(b"/commits/feature%2Ffixture")
            return httpx.Response(200, json={"sha": SHA})
        if path == base + f"/git/trees/{SHA}":
            if kind == "gitcode":
                assert request.url.params["page"] == "1"
            return httpx.Response(200, json={"tree": [{"path": file_path, "type": "blob", "sha": SHA, "size": len(text)}]})
        if path == base + "/contents/" + file_path:
            assert request.url.params["ref"] == SHA
            return httpx.Response(200, text=text)
        raise AssertionError(str(request.url))

    relay = create_app(TENANT, transport=httpx.MockTransport(provider))
    client_type = httpx.AsyncClient

    def client(*args, **kwargs):
        if "transport" not in kwargs:
            kwargs["transport"] = httpx.ASGITransport(app=relay)
        return client_type(*args, **kwargs)

    monkeypatch.setattr(repo_transport.httpx, "AsyncClient", client)
    monkeypatch.setenv("REPO_RELAY_URL", "http://tenant-relay:8081")
    monkeypatch.setenv("REPO_RELAY_TOKEN", TENANT)
    info = {"kind": kind, "owner": "fixture", "repo": "project"}
    repo = await project_sources.open_repository(info, token=token)
    assert repo.revision == SHA
    assert await repo.read_file(file_path) == text
    assert len(seen) == 4

    credentials = {f"{kind}_token": token, "unrelated_token": "fixture-sentinel"}
    snapshot = await project_autopilot_sources.probe_repository(info, credentials)
    manifest = await project_autopilot_sources.fetch_manifest_page(info, snapshot, None, credentials)
    source = await project_autopilot_sources.read_source(info, snapshot, manifest["entries"][0], credentials)
    assert source["content"] == text
    assert source["source_revision"] == SHA
    assert len(seen) == 8


@pytest.mark.asyncio
async def test_personal_transport_still_goes_direct(monkeypatch):
    monkeypatch.delenv("REPO_RELAY_URL", raising=False)
    direct = httpx.AsyncClient

    def provider(request):
        assert request.method == "GET"
        assert request.url == "https://api.github.com/repos/fixture/project"
        assert request.headers["authorization"] == "Bearer personal-fixture"
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(repo_transport.httpx, "AsyncClient", lambda **kw: direct(transport=httpx.MockTransport(provider), **kw))
    response = await repo_transport.get_repo("https://api.github.com/repos/fixture/project",
                                             headers={"Authorization": "Bearer personal-fixture"})
    assert response.json() == {"ok": True}
