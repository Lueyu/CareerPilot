"""No real DNS/provider calls. Exercise the model network boundary with controlled peers."""
import asyncio
import ipaddress
import json
import socket
import ssl
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from app.config import get_settings
from app.services import model_outbound as outbound
from app.services.llm import AgentAdapter


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_private_billing")
@pytest.mark.parametrize("url", ["http://169.254.169.254/v1", "https://127.0.0.1/v1"])
async def test_unsafe_stored_config_never_sends_credentials(monkeypatch, url):
    original = httpx.AsyncClient
    sent = []

    def respond(request):
        sent.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "unsafe success"}}]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        **{**kw, "transport": httpx.MockTransport(respond)}))
    adapter = AgentAdapter()
    with pytest.raises(Exception) as error:
        await adapter.chat_with({"provider": "synthetic", "api_key": "synthetic-only",
            "model": "fake", "base_url": url}, "s", "u")
    assert error.value.__class__.__name__ == "OutboundPolicyError"
    assert not sent
    assert adapter.last_error == {"kind": "outbound_policy", "code": "MODEL_OUTBOUND_POLICY"}


@pytest.fixture(autouse=True)
def isolated_policy(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_local_origins", [])

    async def no_dns(*args):
        pytest.fail("Unexpected DNS; every test must install controlled answers")

    monkeypatch.setattr(outbound, "_resolve", no_dns)


def answers(monkeypatch, values):
    resolver = AsyncMock(return_value=tuple(values))
    monkeypatch.setattr(outbound, "_resolve", resolver)
    return resolver


@pytest.mark.parametrize("url", [
    "ftp://model.example/v1", "https://u:p@model.example", "https://model.example?key=secret",
    "https://model.example#fragment", "https://model.example\\@127.0.0.1", " https://model.example",
    "https://model.example\n", "https://model.example:0", "https://model.example:65536",
    "https://127.1", "https://2130706433", "https://0x7f000001", "https://0177.0.0.1",
    "https://[fe80::1%25eth0]", "https://model.example:", "https://model.example.",
    "https://127.0.0.1", "https://10.1.2.3", "https://169.254.169.254", "https://100.64.0.1",
    "https://0.0.0.0", "https://224.0.0.1", "https://240.0.0.1", "https://[::]",
    "https://[::1]", "https://[fd00::1]", "https://[fe80::1]", "https://[ff02::1]",
    "https://[::ffff:8.8.8.8]", "https://[::ffff:127.0.0.1]", "https://[2001:db8::1]",
])
def test_url_and_literal_boundary(url):
    with pytest.raises(outbound.OutboundPolicyError) as error:
        outbound.ModelOutboundPolicy.deployment().endpoint(url)
    assert error.value.detail == {"code": "MODEL_OUTBOUND_POLICY", "message": outbound.SAFE_MESSAGE}
    assert str(error.value) == outbound.SAFE_MESSAGE


@pytest.mark.asyncio
@pytest.mark.parametrize("ips", [
    ["127.0.0.1"], ["10.0.0.3"], ["169.254.169.254"], ["::1"], ["fd00::5"],
    ["::ffff:93.184.216.34"], ["93.184.216.34", "127.0.0.1"],
    ["2606:4700:4700::1111", "fe80::1"], [], ["invalid-address"],
])
async def test_all_dns_answers_checked_before_any_dial(monkeypatch, ips):
    answers(monkeypatch, ips)
    dial = AsyncMock(side_effect=AssertionError("unsafe connection"))
    monkeypatch.setattr(outbound.AnyIOBackend, "connect_tcp", dial)
    with pytest.raises(outbound.OutboundPolicyError):
        async with outbound.model_client("https://models.example/v1") as client:
            await client.post("https://models.example/v1/chat/completions", headers={"Authorization": "synthetic"})
    dial.assert_not_called()


@pytest.mark.asyncio
async def test_exact_deployment_exception_and_safe_special_addresses(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_local_origins", ["http://local.example:8100"])
    answers(monkeypatch, ["127.0.0.1", "10.0.0.5"])
    await outbound.validate_model_url("http://local.example:8100/v1")
    for url in ("http://local.example:8101/v1", "https://local.example:8100/v1",
                "http://127.0.0.1:8100/v1", "http://other.example:8100/v1"):
        with pytest.raises(outbound.OutboundPolicyError):
            await outbound.validate_model_url(url)
    for ip in ("169.254.169.254", "::ffff:127.0.0.1", "0.0.0.0", "ff02::1"):
        answers(monkeypatch, [ip])
        with pytest.raises(outbound.OutboundPolicyError):
            await outbound.validate_model_url("http://local.example:8100/v1")


class WireStream(httpcore.AsyncNetworkStream):
    def __init__(self, observation, body=b'{"ok":true}'):
        self.observation = observation
        self.response = (b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\n\r\n" + body)

    async def read(self, max_bytes, timeout=None):
        result, self.response = self.response, b""
        return result

    async def write(self, buffer, timeout=None):
        self.observation.setdefault("wire", bytearray()).extend(buffer)

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.observation.update(sni=server_hostname, check_hostname=ssl_context.check_hostname,
                                verify_mode=ssl_context.verify_mode)
        return self

    async def aclose(self):
        pass

    def get_extra_info(self, name):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("ips", [["93.184.216.34", "8.8.8.8"], ["2606:4700:4700::1111"]])
async def test_tcp_is_pinned_while_host_sni_and_certificate_identity_stay_logical(monkeypatch, ips):
    resolver = answers(monkeypatch, ips)
    observed = {}

    async def dial(self, host, port, **kwargs):
        observed.update(ip=host, port=port)
        assert ipaddress.ip_address(host)
        # Any unexpected second DNS lookup would now be unsafe.
        resolver.return_value = ("127.0.0.1",)
        return WireStream(observed)

    monkeypatch.setattr(outbound.AnyIOBackend, "connect_tcp", dial)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9998")
    monkeypatch.setenv("SSL_CERT_FILE", "no-environment-ca.pem")
    async with outbound.model_client("https://models.example:8443/v1") as client:
        response = await client.post("https://models.example:8443/v1/chat/completions")
        assert response.json() == {"ok": True}
        assert client.follow_redirects is False and client.trust_env is False
    assert observed["ip"] == ips[0] and observed["port"] == 8443
    assert observed["sni"] == "models.example" and observed["check_hostname"]
    assert observed["verify_mode"] == ssl.CERT_REQUIRED
    assert b"Host: models.example:8443\r\n" in observed["wire"]
    assert b"CONNECT " not in observed["wire"] and resolver.await_count == 1


@pytest.mark.asyncio
async def test_dns_changes_between_save_and_connect_are_rejected(monkeypatch):
    resolver = answers(monkeypatch, ["93.184.216.34"])
    await outbound.validate_model_url("https://models.example/v1")
    resolver.return_value = ("127.0.0.1",)
    dial = AsyncMock()
    monkeypatch.setattr(outbound.AnyIOBackend, "connect_tcp", dial)
    with pytest.raises(outbound.OutboundPolicyError):
        async with outbound.model_client("https://models.example/v1") as client:
            await client.post("https://models.example/v1/chat/completions")
    assert resolver.await_count == 2
    dial.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("host,ip", [
    ("[2606:4700:4700:0000:0000:0000:0000:1111]", "2606:4700:4700::1111"),
    ("[0:0:0:0:0:0:0:1]", "::1"),
])
async def test_expanded_ipv6_keeps_wire_identity_and_uses_canonical_literal(monkeypatch, host, ip):
    origin = f"https://{host}:8443"
    if ip == "::1":
        monkeypatch.setattr(get_settings(), "model_local_origins", [origin])
    observed = {}

    async def dial(self, actual, port, **kwargs):
        observed["dial"] = actual
        return WireStream(observed)

    monkeypatch.setattr(outbound.AnyIOBackend, "connect_tcp", dial)
    async with outbound.model_client(origin) as client:
        assert (await client.post(origin + "/v1")).status_code == 200
    assert observed["dial"] == ip
    assert f"Host: {host}:8443\r\n".encode() in observed["wire"]
    assert observed["sni"] == host[1:-1]


@pytest.mark.asyncio
async def test_real_tls_verifies_original_hostname_and_rejects_wrong_certificate(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "local.example")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1)).add_extension(
                x509.SubjectAlternativeName([x509.DNSName("local.example")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "synthetic.crt", tmp_path / "synthetic.key"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(cert_file, key_file)
    sni = []
    server_tls.set_servername_callback(lambda obj, name, context: sni.append(name))
    trusted = ssl.create_default_context(cafile=str(cert_file))
    monkeypatch.setattr(outbound, "_verified_tls", lambda: trusted)
    async with local_provider(monkeypatch, tls=server_tls) as (url, requests):
        async with outbound.model_client(url) as client:
            assert (await client.post(url + "/chat/completions", json={})).status_code == 200
        wrong = url.replace("local.example", "wrong.example")
        monkeypatch.setattr(get_settings(), "model_local_origins", [url.rsplit("/", 1)[0], wrong.rsplit("/", 1)[0]])
        async with outbound.model_client(wrong) as client:
            with pytest.raises(httpx.ConnectError):
                await client.post(wrong + "/chat/completions", json={})
        assert len(requests) == 1 and sni == ["local.example", "wrong.example"]


@pytest.mark.asyncio
async def test_http_settings_refuses_request_side_exception_and_sanitizes_dns_error(monkeypatch, db_session, secrets_key):
    from fastapi import FastAPI

    from app.api.deps import get_workspace, require_local_owner
    from app.api.v1 import settings as api
    from app.db import get_session

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[require_local_owner] = lambda: True
    app.dependency_overrides[get_workspace] = lambda: SimpleNamespace(id=1)
    monkeypatch.setattr(api, "get_llm_config", AsyncMock(return_value={"provider": "synthetic",
        "api_key": "", "base_url": "", "model": "fake"}))
    answers(monkeypatch, ["169.254.169.254"])
    body = {"provider": "synthetic", "api_key": "synthetic-only", "model": "fake",
            "base_url": "https://hidden.example/v1", "model_local_origins": ["https://hidden.example"]}
    # Mock the billing admission only for a policy-focused connection probe.
    monkeypatch.setattr("app.services.llm.reserve", AsyncMock(return_value="synthetic-policy-call"))
    from app.services.private_model_billing import Charge
    monkeypatch.setattr("app.services.llm.settle", AsyncMock(return_value=Charge("synthetic-policy-call", "UNKNOWN", None, 1)))
    monkeypatch.setattr(AgentAdapter, "_record_metric", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        for method, path in (("PUT", "/settings/llm"), ("POST", "/settings/llm/test")):
            response = await client.request(method, path, json=body)
            assert response.status_code == 422
            assert response.json()["detail"] == {"code": "MODEL_OUTBOUND_POLICY", "message": outbound.SAFE_MESSAGE}
            assert all(secret not in response.text for secret in ("169.254", "hidden.example", "synthetic-only"))
    assert not get_settings().model_local_origins and not db_session.new


@pytest.mark.asyncio
async def test_workspace_podcast_stored_url_reaches_policy(monkeypatch, db_session, test_workspace, secrets_key):
    from app.models import AppSetting
    from app.services.podcast_content import make_scripts
    from app.services.secret_storage import seal_setting

    key = f"llm:{test_workspace.id}"
    db_session.add(AppSetting(key=key, value=seal_setting(key, {"provider": "synthetic",
        "api_key": "synthetic-workspace", "model": "fake", "base_url": "https://127.0.0.1/v1"})))
    await db_session.commit()
    with pytest.raises(outbound.OutboundPolicyError):
        await make_scripts(db_session, test_workspace.id, title="合成问题", answer="合成参考答案",
                           topic="测试", series=False, source="合成夹具")


@pytest.mark.asyncio
async def test_late_dns_exception_is_consumed_without_raw_loop_log(monkeypatch):
    errors = []
    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    release = threading.Event()

    def failed(*args):
        release.wait(2)
        raise OSError("sensitive late DNS failure")

    monkeypatch.setattr(socket, "getaddrinfo", failed)
    try:
        with pytest.raises(outbound.OutboundPolicyError):
            await _REAL_RESOLVE("models.example", 443, .01)
        release.set()
        await asyncio.sleep(.05)
        assert not errors
    finally:
        release.set()
        loop.set_exception_handler(original_handler)


@pytest.mark.asyncio
async def test_tenant_relay_real_backend_denies_mixed_dns_before_forwarding(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from deploy.tenant.model_relay import RelayConfig, create_app

    token = "synthetic-tenant-token-01234567890123456789"
    app = create_app(RelayConfig("https://models.example/v1", "fake", "synthetic-upstream", token))
    resolver = answers(monkeypatch, ["93.184.216.34", "127.0.0.1"])
    dial = AsyncMock()
    monkeypatch.setattr(outbound.AnyIOBackend, "connect_tcp", dial)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        for stream in (False, True):
            response = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer " + token},
                json={"model": "fake", "stream": stream, "messages": [{"role": "user", "content": "synthetic"}]})
            assert response.status_code == 502
            assert response.json()["detail"] == {"code": "MODEL_OUTBOUND_POLICY", "message": outbound.SAFE_MESSAGE}
            assert "127.0" not in response.text and "synthetic-upstream" not in response.text
    assert resolver.await_count == 2
    dial.assert_not_called()


@pytest.mark.asyncio
async def test_transport_refuses_other_origins_host_and_tls_override(monkeypatch):
    resolver = answers(monkeypatch, ["93.184.216.34"])
    async with outbound.model_client("https://models.example/v1") as client:
        for url, extra in (
            ("https://other.example/v1", {}),
            ("https://models.example/v1", {"headers": {"Host": "other.example"}}),
            ("https://models.example/v1", {"extensions": {"sni_hostname": "other.example"}}),
        ):
            with pytest.raises(outbound.OutboundPolicyError):
                await client.post(url, **extra)
    resolver.assert_not_called()


@asynccontextmanager
async def local_provider(monkeypatch, *, redirect=False, tls=None):
    requests = []

    async def serve(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next((int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                           if line.lower().startswith(b"content-length:")), 0)
            payload = json.loads(await reader.readexactly(length)) if length else {}
            requests.append((headers, payload))
            if redirect:
                response = b"HTTP/1.1 307 Temporary Redirect\r\nLocation: http://127.0.0.1:1/secret\r\nContent-Length: 0\r\n\r\n"
            else:
                usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
                if payload.get("stream"):
                    body = b'data: {"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
                    body += b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                    body += b'data: ' + json.dumps({"choices": [], "usage": usage}).encode() + b'\n\ndata: [DONE]\n\n'
                else:
                    body = json.dumps({"choices": [{"message": {"content": "OK"}}], "usage": usage}).encode()
                media = b"text/event-stream" if payload.get("stream") else b"application/json"
                response = b"HTTP/1.1 200 OK\r\nContent-Type: " + media + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=tls)
    port = server.sockets[0].getsockname()[1]
    origin = f"{'https' if tls else 'http'}://local.example:{port}"
    monkeypatch.setattr(get_settings(), "model_local_origins", [origin])
    answers(monkeypatch, ["127.0.0.1"])
    try:
        yield origin + "/v1", requests
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_private_billing")
@pytest.mark.parametrize("stream", [False, True])
async def test_real_local_provider_requires_exact_allowance_and_retains_identity(monkeypatch, stream):
    dialed = []
    real_dial = outbound.AnyIOBackend.connect_tcp

    async def observe(self, host, port, **kwargs):
        dialed.append(host)
        return await real_dial(self, host, port, **kwargs)

    monkeypatch.setattr(outbound.AnyIOBackend, "connect_tcp", observe)
    async with local_provider(monkeypatch) as (url, requests):
        adapter = AgentAdapter()
        cfg = {"provider": "synthetic", "api_key": "synthetic-only", "model": "fake", "base_url": url}
        assert (await adapter.chat_with(cfg, "s", "u", stream=stream))["content"] == "OK"
        assert cfg["base_url"] == url and len(requests) == 1
        assert b"Authorization: Bearer synthetic-only" in requests[0][0]
        assert b"Host: local.example:" in requests[0][0]
        monkeypatch.setattr(get_settings(), "model_local_origins", [])
        with pytest.raises(outbound.OutboundPolicyError):
            await adapter.chat_with(cfg, "s", "u", stream=stream)
        assert len(requests) == 1 and dialed == ["127.0.0.1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_private_billing")
@pytest.mark.parametrize("stream", [False, True])
async def test_redirect_does_not_forward_key(monkeypatch, stream):
    dialed = []
    real_dial = outbound.AnyIOBackend.connect_tcp

    async def observe(self, host, port, **kwargs):
        dialed.append((host, port))
        return await real_dial(self, host, port, **kwargs)

    monkeypatch.setattr(outbound.AnyIOBackend, "connect_tcp", observe)
    async with local_provider(monkeypatch, redirect=True) as (url, requests):
        adapter = AgentAdapter()
        assert await adapter.chat_with({"provider": "synthetic", "api_key": "synthetic-only",
            "model": "fake", "base_url": url}, "s", "u", stream=stream) is None
        assert len(dialed) == len(requests) == 1
        assert dialed[0][1] != 1 and adapter.last_error == {"kind": "http", "status": 307}


@pytest.mark.asyncio
async def test_settings_save_and_test_errors_are_safe_and_have_no_write(monkeypatch, db_session, secrets_key):
    from app.api.v1 import settings as api
    from app.services import llm

    monkeypatch.setattr(llm, "get_llm_config", AsyncMock(return_value={}))
    monkeypatch.setattr(api, "get_llm_config", AsyncMock(return_value={"provider": "synthetic",
        "api_key": "", "base_url": "", "model": "fake"}))
    answers(monkeypatch, ["169.254.169.254"])
    config = {"provider": "synthetic", "api_key": "synthetic-only", "model": "fake",
              "base_url": "https://internal.example/v1"}
    with pytest.raises(outbound.OutboundPolicyError) as save:
        await api.update_llm(api.LlmUpdate(**config), db_session)
    assert "internal" not in str(save.value) and "169.254" not in str(save.value)
    assert not db_session.new and not db_session.dirty

    # Literal rejection happens before billing and never becomes a success fallback.
    config["base_url"] = "https://127.0.0.1/v1"
    with pytest.raises(outbound.OutboundPolicyError) as probe:
        await api.test_llm(api.LlmTest(**config), SimpleNamespace(id=1))
    assert "127.0" not in str(probe.value.detail) and "synthetic-only" not in str(probe.value.detail)


@pytest.mark.asyncio
async def test_dns_failure_is_fixed_safe_error(monkeypatch):
    monkeypatch.setattr(outbound, "_resolve", _REAL_RESOLVE)
    def failed(*args):
        raise OSError("private-address and credential details")
    monkeypatch.setattr(socket, "getaddrinfo", failed)
    with pytest.raises(outbound.OutboundPolicyError) as error:
        await outbound.validate_model_url("https://internal.example/v1")
    assert str(error.value) == outbound.SAFE_MESSAGE


_REAL_RESOLVE = outbound._resolve


@pytest.mark.asyncio
async def test_dns_timeout_and_cancellation_keep_worker_slots_bounded(monkeypatch):
    started, release = threading.Event(), threading.Event()
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(outbound, "_DNS_SLOTS", slots)
    monkeypatch.setattr(outbound, "_resolve", _REAL_RESOLVE)

    def stalled(*args):
        started.set()
        release.wait(5)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", stalled)
    try:
        with pytest.raises(outbound.OutboundPolicyError):
            await outbound._resolve("models.example", 443, .02)
        assert started.is_set()
        for _ in range(12):
            with pytest.raises(outbound.OutboundPolicyError):
                await outbound._resolve("models.example", 443, .02)
        assert not slots.acquire(blocking=False)
    finally:
        release.set()
        for _ in range(100):
            if slots.acquire(blocking=False):
                slots.release()
                break
            await asyncio.sleep(.01)
    # A cancelled caller also cannot free a still-running OS resolver slot.
    started.clear()
    release.clear()
    pending = asyncio.create_task(outbound._resolve("models.example", 443, 1))
    while not started.is_set():
        await asyncio.sleep(.001)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not slots.acquire(blocking=False)
    release.set()
    await asyncio.sleep(.03)
