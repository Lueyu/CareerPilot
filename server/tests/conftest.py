"""All database tests are isolated; never default to a personal database."""
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlparse

import asyncpg
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
TEST_DATABASE_URL = os.environ.get(
    "CAREERPILOT_TEST_DATABASE_URL",
    "postgresql+asyncpg://careerpilot:careerpilot_dev@127.0.0.1:5432/careerpilot_core_test",
)
if not urlparse(TEST_DATABASE_URL).path.endswith("_test"):
    raise RuntimeError("Tests require an explicitly isolated database ending in _test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["LLM_PROVIDER"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["GITHUB_TOKEN"] = ""
os.environ["PORTAL_AUTO_MERGE"] = "false"

from app.models import Base, Exam, ImportSession, Workspace  # noqa: E402


_schema_ready = False


async def prepare_schema():
    global _schema_ready
    if _schema_ready:
        return
    connection = await asyncpg.connect(TEST_DATABASE_URL.replace("+asyncpg", ""))
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        await connection.execute("SELECT pg_advisory_lock(2026111601)")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        for path in sorted((Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")):
            await connection.execute(path.read_text(encoding="utf-8"))
        _schema_ready = True
    finally:
        await connection.execute("SELECT pg_advisory_unlock(2026111601)")
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    await prepare_schema()
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def test_workspace(db_session):
    workspace = Workspace(slug="test-" + uuid4().hex, name="Fixture workspace", is_demo=False)
    db_session.add(workspace)
    await db_session.commit()
    workspace_id = workspace.id
    yield workspace
    await db_session.rollback()
    await db_session.execute(delete(Exam).where(Exam.workspace_id == workspace_id))
    # ImportItem -> Question uses RESTRICT; clear fixture imports before the
    # workspace cascades its private questions. Never apply this to personal DBs.
    await db_session.execute(delete(ImportSession).where(ImportSession.workspace_id == workspace_id))
    await db_session.execute(delete(Workspace).where(Workspace.id == workspace_id))
    await db_session.commit()
