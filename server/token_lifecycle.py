"""Offline local access enrollment, issuance, metadata listing, rotation and revocation."""
import argparse
import asyncio
import json
import os
import secrets
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from personal_credentials import create_private_empty


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("issue", "enroll", "enroll-env", "migrate-scopes", "rotate", "revoke", "list"):
        command = commands.add_parser(name)
        if name in {"rotate", "revoke"}:
            command.add_argument("id")
        if name in {"issue", "enroll", "enroll-env", "rotate"}:
            command.add_argument("--days", type=int, default=30)
        if name in {"issue", "enroll", "enroll-env"}:
            command.add_argument("--workspace", help="existing workspace slug; omission explicitly grants local owner scope")
        if name in {"enroll", "enroll-env", "migrate-scopes"}:
            command.add_argument("--confirm", required=True)
        if name == "enroll":
            command.add_argument("--input-file", type=Path, required=True)
        if name in {"issue", "rotate"}:
            destination = command.add_mutually_exclusive_group(required=True)
            destination.add_argument("--output", type=Path)
            destination.add_argument("--show-token", action="store_true", help="explicitly print the new secret to this terminal")
    return result


async def execute(args):
    owned_output = None
    committed = False
    engine = None
    try:
        cfg = get_settings()
        cfg.require_secure_personal_startup()
        # Import runtime modules only after G6 preflight has validated the URL.
        from app.db import SessionLocal, engine
        from app.models import LocalCredential
        from app.services.local_credentials import enroll, metadata, migrate_legacy_boundaries, revoke, rotate
        from app.services.recovery_control import require_normal
        if args.command in {"enroll", "enroll-env", "migrate-scopes"}:
            expected = f"ENROLL {make_url(cfg.database_url).database}"
            if args.confirm != expected:
                raise ValueError("Confirmation must name the reviewed database")
        raw = None
        if args.command in {"issue", "rotate"}:
            raw = secrets.token_urlsafe(32)
            if args.output:
                create_private_empty(args.output)
                owned_output = args.output
        elif args.command == "enroll-env":
            raw = cfg.api_token
        elif args.command == "enroll":
            if args.input_file.stat().st_size > 1024:
                raise ValueError("Credential input exceeds supported bounds")
            raw = args.input_file.read_text(encoding="utf-8").strip()
        async with SessionLocal() as session, session.begin():
            await require_normal(session)
            if args.command in {"issue", "enroll", "enroll-env", "migrate-scopes"}:
                await migrate_legacy_boundaries(session, cfg.workspace_token_hashes)
            if args.command == "migrate-scopes":
                result = {"legacy_claims_preserved": len(cfg.workspace_token_hashes)}
            elif args.command == "list":
                result = [metadata(row) for row in await session.scalars(select(LocalCredential).order_by(LocalCredential.created_at))]
            else:
                if args.command == "rotate":
                    row = await rotate(session, args.id, raw, days=args.days)
                elif args.command == "revoke":
                    row = await revoke(session, args.id)
                else:
                    row = await enroll(session, raw, workspace=args.workspace, days=args.days)
                result = metadata(row)
                if owned_output:
                    with owned_output.open("w", encoding="utf-8", newline="\n") as stream:
                        stream.write(raw + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
        committed = True
        if getattr(args, "show_token", False):
            result["token"] = raw
        print(json.dumps(result))
        return 0
    except (SQLAlchemyError, HTTPException, RuntimeError, OSError, ValueError):
        print("Credential operation rejected. Check installation state, scope, lifetime, input and output permissions. Existing credentials are not re-enabled.")
        return 1
    finally:
        if owned_output and not committed:
            owned_output.unlink(missing_ok=True)
        if engine is not None:
            await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(execute(parser().parse_args())))
