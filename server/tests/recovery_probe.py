"""Separate-process probe, only invoked against an explicitly restored *_test database."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.api.deps import require_auth
from app.db import SessionLocal, engine
from app.services import github_publish, llm, public_model
from app.services.private_model_billing import reserve
from app.services.repository_credentials import repository_credentials


async def refused(call):
    try:
        await call
    except HTTPException as error:
        assert error.status_code == 503
    else:
        raise AssertionError('Recovery boundary allowed access')


async def main():
    assert make_url(os.environ['DATABASE_URL']).database.endswith('_test')
    os.environ['SEED_PUBLIC_ORIGIN'] = 'http://localhost'
    os.environ['SEED_SESSION_SECRET'] = 'F6OnlySyntheticSessionSecret_8f991a0e4c62'
    from app.config import get_settings
    await refused(require_auth(authorization='Bearer ' + get_settings().api_token, x_api_token=None))
    await refused(reserve(None, {}, '', '', 1))
    await refused(public_model._claim(1, 'synthetic', 1, 1))
    await refused(llm.get_llm_config())
    await refused(github_publish.get_github_token())
    from app import project_worker
    from app.services import assessment_worker, podcast_jobs, project_scheduler, study_notes
    await refused(project_worker.reconcile(SessionLocal))
    await refused(assessment_worker.reconcile(SessionLocal))
    await refused(project_scheduler.tick(SessionLocal))
    await refused(study_notes.process_next_note_job(SessionLocal))
    await refused(podcast_jobs.process_next())
    async with SessionLocal() as session:
        await refused(repository_credentials(session, 1))
        # Even an access-only state change cannot enable paid or credential state.
        await session.execute(text("UPDATE recovery_control SET access_state='NORMAL'"))
        await session.commit()
    await refused(reserve(None, {}, '', '', 1))
    from unittest.mock import AsyncMock

    from app import main as private_entry
    from app.main import app
    from app.public_main import app as registered
    private_entry.question_generation.recover_interrupted = AsyncMock(side_effect=AssertionError('must not resume'))
    private_entry.project_analysis.recover_interrupted = AsyncMock(side_effect=AssertionError('must not resume'))
    private_entry.recover_study_chat = AsyncMock(side_effect=AssertionError('must not resume'))
    private_entry.run_podcast_worker = AsyncMock(side_effect=AssertionError('must not start'))
    async with private_entry.lifespan(app):
        assert private_entry.run_podcast_worker.call_count == 0
        assert private_entry.recover_study_chat.call_count == 0
    for application in [app, registered]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://localhost') as client:
            for path in ['/api/v1/settings/llm', '/api/v1/auth/login', '/api/v1/auth/recover']:
                assert (await client.post(path, json={})).status_code == 503
            assert (await client.get('/healthz')).status_code == 200
    async with SessionLocal.begin() as session:
        await session.execute(text("UPDATE recovery_control SET access_state='QUARANTINED'"))
    from sqlalchemy import delete, select

    from app.config import get_settings
    from app.models import AppSetting
    cfg = get_settings()
    cfg.llm_api_key = cfg.github_token = cfg.gitcode_token = 'synthetic-no-row-env-fallback'
    async with SessionLocal.begin() as session:
        saved = [(row.key, row.value) for row in await session.scalars(select(AppSetting))]
        await session.execute(delete(AppSetting))
    try:
        await refused(llm.get_llm_config())
        await refused(github_publish.get_github_token())
        from app.services.gitcode_repo import get_gitcode_token
        await refused(get_gitcode_token())
    finally:
        async with SessionLocal.begin() as session:
            session.add_all(AppSetting(key=key, value=value) for key, value in saved)
    await engine.dispose()
    print('all boundaries refused; no providers called')


if __name__ == '__main__':
    asyncio.run(main())
