"""Check public release builds and exercise deployment with throwaway fixtures."""
import argparse
import http.client
import json
import os
import secrets
import shutil
import ssl
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODEL_KEY = "fixture-model-key"
SYNTHETIC_ENV = {
    "SEED_HOST": "localhost",
    "SEED_PUBLIC_ORIGIN": "https://localhost",
    "SEED_BIND_ADDRESS": "127.0.0.1",
    "SEED_HTTP_PORT": "0",
    "SEED_HTTPS_PORT": "0",
    "SEED_DB_ADMIN_PASSWORD": "a" * 64,
    "SEED_DB_PASSWORD": "b" * 64,
    "SEED_SESSION_SECRET": secrets.token_hex(32),
    "SEED_MODEL_BASE_URL": "https://model.invalid/v1",
    "SEED_MODEL_KEY": MODEL_KEY,
    "SEED_MODEL_NAME": "fixture",
    "SEED_INPUT_CNY_PER_MILLION": "1",
    "SEED_OUTPUT_CNY_PER_MILLION": "2",
    "SEED_MONTHLY_CNY": "50",
    "SEED_DAILY_USER_CNY": "1",
    "SEED_STORAGE_REGION": "fixture",
    "SEED_MODEL_PROVIDER_LABEL": "fixture",
}

# The client must receive the first event before it opens the gate.
PUBLIC_MAIN = """
import asyncio
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Route

gate = asyncio.Event()

async def stream(request):
    async def events():
        yield "data: " + request.url.scheme + "\\n\\n"
        await asyncio.wait_for(gate.wait(), timeout=10)
        yield "data: done\\n\\n"
    return StreamingResponse(events(), media_type="text/event-stream")

async def release(request):
    gate.set()
    return PlainTextResponse("ok")

async def health(request):
    return PlainTextResponse("ok")

app = Starlette(routes=[
    Route("/healthz", health),
    Route("/api/v1/stream", stream),
    Route("/api/v1/release", release, methods=["POST"]),
])
"""


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def check_release(work, config_only, restore_only=False, postgres_image="postgres:16-alpine", *, proxy_only=False):
    project = f"cp-seed-test-{uuid4().hex[:12]}"
    values = {**SYNTHETIC_ENV, "SEED_RELEASE_TAG": project, "SEED_POSTGRES_IMAGE": postgres_image}
    env_file = work / "runtime.conf"
    write(env_file, "".join(f"{key}={value}\n" for key, value in values.items()))
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("SEED_", "COMPOSE_", "FEEDBACK_"))
    }
    env.update(COMPOSE_PROJECT_NAME=project, SEED_ENV_FILE=str(env_file))
    ops = HERE if config_only else work / "deploy" / "public"

    def run(*args, **kwargs):
        return subprocess.run(args, env=env, check=True, text=True, **kwargs)

    def compose(*args, **kwargs):
        return run(
            "docker", "compose", "--env-file", str(env_file),
            "-f", str(ops / "compose.yaml"), *args, **kwargs,
        )

    if not config_only:
        fixture_sources(work, ops)

    quiet = compose("config", "--quiet", capture_output=True)
    assert not quiet.stdout and not quiet.stderr
    config = json.loads(compose("config", "--format", "json", capture_output=True).stdout)
    assert set(config["services"]) == {"db", "api", "web"}
    db, api, web = (config["services"][name] for name in ("db", "api", "web"))
    assert not db.get("ports") and not api.get("ports")
    assert all(port["host_ip"] == "127.0.0.1" for port in web["ports"])
    assert config["networks"]["db_net"]["internal"]
    assert api["environment"]["SEED_MODEL_KEY"] == MODEL_KEY
    assert "SEED_MODEL_KEY" not in web["environment"]
    print("Synthetic Compose config passed", flush=True)
    if config_only:
        return

    try:
        if restore_only:
            compose("up", "--no-build", "--pull", "never", "--wait", "db")
        else:
            compose("up", "--build", "--pull", "never", "--wait", "--wait-timeout", "120")
            check_proxy(compose)
        if not proxy_only:
            check_backup(work, ops, env, run, compose)
    finally:
        compose("down", "--volumes", "--remove-orphans")
        subprocess.run(
            ["docker", "image", "rm", f"careerpilot-seed-api:{project}",
             f"careerpilot-seed-web:{project}"],
            env=env, capture_output=True,
        )


def fixture_sources(work, ops):
    ops.mkdir(parents=True)
    for path in HERE.iterdir():
        if path.suffix == ".sh" or path.name in {"compose.yaml", "Caddyfile"}:
            shutil.copyfile(path, ops / path.name)
            (ops / path.name).chmod(0o755)
    for component in ("server", "web"):
        for name in ("Dockerfile.seed", "Dockerfile.seed.dockerignore"):
            (work / component).mkdir(exist_ok=True)
            shutil.copyfile(ROOT / component / name, work / component / name)
        write(work / component / ".env", "SYNTHETIC_PRIVATE_SENTINEL")
        write(work / component / "private.txt", "SYNTHETIC_PRIVATE_SENTINEL")
    write(work / "server" / "requirements.txt", "starlette\nuvicorn\n")
    write(work / "server" / "app" / "public_main.py",
          "from app.services import interview_import_text, text_extract\n" + PUBLIC_MAIN)
    for name in ("interview_import_text", "text_extract", "feedback_delivery"):
        write(work / "server" / "app" / "services" / f"{name}.py", "# fixture helper\n")
    write(work / "server" / "app" / "seed.py", "raise AssertionError('legacy seed')\n")
    write(work / "server" / "app" / "data" / "bank.json", '{"fixture": "private"}\n')
    write(work / "server" / "app" / "services" / "unlisted_fixture.py", "# excluded\n")
    write(work / "web" / "package.json",
          '{"name":"seed-fixture","scripts":{"build:seed":"node src/seed/build.ts"}}\n')
    write(work / "web" / "package-lock.json",
          '{"name":"seed-fixture","lockfileVersion":3,"packages":{"":{"name":"seed-fixture"}}}\n')
    write(work / "web" / "public" / "old-bank.json", '{"fixture": "private"}\n')
    write(work / "web" / "public" / "podcast-sw.js", "self.clients.claim()")
    write(work / "web" / "podcast-offline.html", "<html>offline entry</html>")
    write(work / "web" / "src" / "data" / "old-bank.json", '{"fixture": "private"}\n')
    write(work / "web" / "index.html", '<script type="module" src="/src/main.tsx"></script>')
    write(work / "web" / "src" / "main.tsx", 'import App from "./workspace-entry";')
    write(work / "web" / "src" / "seed" / "RegisteredApp.tsx", 'export { NativeWorkspace } from "../App";')
    write(work / "web" / "src" / "App.tsx", "export function NativeWorkspace() {}")
    write(work / "web" / "src" / "workspace-entry.ts", 'export { default } from "./App";')
    write(work / "web" / "src" / "data" / "demo.ts", "export const STATUS_META = {};")
    shutil.copyfile(ROOT / "web" / "vite.seed.config.ts", work / "web" / "vite.seed.config.ts")
    write(work / "web" / "src" / "seed" / "build.ts", """
const fs = require("node:fs");
for (const path of [
    ".env", "private.txt", "public/old-bank.json", "src/data/old-bank.json",
    "seed/index.html", "src/data/bank.json", "src/pages/legacyBank.ts", "src/api/localTransport.ts",
    "src/seed/fixture.test.ts", "src/seed/fixtures/private.txt",
]) {
    if (fs.existsSync(path)) throw new Error("excluded fixture entered build context: " + path);
}
fs.mkdirSync("dist-seed/static-assets", { recursive: true });
for (const path of [
    "index.html", "podcast-offline.html", "public/podcast-sw.js", "src/main.tsx", "src/App.tsx", "src/workspace-entry.ts",
    "src/seed/RegisteredApp.tsx", "src/data/demo.ts", "vite.seed.config.ts",
]) {
    if (!fs.existsSync(path)) throw new Error("missing registered entry: " + path);
}
fs.writeFileSync("dist-seed/index.html", "seed fixture");
fs.copyFileSync("podcast-offline.html", "dist-seed/podcast-offline.html");
fs.copyFileSync("public/podcast-sw.js", "dist-seed/podcast-sw.js");
fs.writeFileSync("dist-seed/static-assets/fixture.js", "console.log('fixture')");
""")
    for path in (
        "seed/index.html", "src/data/bank.json", "src/pages/legacyBank.ts", "src/api/localTransport.ts",
        "src/seed/fixture.test.ts", "src/seed/fixtures/private.txt",
    ):
        write(work / "web" / path, "SYNTHETIC_PRIVATE_SENTINEL")


def check_source_build():
    tag = f"cp-seed-source-{uuid4().hex[:12]}"
    api, builder, web = (f"{tag}-{part}" for part in ("api", "builder", "web"))

    def docker(*args):
        subprocess.run(["docker", *args], check=True)

    def build(component, image, *args):
        context = ROOT / component
        docker("build", "-f", str(context / "Dockerfile.seed"), "-t", image, *args, str(context))

    try:
        build("server", api)
        docker("run", "--rm", "--network", "none", api, "python", "-c", """
from pathlib import Path
from app.public_main import app

paths = app.openapi()["paths"]
assert "get" in paths["/api/v1/coach/notes/{note_id}/versions/{version_id}"]
assert "get" in paths["/api/v1/learning/daily-plan"]
assert "get" in paths["/api/v1/native/access"]
assert "get" in paths["/api/v1/native/bank/legacy-data"]
assert "post" in paths["/api/v1/product-feedback"]
assert "post" in paths["/api/v1/feedback"]
assert not any(Path(path).exists() for path in (
    ".env", "app/main.py", "app/api/v1", "app/data", "app/services/llm.py",
    "app/services/study_notes.py", "app/services/study_teaching.py",
))
""")
        build("web", builder, "--target", "build")
        docker("run", "--rm", "--network", "none", builder, "sh", "-ec", """
for path in .env src/data/bank.json src/pages/legacyBank.ts src/api/localTransport.ts seed/index.html; do
    test ! -e "$path"
done
test "$(find public -type f)" = public/podcast-sw.js
test -f podcast-offline.html
test -f src/main.tsx
test -f src/App.tsx
test -f src/data/demo.ts
test -f src/seed/RegisteredApp.tsx
test -f src/seed/legacyBank.ts
""")
        build("web", web)
        docker("run", "--rm", "--network", "none", "--entrypoint", "sh", web, "-ec", """
cd /srv/seed
test -f index.html
test -f podcast-offline.html
test -f podcast-sw.js
grep -Eq 'src="/static-assets/[^"]+[.]js"' index.html
test ! -e assets
test -z "$(find static-assets -type f -name '*.json' -print)"
if grep -Eq 'cp-dev-token|X-Api-Token|cp-login-token' static-assets/*.js; then
    exit 1
fi
test -z "$(find . -type f -name '*.map' -print)"
test ! -e src
test ! -e public
test ! -e /build
""")
    finally:
        subprocess.run(["docker", "image", "rm", api, builder, web], capture_output=True)
    print("Allowlisted API import, registered web build and private-file exclusion passed", flush=True)


def check_proxy(compose):
    compose("exec", "-T", "api", "python", "-c",
            "from pathlib import Path; "
            "assert not any(Path(p).exists() for p in "
            "('.env', 'private.txt', 'app/seed.py', 'app/data/bank.json', "
            "'app/services/unlisted_fixture.py'))")
    port = int(compose("port", "web", "443", capture_output=True).stdout.rsplit(":", 1)[1])
    for attempt in range(30):
        try:
            ca = compose("exec", "-T", "web", "cat",
                         "/data/caddy/pki/authorities/local/root.crt", capture_output=True).stdout
            ssl_context = ssl.create_default_context(cadata=ca)
            break
        except subprocess.CalledProcessError:
            if attempt == 29:
                raise
            time.sleep(1)

    def client():
        return http.client.HTTPSConnection("localhost", port, timeout=15, context=ssl_context)

    first = client()
    second = client()
    try:
        for route in ("/", "/podcasts", "/assets", "/assets/project/42"):
            first.request("GET", route)
            response = first.getresponse()
            assert response.status == 200, route
            assert response.read() == b"seed fixture", route
        for path, content in (("/podcast-offline.html", b"<html>offline entry</html>"), ("/podcast-sw.js", b"self.clients.claim()")):
            first.request("GET", path)
            response = first.getresponse()
            assert response.status == 200 and response.read() == content, path
        first.request("GET", "/static-assets/fixture.js")
        response = first.getresponse()
        assert response.status == 200
        assert response.read() == b"console.log('fixture')"
        for missing in ("/api/legacy", "/static-assets/missing.js"):
            first.request("GET", missing)
            response = first.getresponse()
            assert response.status == 404
            response.read()
        first.request("GET", "/api/v1/stream")
        response = first.getresponse()
        assert response.getheader("Content-Type").startswith("text/event-stream")
        assert response.readline() == b"data: https\n"
        second.request("POST", "/api/v1/release")
        assert second.getresponse().read() == b"ok"
        assert response.read() == b"\ndata: done\n\n"
    finally:
        first.close()
        second.close()
    print("Loopback HTTPS, project deep links, static assets, SSE and build-context isolation passed", flush=True)


def check_backup(work, ops, env, run, compose):
    """Verify retired entrypoints refuse work; real DR lives in common server tests."""
    import hashlib

    def database_snapshot():
        def sql(statement, database="careerpilot_seed"):
            return compose("exec", "-T", "db", "psql", "-XAt", "-v", "ON_ERROR_STOP=1",
                           "-U", "seed_admin", "-d", database, "-c", statement,
                           capture_output=True).stdout.strip()
        inventory = sql("SELECT datname FROM pg_database ORDER BY datname", "postgres")
        tables = sql("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        hashes = []
        for table in tables.splitlines():
            quoted = table.replace('"', '""')
            hashes.append(sql("SELECT md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]')) "
                              f'FROM public."{quoted}" t'))
        return inventory, tables, hashes

    sentinel = work / "legacy-rejection"
    sentinel.mkdir()
    archive = sentinel / "unchanged.pgdump.age"
    identity = sentinel / "synthetic-identity"
    archive.write_bytes(b"synthetic archive must remain unchanged")
    identity.write_bytes(b"synthetic external identity must remain unchanged")

    def files_snapshot():
        return {str(path.relative_to(work)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in work.rglob("*") if path.is_file()}

    before_files = files_snapshot()
    before_databases = database_snapshot()
    for script, args in (("backup.sh", ()), ("restore.sh", ("--dry-run", str(archive), str(identity))),
                         ("restore.sh", ("--keep", str(archive), str(identity)))):
        try:
            run("bash", str(ops / script), *args, capture_output=True)
        except subprocess.CalledProcessError as error:
            assert error.returncode == 78, (script, error.returncode)
            assert "disabled" in error.stderr.lower()
        else:
            raise AssertionError(f"retired {script} unexpectedly accepted work")
    assert files_snapshot() == before_files, "retired entrypoint modified fixture files"
    assert database_snapshot() == before_databases, "retired entrypoint changed database inventory"
    print("Legacy backup/restore refused with exit78; files and database inventory unchanged. "
          "No legacy DR pass claimed. Actual recovery: server/tests/test_backup_recovery.py and "
          "server/tests/test_recovery_quarantine.py via run_isolated_tests.py.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--config-only", action="store_true")
    mode.add_argument("--restore-only", action="store_true")
    mode.add_argument("--proxy-only", action="store_true")
    mode.add_argument("--build-only", action="store_true")
    parser.add_argument("--postgres-image", default="postgres:16-alpine")
    options = parser.parse_args()
    if options.build_only:
        check_source_build()
    else:
        with tempfile.TemporaryDirectory(prefix="cp-seed-release-") as workspace:
            check_release(Path(workspace), options.config_only, options.restore_only, options.postgres_image,
                          proxy_only=options.proxy_only)
