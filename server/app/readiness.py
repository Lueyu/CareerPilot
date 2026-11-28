"""Current database and recovery admission for unauthenticated probes."""
import asyncio

from asyncpg import PostgresError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from app import db
from app.services.recovery_control import RecoveryBlocked, require_normal

READINESS_TIMEOUT_SECONDS = 1.0


async def readiness_response(service: str) -> JSONResponse:
    ok = False
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            async with db.SessionLocal() as session:
                await session.execute(text("SELECT 1"))
                await require_normal(session)
        ok = True
    except (SQLAlchemyError, PostgresError, OSError, TimeoutError, RecoveryBlocked):
        pass
    return JSONResponse({"ok": ok, "service": service}, status_code=200 if ok else 503,
                        headers={"Cache-Control": "no-store"})
