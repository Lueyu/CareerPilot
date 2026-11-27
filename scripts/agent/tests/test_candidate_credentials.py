"""Exercise real private-file initialization; Docker interpolation is a bounded fixture."""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate_delivery as delivery


def test_preview_has_own_random_credentials_and_explicit_new_install_provision(tmp_path, monkeypatch):
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
        backend = {"API_TOKEN": env["API_TOKEN"], "DATABASE_URL":
                   f"postgresql+asyncpg://careerpilot:{env['POSTGRES_PASSWORD']}@postgres:5432/careerpilot"}
        return json.dumps({"services": {
            "postgres": {"environment": {"POSTGRES_PASSWORD": env["POSTGRES_PASSWORD"]}},
            "server": {"environment": backend, "build": {}},
            "worker": {"environment": backend, "build": {}},
            "preflight": {"environment": backend, "build": {}, "command": ["python", "personal_credentials.py", "check"]},
            "web": {"build": {"context": str(source), "dockerfile": "web/Dockerfile"}},
        }}).encode()

    monkeypatch.setattr(delivery, "output", output)
    receipt = {"project": "careerpilot", "owner": "candidate-" + "a" * 32, "docker_host": "npipe://fixture"}
    delivery.configure(tmp_path, receipt, b"services: {}")
    credentials = delivery.preview_credentials(tmp_path)
    snapshot = delivery.read(tmp_path / "compose.json")
    assert snapshot["services"]["server"]["environment"]["API_TOKEN"] == credentials["API_TOKEN"]
    assert snapshot["services"]["worker"]["environment"]["API_TOKEN"] == credentials["API_TOKEN"]
    command = snapshot["services"]["server"]["command"][-1]
    assert "personal_credentials.py check" in command
    assert "provision_recovery_control.py" in command
    assert '"PROVISION KNOWN INSTALLATION careerpilot"' in command
    assert "--demo" in command
    assert "provision_recovery_control.py" not in (root / "server/Dockerfile").read_text().split("CMD ")[-1]
    assert credentials["API_TOKEN"] not in snapshot["services"]["web"]["build"]["dockerfile_inline"]
    assert "cp-dev-token" not in json.dumps(snapshot)
    before = (tmp_path / "preview.env").read_bytes()
    delivery.output([sys.executable, "personal_credentials.py", "init", "--output", str(tmp_path / "preview.env")],
                    cwd=source / "server", env=delivery.environment(tmp_path / "home"))
    assert (tmp_path / "preview.env").read_bytes() == before
