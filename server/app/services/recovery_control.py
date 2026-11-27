"""Durable recovery quarantine. There is deliberately no runtime release API."""
import re
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


class RecoveryBlocked(HTTPException):
    def __init__(self):
        super().__init__(503, {"code": "RECOVERY_QUARANTINED",
                               "message": "Recovery requires offline validation and separate credential/budget reconciliation."})


async def require_normal(session=None) -> None:
    """Read on every boundary; missing schema/row or unsupported state is a denial."""
    if session is None:
        from app.db import SessionLocal
        async with SessionLocal() as db:
            await require_normal(db)
        return
    try:
        rows = (await session.execute(text(
            "SELECT *, current_database() AS actual_database, inet_server_addr()::text AS actual_address, "
            "inet_server_port() AS actual_port FROM recovery_control"
        ))).mappings().all()
    except SQLAlchemyError:
        raise RecoveryBlocked() from None
    if len(rows) != 1:
        raise RecoveryBlocked()
    row = rows[0]
    if not (row["id"] == 1 and row["version"] == 1 and row["origin"] == "INSTALLATION"
            and isinstance(row["restore_id"], str) and re.fullmatch('[a-f0-9]{32}', row["restore_id"])
            and row["archive_sha256"] is None
            and row["target_database"] == row["actual_database"]
            and row["server_address"] == row["actual_address"] and row["server_port"] == row["actual_port"]
            and row["validation_state"] == "KNOWN_INSTALLATION"
            and row["access_state"] == row["paid_state"] == row["credentials_state"] == "NORMAL"):
        raise RecoveryBlocked()


async def provision_known_installation(session) -> None:
    """Offline operator/test-fixture entry only. Never reclassify a restored database."""
    await session.execute(text("LOCK TABLE recovery_control IN EXCLUSIVE MODE"))
    count = await session.scalar(text("SELECT count(*) FROM recovery_control"))
    if count:
        await require_normal(session)
        return
    await session.execute(text(
        "INSERT INTO recovery_control(id,version,restore_id,target_database,server_address,server_port,"
        "validation_state,access_state,paid_state,credentials_state,origin) "
        "VALUES(1,1,:generation,current_database(),inet_server_addr()::text,inet_server_port(),"
        "'KNOWN_INSTALLATION','NORMAL','NORMAL','NORMAL','INSTALLATION')"
    ), {"generation": uuid4().hex})
