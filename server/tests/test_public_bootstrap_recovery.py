"""Real PostgreSQL bootstrap checks in owned schemas of a fresh *_test database."""
import os
import re
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from app import public_main
from app.services.recovery_control import RecoveryBlocked, provision_known_installation, require_normal


@pytest_asyncio.fixture
async def public_database(monkeypatch):
    url = make_url(os.environ["DATABASE_URL"])
    assert url.database.endswith("_test")
    assert url.host in {"127.0.0.1", "localhost", "::1"}
    schema = "f6_bootstrap_" + uuid4().hex
    assert re.fullmatch(r"f6_bootstrap_[a-f0-9]{32}", schema)
    admin = create_async_engine(url, poolclass=NullPool)
    engine = create_async_engine(
        url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}},
    )
    async with admin.begin() as conn:
        await conn.execute(CreateSchema(schema))
    monkeypatch.setattr(public_main, "engine", engine)
    try:
        yield engine, async_sessionmaker(engine)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            assert conn.engine.url.database.endswith("_test")
            assert re.fullmatch(r"f6_bootstrap_[a-f0-9]{32}", schema)
            await conn.execute(DropSchema(schema, cascade=True))
        await admin.dispose()


async def tables(engine):
    async with engine.connect() as conn:
        return await conn.run_sync(lambda sync: inspect(sync).get_table_names())


@pytest.mark.asyncio
async def test_fresh_bootstrap_requires_explicit_provision_and_retains_generation(public_database):
    engine, sessions = public_database
    assert await tables(engine) == []
    await public_main.initialize_public_database()
    assert "recovery_control" in await tables(engine)
    async with sessions.begin() as session:
        assert await session.scalar(text("SELECT count(*) FROM recovery_control")) == 0
        with pytest.raises(RecoveryBlocked):
            await require_normal(session)
    await public_main.initialize_public_database()
    async with sessions.begin() as session:
        assert await session.scalar(text("SELECT count(*) FROM recovery_control")) == 0
        await provision_known_installation(session)
        generation = await require_normal(session)
        before = dict((await session.execute(text("SELECT * FROM recovery_control"))).mappings().one())
    await public_main.initialize_public_database()
    async with sessions.begin() as session:
        await provision_known_installation(session)
        assert await require_normal(session) == generation
        assert dict((await session.execute(text("SELECT * FROM recovery_control"))).mappings().one()) == before


@pytest.mark.asyncio
async def test_existing_public_marker_gets_empty_recovery_schema(public_database):
    engine, sessions = public_database
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE careerpilot_seed_install (version integer PRIMARY KEY)"))
        await conn.execute(text("INSERT INTO careerpilot_seed_install VALUES (1)"))
    await public_main.initialize_public_database()
    async with sessions.begin() as session:
        assert await session.scalar(text("SELECT count(*) FROM recovery_control")) == 0
        with pytest.raises(RecoveryBlocked):
            await require_normal(session)


@pytest.mark.asyncio
async def test_restored_quarantine_is_unchanged_and_cannot_be_provisioned(public_database):
    _, sessions = public_database
    await public_main.initialize_public_database()
    async with sessions.begin() as session:
        await session.execute(text(
            "INSERT INTO recovery_control(id,version,restore_id,archive_sha256,target_database,"
            "server_address,server_port,validation_state,access_state,paid_state,credentials_state,origin) "
            "VALUES(1,1,repeat('a',32),repeat('b',64),current_database(),inet_server_addr()::text,"
            "inet_server_port(),'PENDING','QUARANTINED','BLOCKED','BLOCKED','RESTORE')"
        ))
        before = dict((await session.execute(text("SELECT * FROM recovery_control"))).mappings().one())
    await public_main.initialize_public_database()
    await public_main.initialize_public_database()
    async with sessions.begin() as session:
        with pytest.raises(RecoveryBlocked):
            await require_normal(session)
        with pytest.raises(RecoveryBlocked):
            await provision_known_installation(session)
        assert dict((await session.execute(text("SELECT * FROM recovery_control"))).mappings().one()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("public_version", [None, 999])
async def test_invalid_target_is_rejected_before_schema_access(public_database, monkeypatch, tmp_path, public_version):
    engine, _ = public_database
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE sentinel (value text)"))
        await conn.execute(text("INSERT INTO sentinel VALUES ('retain')"))
        if public_version is not None:
            await conn.execute(text("CREATE TABLE careerpilot_seed_install (version integer PRIMARY KEY)"))
            await conn.execute(text("INSERT INTO careerpilot_seed_install VALUES (:version)"), {"version": public_version})
    before = await tables(engine)
    monkeypatch.setattr(public_main, "__file__", str(tmp_path / "app" / "public_main.py"))
    message = "fresh database" if public_version is None else "Unsupported public database version"
    with pytest.raises(RuntimeError, match=message):
        await public_main.initialize_public_database()
    assert await tables(engine) == before
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT value FROM sentinel"))).scalars().all() == ["retain"]


@pytest.mark.asyncio
async def test_missing_packaged_migration_fails_and_rolls_back(public_database, monkeypatch, tmp_path):
    engine, _ = public_database
    monkeypatch.setattr(public_main, "__file__", str(tmp_path / "app" / "public_main.py"))
    with pytest.raises(FileNotFoundError, match="20261128_recovery_control.sql"):
        await public_main.initialize_public_database()
    assert await tables(engine) == []
