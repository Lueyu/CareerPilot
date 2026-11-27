"""Exercise real private-file initialization; Docker interpolation is a bounded fixture."""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate_delivery as delivery


@pytest.mark.parametrize("legacy_base", [False, True])
def test_preview_has_own_random_credentials_and_explicit_new_install_provision(tmp_path, monkeypatch, legacy_base):
    root = Path(__file__).resolve().parents[3]
    source = tmp_path / "source"
    (source / "web").mkdir(parents=True)
    shutil.copytree(root / "server", source / "server", ignore=shutil.ignore_patterns(
        ".env", ".venv", "__pycache__", ".pytest_cache", "tests", ".ruff_cache"))
    shutil.copy2(root / "web/Dockerfile", source / "web/Dockerfile")
    (tmp_path / "home").mkdir()
    original = delivery.output

    def output(argv, **kwargs):
        if argv[0] != "docker":
            return original(argv, **kwargs)
        assert "config" in argv
        env = kwargs["env"]
        assert len(env["API_TOKEN"]) >= 32
        assert len(env["POSTGRES_PASSWORD"]) >= 24
        assert "VITE_API_TOKEN" not in env
        password = "careerpilot_dev" if legacy_base else env["POSTGRES_PASSWORD"]
        backend = {"API_TOKEN": "cp-dev-token" if legacy_base else env["API_TOKEN"], "DATABASE_URL":
                   f"postgresql+asyncpg://careerpilot:{password}@postgres:5432/careerpilot"}
        services = {
            "postgres": {"environment": {"POSTGRES_PASSWORD": password}},
            "server": {"environment": backend, "build": {}},
            "worker": {"environment": backend, "build": {}},
            "web": {"build": {"context": str(source), "dockerfile": "web/Dockerfile"}},
        }
        if not legacy_base:
            services["preflight"] = {"environment": backend, "build": {},
                                     "command": ["python", "personal_credentials.py", "check"]}
        return json.dumps({"services": services}).encode()

    monkeypatch.setattr(delivery, "output", output)
    receipt = {"project": "careerpilot", "owner": "candidate-" + "a" * 32, "docker_host": "npipe://fixture"}
    delivery.configure(tmp_path, receipt, b"services: {}")
    credentials = delivery.preview_credentials(tmp_path)
    snapshot = delivery.read(tmp_path / "compose.json")
    for name in ("server", "worker", "preflight"):
        configured = snapshot["services"].get(name, {}).get("environment", {})
        expected_url = f"postgresql+asyncpg://careerpilot:{credentials['POSTGRES_PASSWORD']}@postgres:5432/careerpilot"
        bound = (configured.get("API_TOKEN") == credentials["API_TOKEN"]
                 and configured.get("DATABASE_URL") == expected_url
                 and configured.get("CAREERPILOT_SECRETS_KEY") == credentials["CAREERPILOT_SECRETS_KEY"])
        assert bound, f"{name} must use this attempt's private credentials"
    matching_password = snapshot["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] == credentials["POSTGRES_PASSWORD"]
    assert matching_password, "PostgreSQL and application credentials must agree"
    assert snapshot["services"]["preflight"]["command"] == ["python", "personal_credentials.py", "check"]
    assert snapshot["services"]["postgres"]["depends_on"]["preflight"]["condition"] == "service_completed_successfully"
    command = snapshot["services"]["server"]["command"][-1]
    assert "personal_credentials.py check" in command
    assert "provision_recovery_control.py" in command
    assert '"PROVISION KNOWN INSTALLATION careerpilot"' in command
    assert "--demo" in command
    assert "provision_recovery_control.py" not in (root / "server/Dockerfile").read_text().split("CMD ")[-1]
    assert credentials["API_TOKEN"] not in snapshot["services"]["web"]["build"]["dockerfile_inline"]
    retained_default = "cp-dev-token" in json.dumps(snapshot) or "careerpilot_dev" in json.dumps(snapshot)
    assert not retained_default
    before = (tmp_path / "preview.env").read_bytes()
    delivery.output([sys.executable, "personal_credentials.py", "init", "--output", str(tmp_path / "preview.env")],
                    cwd=source / "server", env=delivery.environment(tmp_path / "home"))
    assert (tmp_path / "preview.env").read_bytes() == before
