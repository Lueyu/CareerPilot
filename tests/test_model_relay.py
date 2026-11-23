import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from deploy.tenant.model_relay import MAX_REQUEST_BYTES, ROUTE, STRONG_ROUTE, RelayConfig, create_app

TOKEN = "synthetic-tenant-token-0123456789"
KEY = "synthetic-provider-key"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "https://approved-model.example/v1"
MODEL = "approved-model"
PAYLOAD = {"model": MODEL, "messages": [{"role": "user", "content": "plain text"}]}


def test_registered_tiers_use_only_the_configured_connection():
    received = []

    async def provider(request):
        received.append((request.url.host, request.headers["authorization"], json.loads(request.content)["model"]))
        return httpx.Response(200, json={"choices": []})

    slow_key = "synthetic-strong-provider-key"
    other = RelayConfig("https://strong.example/v1", MODEL, slow_key, TOKEN)
    app = create_app(RelayConfig(BASE, MODEL, KEY, TOKEN), flash_model="quick", strong_connection=other,
                     transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        for route, model, status in ((ROUTE, "quick", 200), (ROUTE, MODEL, 200),
                                     (STRONG_ROUTE, MODEL, 200), (STRONG_ROUTE, "quick", 400),
                                     (ROUTE, "unconfigured", 400)):
            assert client.post(route, json={**PAYLOAD, "model": model}, headers=AUTH).status_code == status
    assert received == [("approved-model.example", "Bearer " + KEY, "quick"),
                        ("approved-model.example", "Bearer " + KEY, MODEL),
                        ("strong.example", "Bearer " + slow_key, MODEL)]


def test_tenant_only_receives_relay_credentials_for_both_tiers():
    from deploy.tenant.runtime import _compose

    upstream = {"MODEL_UPSTREAM_MODEL": MODEL, "MODEL_UPSTREAM_BASE_URL": BASE, "MODEL_UPSTREAM_API_KEY": KEY,
                "MODEL_FLASH_MODEL": "quick", "MODEL_STRONG_MODEL": MODEL,
                "MODEL_STRONG_BASE_URL": "https://strong.example/v1", "MODEL_STRONG_API_KEY": "strong-key"}
    networks = {"private": "10.9.0.0/28", "egress": "10.9.0.16/28", "repo_egress": "10.9.0.32/28"}
    services = _compose("seed-unit", "运营", "auth", "db", networks, upstream, TOKEN, "repo")["services"]
    for name in ("api", "worker"):
        env = services[name]["environment"]
        assert env["STUDY_FLASH_MODEL"] == "quick" and env["STUDY_STRONG_MODEL"] == MODEL
        assert env["LLM_API_KEY"] == env["STUDY_STRONG_API_KEY"] == TOKEN
        assert env["STUDY_STRONG_BASE_URL"] == "http://10.9.0.14:8080/strong/v1"
        assert KEY not in env.values() and "strong-key" not in env.values()
    assert services["model-relay"]["environment"]["MODEL_STRONG_API_KEY"] == "strong-key"


class FixtureSSE(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.closed = True


@pytest.fixture
def relay():
    requests = []
    sse = FixtureSSE()

    async def provider(request):
        payload = json.loads(await request.aread())
        requests.append((request, payload))
        prompt = payload["messages"][0]["content"]
        if prompt == "provider redirect":
            return httpx.Response(307, headers={"Location": "https://other.example/collect"})
        if prompt == "provider failure":
            return httpx.Response(400, json={"secret": KEY})
        if payload.get("stream"):
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=sse)
        return httpx.Response(200, headers={"Set-Cookie": "upstream=private"},
                              json={"choices": [{"message": {"content": "fixture reply"}}]})

    app = create_app(RelayConfig(BASE, MODEL, KEY, TOKEN), transport=httpx.MockTransport(provider))
    with TestClient(app) as client:
        yield client, requests, sse


def test_native_completion_and_sse_use_fixed_upstream(relay, monkeypatch):
    client, requests, sse = relay
    monkeypatch.setenv("HTTPS_PROXY", "http://unapproved.example:8888")
    response = client.post(ROUTE, json=PAYLOAD, headers={
        **AUTH, "Host": "chosen-by-tenant.example", "Cookie": "tenant=1",
        "Proxy-Authorization": "Basic unrelated", "X-API-Key": "foreign-key",
    })
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "fixture reply"
    assert "set-cookie" not in response.headers

    streaming_payload = {**PAYLOAD, "stream": True, "stream_options": {"include_usage": True},
                         "temperature": 0.2, "max_tokens": 32, "response_format": {"type": "json_object"}}
    with client.stream("POST", ROUTE, json=streaming_payload, headers=AUTH) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert list(response.iter_lines())[-2:] == ["data: [DONE]", ""]
    assert sse.closed

    assert [payload for _, payload in requests] == [PAYLOAD, streaming_payload]
    for request, _ in requests:
        assert str(request.url) == BASE + "/chat/completions"
        assert request.headers["host"] == "approved-model.example"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert not ({"proxy-authorization", "cookie", "x-api-key"} & request.headers.keys())


@pytest.mark.parametrize(("path", "payload", "headers", "status"), [
    (ROUTE, PAYLOAD, {}, 401),
    (ROUTE, PAYLOAD, {"Authorization": f"Bearer {KEY}"}, 401),
    (ROUTE, PAYLOAD, {"Authorization": "Bearer another-tenant"}, 401),
    ("/v1/responses", PAYLOAD, AUTH, 404),
    ("/v1/%63hat/completions", PAYLOAD, AUTH, 404),
    (ROUTE + "/", PAYLOAD, AUTH, 404),
    (ROUTE + "?base_url=https://external.example", PAYLOAD, AUTH, 404),
    (ROUTE, {**PAYLOAD, "model": "foreign-model"}, AUTH, 400),
    (ROUTE, {**PAYLOAD, "api_key": "foreign"}, AUTH, 400),
    (ROUTE, {**PAYLOAD, "tools": [{"type": "web_search"}]}, AUTH, 400),
    (ROUTE, {**PAYLOAD, "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://external.example/file"}},
    ]}]}, AUTH, 400),
    (ROUTE, {**PAYLOAD, "messages": [{"role": "tool", "content": "result"}]}, AUTH, 400),
])
def test_invalid_calls_never_reach_provider(relay, path, payload, headers, status):
    client, requests, _ = relay
    assert client.post(path, json=payload, headers=headers, follow_redirects=False).status_code == status
    assert requests == []


@pytest.mark.parametrize("prompt", ["provider redirect", "provider failure"])
def test_upstream_errors_never_redirect_or_expose_provider_body(relay, prompt):
    client, requests, _ = relay
    payload = {**PAYLOAD, "messages": [{"role": "user", "content": prompt}]}
    response = client.post(ROUTE, json=payload, headers=AUTH)
    assert response.status_code == 502
    assert KEY not in response.text
    assert "location" not in response.headers
    assert len(requests) == 1


@pytest.mark.parametrize("base", [
    "http://approved-model.example/v1", "https://localhost/v1", "https://127.0.0.1/v1",
    "https://actor:secret@approved-model.example/v1", BASE + "?target=elsewhere", BASE + "#elsewhere",
])
def test_invalid_upstream_base_is_rejected_at_startup(base):
    with pytest.raises(ValueError):
        create_app(RelayConfig(base, MODEL, KEY, TOKEN))


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True])
async def test_stream_starts_before_provider_finishes_and_closes(disconnect):
    first_delivered = asyncio.Event()

    class PausedProvider(FixtureSSE):
        async def __aiter__(self):
            yield b"data: first\n\n"
            await asyncio.wait_for(first_delivered.wait(), timeout=2)
            yield b"data: [DONE]\n\n"

    chunks = PausedProvider()

    async def provider(_):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=chunks)

    app = create_app(RelayConfig(BASE, MODEL, KEY, TOKEN), transport=httpx.MockTransport(provider))
    scope = {
        "type": "http", "asgi": {"spec_version": "2.4"}, "method": "POST",
        "path": ROUTE, "raw_path": ROUTE.encode(), "query_string": b"",
        "headers": [(b"authorization", AUTH["Authorization"].encode()), (b"content-type", b"application/json")],
    }
    output = []

    async def receive():
        return {"type": "http.request", "body": json.dumps({**PAYLOAD, "stream": True}).encode()}

    async def send(message):
        if message["type"] == "http.response.body":
            output.append(message["body"])
            if message["body"] == b"data: first\n\n":
                first_delivered.set()
                if disconnect:
                    raise OSError("client disconnected")

    if disconnect:
        with pytest.raises(ClientDisconnect):
            await app(scope, receive, send)
        assert output == [b"data: first\n\n"]
    else:
        await app(scope, receive, send)
        assert output == [b"data: first\n\n", b"data: [DONE]\n\n", b""]
    assert chunks.closed


def test_oversize_request_stops_before_upstream(relay):
    client, requests, _ = relay
    response = client.post(ROUTE, headers={**AUTH, "Content-Type": "application/json"},
                           content=b" " * (MAX_REQUEST_BYTES + 1))
    assert response.status_code == 413
    assert requests == []
