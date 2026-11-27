"""Personal startup must reject insecure credentials before doing any work."""
import secrets

import pytest

from app.config import Settings


def strong_settings(**overrides):
    values = dict(
        _env_file=None,
        api_token=secrets.token_urlsafe(32),
        database_url=f"postgresql+asyncpg://owned:{secrets.token_hex(32)}@127.0.0.1/owned_test",
    )
    return Settings(**(values | overrides))


@pytest.mark.parametrize("value", ["", "cp-dev-token", "short", "x" * 64,
    "replace-me-with-a-real-secret-please", "your-api-token-change-me-before-use-123456789",
    "0123456789" * 4, "Ab9_zQ2x" * 6, "abcdefghijklmnopqrstuvwxyz012345"])
def test_weak_token_rejected_even_in_debug(value):
    with pytest.raises(RuntimeError, match="API_TOKEN") as error:
        strong_settings(api_token=value, debug=True).require_secure_personal_startup()
    if value:
        assert value not in str(error.value)


@pytest.mark.parametrize("password", ["", "careerpilot_dev", "postgres", "password", "a" * 64,
    "replace-me-with-a-long-database-password", "0123456789" * 4, "Ab9_zQ2x" * 6])
def test_weak_database_password_rejected(password):
    with pytest.raises(RuntimeError, match="DATABASE_URL") as error:
        strong_settings(database_url=f"postgresql+asyncpg://owned:{password}@127.0.0.1/owned_test").require_secure_personal_startup()
    if password:
        assert password not in str(error.value)


def test_explicit_strong_configuration_passes():
    strong_settings().require_secure_personal_startup()


def test_malformed_url_does_not_echo_secrets():
    marker = secrets.token_urlsafe(32)
    with pytest.raises(RuntimeError, match="DATABASE_URL") as error:
        strong_settings(database_url="invalid-" + marker).require_secure_personal_startup()
    assert marker not in str(error.value)


def test_init_is_random_and_stable_without_overwriting(tmp_path):
    from dotenv import dotenv_values

    from personal_credentials import initialize

    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    assert initialize(first)
    before = first.read_bytes()
    assert not initialize(first)
    assert first.read_bytes() == before
    assert initialize(second)
    a, b = dotenv_values(first), dotenv_values(second)
    assert a["API_TOKEN"] != b["API_TOKEN"]
    assert a["POSTGRES_PASSWORD"] != b["POSTGRES_PASSWORD"]
    assert a["CAREERPILOT_SECRETS_KEY"] != b["CAREERPILOT_SECRETS_KEY"]


def test_existing_insecure_file_is_rejected_unchanged(tmp_path):
    from personal_credentials import initialize

    existing = tmp_path / "existing.env"
    existing.write_bytes(b"API_TOKEN=cp-dev-token\n# retain exact bytes\n")
    before = existing.read_bytes()
    with pytest.raises(RuntimeError):
        initialize(existing)
    assert existing.read_bytes() == before


def test_permission_failure_occurs_before_any_secret_is_written(tmp_path, monkeypatch):
    import personal_credentials

    def denied(path):
        assert not path.is_file() or path.read_bytes() == b""
        raise RuntimeError("ACL denied")

    monkeypatch.setattr(personal_credentials, "restrict_private", denied)
    target = tmp_path / "private.env"
    with pytest.raises(RuntimeError, match="ACL denied"):
        personal_credentials.initialize(target)
    assert not target.exists()
    assert not list(tmp_path.glob(".credentials-*"))


def test_concurrent_first_initializers_publish_one_complete_file(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from personal_credentials import check_file, initialize

    target = tmp_path / "shared.env"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: initialize(target), range(4)))
    assert sum(results) == 1
    check_file(target)
    before = target.read_bytes()
    assert not initialize(target)
    assert before == target.read_bytes()


def test_generated_windows_acl_is_protected_and_current_sid_only(tmp_path):
    import json
    import os
    import shutil
    import subprocess

    from personal_credentials import initialize

    if os.name != "nt":
        pytest.skip("Windows DACL semantics require Windows")
    target = tmp_path / "new-private" / "personal.env"
    initialize(target)
    script = r"""
$ErrorActionPreference='Stop'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$result=@()
foreach($path in @($env:G6_ACL_FILE, (Split-Path -Parent $env:G6_ACL_FILE))) {
 $a=Get-Acl -LiteralPath $path
 $r=@($a.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
 if(-not $a.AreAccessRulesProtected -or $r.Count -ne 1 -or $r[0].IdentityReference.Value -ne $sid -or $r[0].IsInherited -or $r[0].FileSystemRights -ne 'FullControl') { throw 'Unexpected ACL' }
 $result+=@{protected=$a.AreAccessRulesProtected;owner=$a.GetOwner([Security.Principal.SecurityIdentifier]).Value;sid=$sid;rules=@($r | ForEach-Object {@{sid=$_.IdentityReference.Value;rights=$_.FileSystemRights.ToString();inherited=$_.IsInherited}})}
}
ConvertTo-Json -InputObject $result -Depth 5
"""
    result = subprocess.run([shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=dict(os.environ, G6_ACL_FILE=str(target)), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)) == 2
    print("Actual Get-Acl file and newly-owned directory:", result.stdout)


@pytest.mark.parametrize("entry", [["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "0"],
    ["-m", "app.project_worker", "--once"], ["bootstrap.py"], ["migrate.py"], ["-m", "app.seed"]])
@pytest.mark.parametrize("fault", ["missing", "weak-token", "weak-password", "periodic-password", "malformed-port"])
def test_real_startup_refuses_before_database_connection(tmp_path, entry, fault):
    import os
    import socket
    import subprocess
    import sys
    from pathlib import Path

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.05)
    port = listener.getsockname()[1]
    api = secrets.token_urlsafe(32)
    password = secrets.token_hex(32)
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    }}
    env.update(PYTHONUTF8="1", DEBUG="true", CAREERPILOT_ENV_FILE=str(tmp_path / "absent.env"),
               LLM_API_KEY="", STUDY_STRONG_API_KEY="", GITHUB_TOKEN="", GITCODE_TOKEN="")
    if fault != "missing":
        env["API_TOKEN"] = "cp-dev-token" if fault == "weak-token" else api
        bad = "careerpilot_dev" if fault == "weak-password" else "0123456789" * 4 if fault == "periodic-password" else password
        configured_port = "PRIVATE_PORT_INPUT_MUST_NOT_ECHO" if fault == "malformed-port" else str(port)
        env["DATABASE_URL"] = f"postgresql+asyncpg://owned:{bad}@127.0.0.1:{configured_port}/owned_test"
    try:
        result = subprocess.run([sys.executable, *entry], cwd=Path(__file__).resolve().parents[1],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode != 0
        assert api not in result.stdout + result.stderr
        assert password not in result.stdout + result.stderr
        if "PRIVATE_PORT_INPUT_MUST_NOT_ECHO" in result.stdout + result.stderr:
            pytest.fail("Startup echoed malformed connection input before preflight")
        assert not list(tmp_path.iterdir())
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()
