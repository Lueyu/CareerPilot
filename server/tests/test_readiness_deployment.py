"""Authoritative deployment probe and reverse-proxy contracts, without Docker or Git writes."""
import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "agent"))

import candidate_delivery as delivery  # noqa: E402
from deploy.tenant.runtime import _compose  # noqa: E402


def test_personal_compose_health_uses_readiness_before_worker():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    probe = services["server"]["healthcheck"]
    assert probe["test"][-1] == "http://127.0.0.1:8000/readyz"
    assert probe["timeout"] == "3s"
    assert services["worker"]["depends_on"]["server"]["condition"] == "service_healthy"


def test_public_compose_readiness_preserves_host_and_client_timeout():
    compose = yaml.safe_load((ROOT / "deploy/public/compose.yaml").read_text(encoding="utf-8"))
    health = compose["services"]["api"]["healthcheck"]
    source = health["test"][-1]
    assert 'connection.request("GET", "/readyz", headers={"Host": host})' in source
    assert 'urlsplit(environ["SEED_PUBLIC_ORIGIN"]).netloc' in source
    assert 'HTTPConnection("127.0.0.1", 8000, timeout=2)' in source
    assert health["timeout"] == "3s"


def test_tenant_generated_api_readiness_keeps_worker_dependency():
    compose = _compose("probe-fixture", "Fixture", "synthetic-token", "synthetic-password",
                       {"private": "172.29.208.0/24"}, {}, "", "synthetic-repo-token")
    services = compose["services"]
    assert services["api"]["healthcheck"]["test"][-1] == "http://127.0.0.1:8000/readyz"
    assert services["worker"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["worker"]["healthcheck"]["test"] == ["CMD", "test", "-f", "/tmp/worker-ready"]


def test_preview_probes_careerpilot_readiness_and_preserves_squabble():
    assert delivery.PROJECTS["careerpilot"][3] == "/readyz"
    assert delivery.PROJECTS["squabble"] == ("docker-compose.demo.yml", "app", 3000, "/api/health", "Squabble")
    for path in ("/healthz", "/readyz"):
        assert f"location = {path} {{ proxy_pass http://server:8000; }}" in delivery.NGINX


def test_local_web_and_public_caddy_expose_exact_probe_routes():
    nginx = (ROOT / "web/nginx.local.conf").read_text(encoding="utf-8")
    for path in ("/healthz", "/readyz"):
        block = nginx.split(f"location = {path} {{", 1)[1].split("}", 1)[0]
        assert "set $api_backend server:8000;" in block
        assert "proxy_pass http://$api_backend;" in block
        assert "try_files" not in block
    caddy = (ROOT / "deploy/public/Caddyfile").read_text(encoding="utf-8")
    assert "@probes path /healthz /readyz" in caddy
    assert "handle @probes {\n\t\treverse_proxy api:8000\n\t}" in caddy


def test_public_image_ships_shared_readiness_module():
    allowlist = (ROOT / "server/Dockerfile.seed.dockerignore").read_text(encoding="utf-8").splitlines()
    assert "!app/readiness.py" in allowlist
    dockerfile = (ROOT / "server/Dockerfile.seed").read_text(encoding="utf-8")
    assert "COPY app/ app/" in dockerfile
    assert "test -f app/readiness.py" in dockerfile


def test_minimal_public_allowlist_imports_the_actual_application(tmp_path):
    """Copy only admitted source files; importing the full checkout would hide omissions."""
    server = ROOT / "server"
    patterns = [line[1:] for line in (server / "Dockerfile.seed.dockerignore").read_text(
        encoding="utf-8").splitlines() if line.startswith("!")]
    for source in (server / "app").rglob("*"):
        relative = source.relative_to(server).as_posix()
        if source.is_file() and any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns):
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path)
    result = subprocess.run([sys.executable, "-c", "import app.public_main; assert app.public_main.app"],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
