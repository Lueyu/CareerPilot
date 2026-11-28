"""All database tests are isolated; never default to a personal database."""
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
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
os.environ["API_TOKEN"] = secrets.token_urlsafe(32)
os.environ["LLM_PROVIDER"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["STUDY_STRONG_API_KEY"] = ""
os.environ["GITCODE_TOKEN"] = ""
for key in tuple(os.environ):
    if key.startswith("FEEDBACK_") or key.endswith("_API_KEY"):
        os.environ[key] = ""
os.environ["GITHUB_TOKEN"] = ""
os.environ["PORTAL_AUTO_MERGE"] = "false"

from app.models import Base, Exam, ImportSession, Question, Workspace  # noqa: E402

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
        # Explicitly owned fresh *_test installation; never a runtime auto-NORMAL.
        from app.services.recovery_control import provision_known_installation
        async with async_sessionmaker(engine).begin() as session:
            await provision_known_installation(session)
        _schema_ready = True
    finally:
        await connection.execute("SELECT pg_advisory_unlock(2026111601)")
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(monkeypatch):
    await prepare_schema()
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    # Middleware admission owns independent transactions on this test's engine/loop.
    from app import db
    from app.api import rate_limits

    monkeypatch.setattr(rate_limits, "SessionLocal", factory)
    # Middleware/credential guards own independent transactions on this same loop.
    monkeypatch.setattr(db, "SessionLocal", factory)
    async with factory() as session:
        from app.models import LocalAuthFailure
        # Each test owns its failure allowance; HTTP/peer checks within it share durable state.
        await session.execute(delete(LocalAuthFailure))
        await session.commit()
        yield session
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def track_public_question(db_session):
    """Clean up only public question rows explicitly created by the caller."""
    short_codes = []

    def track(question):
        assert question.workspace_id is None
        short_codes.append(question.short_code)
        return question

    yield track
    await db_session.rollback()
    await db_session.execute(delete(Question).where(
        Question.workspace_id.is_(None), Question.short_code.in_(short_codes),
    ))
    await db_session.commit()


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


@pytest.fixture
def secrets_key(monkeypatch):
    """Explicit synthetic deployment key for credential-storage consumers only."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("CAREERPILOT_SECRETS_KEY", Fernet.generate_key().decode())


@pytest_asyncio.fixture
async def enrolled_default_owner(db_session, monkeypatch):
    """Opt-in fixture for HTTP contracts that need an explicitly enrolled owner."""
    from credential_fixtures import enroll_token

    from app.config import get_settings
    token = secrets.token_urlsafe(32)
    monkeypatch.setattr(get_settings(), "api_token", token)
    await enroll_token(db_session, token)
    return token


@pytest.fixture
def synthetic_generation_quota(monkeypatch):
    """Long transport/content scenarios use explicit synthetic, still-enforced quotas."""
    from app.config import ApiRatePolicy, get_settings

    cfg = get_settings()
    policy = cfg.api_rate_limits.model_dump()
    policy["llm"] = {"burst": 100, "per_minute": 10000, "daily": 1000}
    monkeypatch.setattr(cfg, "api_rate_limits", ApiRatePolicy.model_validate(policy))


@pytest.fixture
def mock_private_billing(monkeypatch):
    """Explicit transport-test boundary. Ledger tests must not use this fixture."""
    from app.services import llm
    from app.services.private_model_billing import Charge

    async def reserve(*args, **kwargs):
        return "transport-test-call"

    async def settle(*args, **kwargs):
        return Charge("transport-test-call", "UNKNOWN", None, 0)

    async def metric(self, **kwargs):
        pass

    monkeypatch.setattr(llm.AgentAdapter, "_record_metric", metric)
    monkeypatch.setattr(llm, "reserve", reserve)
    monkeypatch.setattr(llm, "settle", settle)
