"""Opt in from server/ with python run_isolated_tests.py tests/study_chat_crash_probe.py -s."""
import asyncio
import json
import os
import re
import subprocess
import sys
import sysconfig
import threading
import time
from contextlib import ExitStack
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from test_llm_stream_transport import DONE, frame

from app.models.study_chat import StudyMessage

SERVER = Path(__file__).resolve().parents[1]
LOOPBACK = "127.0.0.1"
TOKEN = "synthetic-crash-probe"
MODEL = "fixture-flash"
PARTIAL = "Durable first sentence."
FINAL = "Manual retry completed."

database_url = os.environ.get("CAREERPILOT_TEST_DATABASE_URL")
database = make_url(database_url) if database_url else None
pytestmark = pytest.mark.skipif(
    database is None
    or database.host not in {LOOPBACK, "::1", "localhost"}
    or not (database.database or "").endswith("_test")
    or not {"host", "port", "database", "dbname"}.isdisjoint(database.query),
    reason="Explicitly select this file through run_isolated_tests.py with loopback PostgreSQL",
)


async def until(check, *, timeout, label):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result := await check():
            return result
        await asyncio.sleep(0.2)
    pytest.fail(f"Timed out waiting for {label}")


@pytest.mark.asyncio
async def test_api_kill_restart_with_shared_database(db_session, test_workspace, tmp_path):
    expected_call = ("/v1/chat/completions", MODEL, True, f"Bearer {TOKEN}")
    calls = []
    release = threading.Event()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            call = (self.path, body.get("model"), body.get("stream"), self.headers.get("Authorization"))
            calls.append(call)
            if self.client_address[0] != LOOPBACK or call != expected_call:
                self.send_error(400, "Unexpected fixture request")
                return
            attempt = len(calls)
            print(f"fixture provider call {attempt}", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if attempt == 1:
                self.wfile.write(frame(PARTIAL))
                self.wfile.flush()
            release.wait(100)
            if attempt > 1:
                self.wfile.write(frame(FINAL) + frame(finish="stop") + DONE)
                self.wfile.flush()

    with ExitStack() as cleanup:
        provider = cleanup.enter_context(ThreadingHTTPServer((LOOPBACK, 0), Provider))
        assert provider.server_address[0] == LOOPBACK
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()
        cleanup.callback(provider.shutdown)
        cleanup.callback(release.set)
        # All HTTP is plain loopback. Avoid loading the host's CA bundle in these children.
        empty_ca = tmp_path / "empty-ca"
        empty_ca.mkdir()
        child_env = {
            **{name: value for name, value in os.environ.items() if not name.startswith("UVICORN_")},
            "DATABASE_URL": os.environ["CAREERPILOT_TEST_DATABASE_URL"],
            "API_TOKEN": TOKEN,
            "WORKSPACE_TOKEN_HASHES": "{}",
            "DEFAULT_WORKSPACE_SLUG": test_workspace.slug,
            "LLM_PROVIDER": "deepseek",
            "LLM_BASE_URL": f"http://{LOOPBACK}:{provider.server_port}/v1",
            "LLM_API_KEY": TOKEN,
            "LLM_MODEL": MODEL,
            "STUDY_FLASH_MODEL": MODEL,
            "STUDY_STRONG_MODEL": "",
            "STUDY_TIMEOUT_SECONDS": "1800",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "SSL_CERT_FILE": "",
            "SSL_CERT_DIR": str(empty_ca),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        launcher = [sys.executable, "-m", "uvicorn"]
        if sys.platform == "win32":
            # Kill Uvicorn itself instead of the Windows venv's redirector process.
            package_paths = sysconfig.get_paths()
            python_paths = dict.fromkeys((package_paths["purelib"], package_paths["platlib"], os.environ.get("PYTHONPATH")))
            child_env["PYTHONPATH"] = os.pathsep.join(filter(None, python_paths))
            launcher = [sys._base_executable, "-c",
                        "import runpy, sys; sys.modules['_wmi'] = None; runpy.run_module('uvicorn', run_name='__main__')"]

        def stop_process(process):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

        async def start_api(label):
            log_path = tmp_path / f"{label}.log"
            log = cleanup.enter_context(log_path.open("wb"))
            process = subprocess.Popen(
                [*launcher, "app.main:app", "--host", LOOPBACK, "--port", "0", "--workers", "1", "--no-use-colors"],
                cwd=SERVER, env=child_env, stdout=log, stderr=subprocess.STDOUT,
            )
            cleanup.callback(stop_process, process)

            async def bound_port():
                assert process.poll() is None, f"{label} exited. See {log_path}"
                match = re.search(rf"Uvicorn running on http://{re.escape(LOOPBACK)}:(\d+)",
                                  log_path.read_text(encoding="utf-8", errors="replace"))
                return int(match[1]) if match else None

            port = await until(bound_port, timeout=35, label=f"{label} listening")
            print(f"{label}: pid={process.pid} port={port}", flush=True)
            return process, f"http://{LOOPBACK}:{port}/api/v1/study/sessions"

        limits = httpx.Limits(max_keepalive_connections=0)
        async with httpx.AsyncClient(trust_env=False, headers={"X-Api-Token": TOKEN}, limits=limits, timeout=20) as client:
            async def post(path, status, body=None):
                response = await client.post(path, json=body)
                assert response.status_code == status, response.text
                return response.json()

            async def reply(path):
                response = await client.get(path)
                response.raise_for_status()
                messages = response.json()["messages"]
                assert len(messages) == 2
                return messages[-1]

            owner, initial = await start_api("owner")
            sid = (await post(initial, 201, {"mode": "auto"}))["id"]
            payload = {"request_id": "kill-restart", "content": "Explain recall in retrieval."}
            ticket = await post(f"{initial}/{sid}/messages", 202, payload)
            rid = ticket["assistant_message_id"]
            assert (ticket["status"], ticket["attempt"]) == ("generating", 1)

            async def saved_partial():
                row = await reply(f"{initial}/{sid}")
                assert row["status"] == "generating", row["error"]
                return row if row["content"] == PARTIAL else None

            async def lease():
                row = (await db_session.execute(select(StudyMessage.expires_at, func.clock_timestamp()).where(
                    StudyMessage.id == rid))).one()
                await db_session.rollback()
                return row

            partial = await until(saved_partial, timeout=25, label="durable partial reply")
            (first_call,) = partial["trace"]["calls"]
            assert (first_call["attempt"], first_call["generation"], first_call["requested_model"]) == (1, 1, MODEL)
            assert (first_call["outcome"], first_call["usage"]) == ("unknown", None)
            first_expiry, _ = await lease()
            print("provider accepted the first request and partial reply is durable", flush=True)
            _, peer = await start_api("peer")
            seen = await reply(f"{peer}/{sid}")
            assert (seen["status"], seen["content"], seen["attempt"]) == ("generating", PARTIAL, 1)

            async def renewed():
                expires_at, now = await lease()
                if expires_at > first_expiry + timedelta(seconds=5):
                    return expires_at, now

            expires_at, now = await until(renewed, timeout=15, label="lease renewal with a silent provider")
            assert timedelta(0) < expires_at - now <= timedelta(seconds=30)
            killed_at = time.monotonic()
            stop_process(owner)
            restart = asyncio.create_task(start_api("replacement"))

            async def interrupted():
                row = await reply(f"{peer}/{sid}")
                return row if row["status"] == "interrupted" else None

            try:
                resumed = await until(interrupted, timeout=32, label="interrupted reply after API kill")
                recovery_seconds = time.monotonic() - killed_at
                assert recovery_seconds <= 33
                _, replacement = await restart
            finally:
                if not restart.done():
                    restart.cancel()
                    await asyncio.gather(restart, return_exceptions=True)
            assert (resumed["id"], resumed["attempt"], resumed["content"]) == (rid, 1, PARTIAL)
            assert resumed["trace"]["calls"] == [first_call]
            assert await reply(f"{replacement}/{sid}") == resumed
            replay = await post(f"{replacement}/{sid}/messages", 202, payload)
            assert replay == {**ticket, "status": "interrupted"}
            assert calls == [expected_call]

            retries = await asyncio.gather(*(
                post(f"{base}/{sid}/messages/{rid}/retry", 202) for base in (peer, replacement)))
            assert retries == [{**ticket, "attempt": 2}] * 2

            async def retry_arrived():
                return len(calls) >= 2

            await until(retry_arrived, timeout=25, label="manual retry reaching the fixture provider")
            assert calls == [expected_call, expected_call]
            release.set()

            async def completed():
                row = await reply(f"{peer}/{sid}")
                assert row["status"] in {"generating", "completed"}, row["error"]
                return row if row["status"] == "completed" else None

            final = await until(completed, timeout=15, label="manual retry completion")
            assert (final["id"], final["attempt"], final["content"]) == (rid, 2, FINAL)
            interrupted_call, completed_call = final["trace"]["calls"]
            assert interrupted_call == {**first_call, "saved_output": PARTIAL}
            assert (completed_call["attempt"], completed_call["outcome"]) == (2, "completed")
            assert calls == [expected_call, expected_call]
            print(f"interrupted after {recovery_seconds:.2f}s, partial preserved, provider calls 1 before retry and 2 total")
