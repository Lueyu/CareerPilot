import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.api.deps import require_auth
from app.config import get_settings


@pytest.mark.asyncio
async def test_restored_token_cannot_authenticate(db_session):
    # Baseline ignores this control and accepts the old token.
    await db_session.execute(text("CREATE TABLE IF NOT EXISTS recovery_control (id integer PRIMARY KEY, version integer NOT NULL, restore_id text NOT NULL, archive_sha256 text, target_database text NOT NULL, server_address text, server_port integer, validation_state text NOT NULL, access_state text NOT NULL, paid_state text NOT NULL, credentials_state text NOT NULL, origin text NOT NULL, restored_at timestamptz NOT NULL DEFAULT now())"))
    await db_session.execute(text("DELETE FROM recovery_control"))
    await db_session.execute(text("INSERT INTO recovery_control(id,version,restore_id,archive_sha256,target_database,server_address,server_port,validation_state,access_state,paid_state,credentials_state,origin) VALUES(1,1,repeat('a',32),repeat('b',64),current_database(),inet_server_addr()::text,inet_server_port(),'PENDING','QUARANTINED','BLOCKED','BLOCKED','RESTORE')"))
    await db_session.commit()
    try:
        with pytest.raises(HTTPException) as exc:
            await require_auth(authorization='Bearer ' + get_settings().api_token, x_api_token=None)
        assert exc.value.status_code == 503
    finally:
        await db_session.execute(text("DELETE FROM recovery_control"))
        from app.services.recovery_control import provision_known_installation
        await provision_known_installation(db_session)
        await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'version', 'identity', 'partial-approval'])
async def test_unknown_control_is_not_a_normal_installation(db_session, fault):
    from app.services.recovery_control import RecoveryBlocked, provision_known_installation, require_normal
    try:
        if fault == 'missing':
            await db_session.execute(text('DELETE FROM recovery_control'))
        elif fault == 'version':
            await db_session.execute(text('UPDATE recovery_control SET version=999'))
        elif fault == 'identity':
            await db_session.execute(text("UPDATE recovery_control SET target_database='different_test'"))
        else:
            await db_session.execute(text("UPDATE recovery_control SET origin='RESTORE', access_state='NORMAL', paid_state='BLOCKED'"))
        await db_session.commit()
        with pytest.raises(RecoveryBlocked):
            await require_normal(db_session)
        if fault != 'missing':
            with pytest.raises(RecoveryBlocked):
                await provision_known_installation(db_session)
    finally:
        await db_session.rollback()
        await db_session.execute(text('DELETE FROM recovery_control'))
        await provision_known_installation(db_session)
        await db_session.commit()
