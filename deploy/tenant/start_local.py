"""Start the account-aware gateway on loopback against an existing registered installation."""
import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]


def initialize_once(path, factory):
    if not path.is_file():
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as temp:
            temp.write(factory())
        try:
            try:
                os.link(temp.name, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temp.name)
    return path.read_text(encoding="utf-8").strip()


async def environment(args):
    import asyncpg

    from app.config import get_settings
    from app.db import engine
    from app.services.llm import get_llm_config
    from app.services.study_routing import profile

    settings = dict(line.split("=", 1) for line in args.account_env.read_text(encoding="utf-8-sig").splitlines()
                    if line and not line.startswith("#"))
    url = settings["DATABASE_URL"].replace("postgresql+asyncpg:", "postgresql:")
    if urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("The local launcher requires a loopback registration database")
    args.state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    owner_path = args.state_dir / "owner.json"
    owner = {}
    conn = await asyncpg.connect(url)
    try:
        if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM careerpilot_seed_install WHERE version=1)"):
            raise ValueError("Expected a registered installation")
        if args.bind_owner:
            row = await conn.fetchrow(
                "SELECT a.id, a.username, a.workspace_id, w.slug FROM seed_accounts a "
                "JOIN workspaces w ON w.id=a.workspace_id WHERE a.username=$1", args.bind_owner.lower())
            if not row:
                raise ValueError("Create the owner's account and verify its identity before binding")
            identity = dict(row)
            stored = json.loads(initialize_once(owner_path, lambda: json.dumps(identity)))
            if identity != stored:
                raise ValueError("The configured owner is already bound to another identity")
        if owner_path.is_file():
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
            matches = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM seed_accounts a JOIN workspaces w ON w.id=a.workspace_id "
                "WHERE a.id=$1 AND a.workspace_id=$2 AND w.slug=$3 AND a.username=$4)",
                owner["id"], owner["workspace_id"], owner["slug"], owner["username"])
            if not matches:
                # Deleting the registration retires this binding. Other members can
                # still sign in after a restart, including one who reuses its name.
                owner = {}
    finally:
        await conn.close()
    try:
        model = await get_llm_config() if args.use_personal_model else {}
        owner_token = get_settings().api_token if owner else None
    finally:
        await engine.dispose()
    env = {**os.environ, **{k: v for k, v in settings.items() if k in {"DATABASE_URL", "MODEL_LOCAL_ORIGINS"} or k.startswith(("SEED_", "FEEDBACK_"))},
           "SEED_PUBLIC_ORIGIN": f"http://127.0.0.1:{args.port}",
           "SEED_STATIC_DIR": str(ROOT/"web/dist-seed"), "SEED_NATIVE_LOCAL_TENANTS": "1",
           "TENANT_RUNTIME_SECRET": initialize_once(args.state_dir/"native.key", lambda: secrets.token_hex(48)),
           "TENANT_RUNTIME_STATE_DIR": str(args.state_dir/"locks"),
           "PYTHONPATH": os.pathsep.join([str(ROOT), *(str(path) for path in args.library_dir)]),
           "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    for key in ("SEED_NATIVE_OWNER_BINDING", "SEED_NATIVE_OWNER_API_TOKEN", "SEED_NATIVE_OWNER_BANK", "SEED_NATIVE_OWNER_USERNAME"):
        env.pop(key, None)
    if owner:
        env.update(SEED_NATIVE_OWNER_BINDING=f"{owner['workspace_id']}:{owner['slug']}",
                   SEED_NATIVE_OWNER_USERNAME=owner["username"], SEED_NATIVE_OWNER_API_TOKEN=owner_token,
                   SEED_NATIVE_OWNER_BANK=str(args.personal_server.parent/"web/src/data/bank.json"))
    if args.use_personal_model:
        if not all(model.get(k) for k in ("model", "api_key", "base_url")):
            raise ValueError("The personal model is not configured")
        env.update(SEED_MODEL_NAME=model["model"], SEED_MODEL_KEY=model["api_key"], SEED_MODEL_BASE_URL=model["base_url"],
                   SEED_MODEL_PROVIDER_LABEL="CareerPilot · " + model["model"])
        if "MODEL_LOCAL_ORIGINS" not in settings:
            env["MODEL_LOCAL_ORIGINS"] = json.dumps(get_settings().model_local_origins)
        flash = profile(model, "flash")
        strong = profile(model, "strong") or {}
        separate = strong and (strong["base_url"].rstrip("/") != model["base_url"].rstrip("/")
                               or strong["api_key"] != model["api_key"])
        env.update(SEED_STUDY_FLASH_MODEL=flash["model"],
                   SEED_STUDY_FLASH_THINKING=flash.get("thinking") or env.get("SEED_MODEL_THINKING", ""),
                   SEED_STUDY_STRONG_MODEL=strong.get("model", ""),
                   SEED_STUDY_STRONG_BASE_URL=strong["base_url"] if separate else "",
                   SEED_STUDY_STRONG_API_KEY=strong["api_key"] if separate else "")
    if args.local_unmetered:
        env["SEED_LOCAL_UNMETERED"] = "true"
    return env


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", type=int, default=18457)
    cli.add_argument("--account-env", type=Path, required=True)
    cli.add_argument("--state-dir", type=Path, required=True)
    cli.add_argument("--personal-server", type=Path, required=True)
    cli.add_argument("--bind-owner", help="One-time operator binding to an existing, verified registration")
    cli.add_argument("--library-dir", type=Path, action="append", default=[])
    cli.add_argument("--use-personal-model", action="store_true")
    cli.add_argument("--local-unmetered", action="store_true")
    args = cli.parse_args()
    for name in ("account_env", "state_dir", "personal_server"):
        setattr(args, name, getattr(args, name).resolve())
    args.library_dir = [path.resolve() for path in args.library_dir]
    sys.path[:0] = [str(args.personal_server), *(str(path) for path in args.library_dir)]
    os.chdir(args.personal_server)
    env = asyncio.run(environment(args))
    return subprocess.call([sys.executable, "-m", "uvicorn", "app.public_main:app", "--host", "127.0.0.1",
                            "--port", str(args.port), "--no-access-log"], cwd=ROOT/"server", env=env)


if __name__ == "__main__":
    sys.exit(main())
