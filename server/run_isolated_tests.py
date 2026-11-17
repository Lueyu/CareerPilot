"""Run one pytest invocation in a fresh PostgreSQL *_test database.

Keeps the database for inspection. Never drops or reuses a personal database.
Usage from server/: python run_isolated_tests.py tests -q --tb=short
"""
import asyncio
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import asyncpg
from sqlalchemy.engine import make_url
from app.config import get_settings


async def main():
    source=make_url(get_settings().database_url)
    name='careerpilot_suite_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:6]+'_test'
    connection=await asyncpg.connect(source.set(database='postgres').render_as_string(hide_password=False).replace('+asyncpg',''))
    try:
        await connection.execute('CREATE DATABASE "'+name+'"')
    finally:
        await connection.close()
    env=os.environ.copy()
    env['CAREERPILOT_TEST_DATABASE_URL']=source.set(database=name).render_as_string(hide_password=False)
    env['PYTHONIOENCODING']='utf-8'
    print('Isolated test database: '+name,flush=True)
    return subprocess.call([sys.executable,'-m','pytest',*(sys.argv[1:] or ['tests','-q','--tb=short'])],
        cwd=Path(__file__).resolve().parent,env=env)


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
