"""Initialize once or check deployment credentials; never connects to PostgreSQL."""
import argparse
import os
import secrets
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy.engine import URL

from app.config import Settings, get_settings


def restrict_private(path: Path) -> None:
    """Protect only a newly owned file/directory, before writing secret bytes."""
    if os.name == "nt":
        # Same .NET ACL mechanism as deploy/backup-common.ps1, verified by SID.
        script = r"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1')
$path = $env:CAREERPILOT_PRIVATE_PATH
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
$item = Get-Item -LiteralPath $path
$info = if ($item.PSIsContainer) { [IO.DirectoryInfo]::new($path) } else { [IO.FileInfo]::new($path) }
$acl = if ($PSVersionTable.PSEdition -eq 'Core') { [IO.FileSystemAclExtensions]::GetAccessControl($info, [Security.AccessControl.AccessControlSections]::Access) } else { $info.GetAccessControl([Security.AccessControl.AccessControlSections]::Access) }
$acl.SetAccessRuleProtection($true, $false)
foreach ($old in @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))) { $acl.RemoveAccessRuleSpecific($old) }
$rule = if ($item.PSIsContainer) {
    [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
} else { [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow') }
$acl.AddAccessRule($rule)
if ($PSVersionTable.PSEdition -eq 'Core') { [IO.FileSystemAclExtensions]::SetAccessControl($info, $acl) } else { $info.SetAccessControl($acl) }
$actual = Get-Acl -LiteralPath $path
$rules = @($actual.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
if (-not $actual.AreAccessRulesProtected -or $actual.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value -or $rules.Count -ne 1 -or $rules[0].IdentityReference.Value -ne $sid.Value -or $rules[0].IsInherited -or $rules[0].AccessControlType -ne 'Allow' -or $rules[0].FileSystemRights -ne 'FullControl') { throw 'Private credential ACL could not be verified' }
"""
        env = dict(os.environ, CAREERPILOT_PRIVATE_PATH=str(path.resolve()))
        result = subprocess.run([shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                                env=env, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError("Private credential ACL could not be verified")
    else:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError("Private credential permissions could not be verified")


def check_file(path: Path) -> None:
    values = dotenv_values(path)
    settings = Settings(_env_file=None, api_token=values.get("API_TOKEN") or "",
                        database_url=values.get("DATABASE_URL") or "")
    settings.require_secure_personal_startup()
    password = values.get("POSTGRES_PASSWORD")
    if password and password != settings_url_password(settings):
        raise RuntimeError("POSTGRES_PASSWORD must match DATABASE_URL in the generated environment file")


def settings_url_password(settings: Settings) -> str:
    from sqlalchemy.engine import make_url
    return make_url(settings.database_url).password or ""


def create_private_empty(path: Path) -> None:
    """Exclusive new file only. Callers may write after ACL verification succeeds."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        restrict_private(path)
    except Exception:
        path.unlink()
        raise


def initialize(path: Path, *, host="127.0.0.1", port=5432, user="careerpilot", database="careerpilot") -> bool:
    """Exclusive creation is race safe; existing bytes are never overwritten."""
    if path.exists():
        check_file(path)
        return False
    password = secrets.token_hex(32)
    url = URL.create("postgresql+asyncpg", username=user, password=password,
                     host=host, port=port, database=database).render_as_string(hide_password=False)
    body = (f"API_TOKEN={secrets.token_urlsafe(32)}\nPOSTGRES_PASSWORD={password}\n"
            f"DATABASE_URL={url}\nCAREERPILOT_SECRETS_KEY={Fernet.generate_key().decode()}\n"
            f"PG_HOST_PORT=127.0.0.1:{port}\nAPI_HOST_PORT=127.0.0.1:8000\nWEB_HOST_PORT=127.0.0.1:5173\n")
    if not path.parent.exists():
        try:
            path.parent.mkdir(parents=True)
        except FileExistsError:
            pass
        else:
            restrict_private(path.parent)
    staging = Path(tempfile.mkdtemp(prefix=".credentials-", dir=path.parent))
    temporary = staging / "personal.env"
    try:
        restrict_private(staging)
        create_private_empty(temporary)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            check_file(path)
            return False
    finally:
        temporary.unlink(missing_ok=True)
        staging.rmdir()
    check_file(path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a private env for a NEW database/Compose volume")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--host", default="127.0.0.1")
    init.add_argument("--port", type=int, default=5432)
    init.add_argument("--user", default="careerpilot")
    init.add_argument("--database", default="careerpilot")
    commands.add_parser("check", help="validate resolved env before any database operation")
    private = commands.add_parser("create-private-file", help="exclusively create and verify an empty private deployment artifact")
    private.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            created = initialize(args.output, host=args.host, port=args.port, user=args.user, database=args.database)
            print("Private credential file created." if created else "Existing credential file validated; unchanged.")
        elif args.command == "create-private-file":
            create_private_empty(args.output)
            print("Empty private deployment artifact created and permissions verified.")
        else:
            get_settings().require_secure_personal_startup()
            print("Personal credential preflight passed.")
    except (RuntimeError, OSError, ValueError):
        # No URL, token, parsed input or traceback reaches an operator log.
        print("Personal credentials rejected: require a unique random API_TOKEN (32+ characters) and PostgreSQL password (24+ characters); preserve existing configuration and follow QUICK_START_PERSONAL.md.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
