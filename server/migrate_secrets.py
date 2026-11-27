"""Explicit offline credential conversion. Never selects the application's default database."""
import argparse
import asyncio
import json
import os
import sys

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AppSetting
from app.services.secret_storage import _cipher, credential_setting, seal_setting


async def migrate_legacy(session) -> dict[str, int]:
    # Validate even an empty database, before acquiring a lock or writing.
    _cipher()
    changed = 0
    checked = 0
    async with session.begin():
        await session.execute(text("LOCK TABLE app_settings IN EXCLUSIVE MODE"))
        rows = (await session.scalars(select(AppSetting).order_by(AppSetting.key))).all()
        for row in rows:
            if not credential_setting(row.key):
                continue
            checked += 1
            encrypted = seal_setting(row.key, row.value)
            if encrypted != row.value:
                row.value = encrypted
                # A later invalid row still rolls this write back with the entire transaction.
                await session.flush()
                changed += 1
    return {"checked_rows": checked, "changed_rows": changed}


async def run(env_name: str):
    url = os.environ.get(env_name, "")
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError
    engine = create_async_engine(url, echo=False, hide_parameters=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await migrate_legacy(session)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", required=True, help="Name of explicit target DSN environment variable")
    parser.add_argument("--apply", required=True, action="store_true", help="Confirm offline in-place conversion")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.database_url_env))
    except Exception:
        print("Credential migration did not confirm completion. Check target, key and data; rerun is idempotent.", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
