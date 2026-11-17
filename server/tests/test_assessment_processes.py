"""Real Worker/API process failures, fresh PostgreSQL, explicit non-network judge."""
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base, CompetencySnapshot, Evidence, Exam, ScoringRun, ScoringStep, Workspace
from test_assessment_reliability import prepared

SERVER = Path(__file__).resolve().parents[1]
HARNESS = r'''
import asyncio,json,os,sys,time
from pathlib import Path
sys.path[:0]=json.loads(os.environ['FIXTURE_PYTHON_PATHS'])
import httpx
from app.services import assessment_jobs as jobs,assessment_worker as worker
from app.services.llm import AgentAdapter
from test_assessment_reliability import result_for
folder=Path(os.environ['FIXTURE_FOLDER'])
async def forbidden(*args,**kwargs):raise AssertionError('Fixture forbids live model/source calls')
AgentAdapter.chat=forbidden
AgentAdapter.chat_with=forbidden
httpx.AsyncClient.request=forbidden
async def config(*args,**kwargs):return {'provider':'fixture','model':'assessment-fixture','base_url':'https://fixture.invalid','api_key':'fixture-not-a-live-key'},{},{}
jobs.runtime_config=config
worker.runtime_config=config
worker.OUTBOX_ROOT=folder/'private-outbox'
async def evaluate(claim,cfg,repair=None):
    with (folder/'model-calls.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps({'step_id':claim.step_id,'title':claim.payload['title'],'pid':os.getpid(),'time':time.time()})+'\n')
    if claim.payload['title'].endswith(' 2'):
        while not (folder/'release-slow').exists():await asyncio.sleep(.05)
    return result_for(claim,criterion_score=2)
worker.evaluate=evaluate
from app import project_worker
asyncio.run(project_worker.serve(poll_seconds=.05,lease_seconds=1.2,heartbeat_seconds=.2))
'''


@dataclass
class Fixture:
    folder: Path
    database: str
    exam_id: int
    run_id: int
    workspace_id: int
    factory: object = field(repr=False)
    env: dict = field(repr=False)
    processes: list = field(default_factory=list, repr=False)
    streams: list = field(default_factory=list, repr=False)

    def start(self, kind, port=None):
        env = dict(self.env)
        env.update(FIXTURE_FOLDER=str(self.folder), FIXTURE_PYTHON_PATHS=json.dumps([
            str(SERVER), str(SERVER / 'tests'), str(Path(sys.executable).parents[1] / 'Lib/site-packages')]))
        script = self.folder / f'{kind}-harness.py'
        script.write_text(HARNESS if kind == 'worker' else
            "import sys,os,json\nsys.path[:0]=json.loads(os.environ['FIXTURE_PYTHON_PATHS'])\n"
            + f"import uvicorn\nuvicorn.run('app.main:app',host='127.0.0.1',port={port},log_level='warning')\n", encoding='utf-8')
        out = (self.folder / f'{kind}-{len(self.processes)}.out.log').open('wb')
        err = (self.folder / f'{kind}-{len(self.processes)}.err.log').open('wb')
        self.streams.extend([out, err])
        process = subprocess.Popen([sys._base_executable, str(script)], cwd=SERVER, env=env,
            stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        self.processes.append(process)
        return process

    def stop(self, process):
        if process.poll() is None:process.kill()
        process.wait(timeout=10)

    async def state(self):
        async with self.factory() as session:
            run = await session.get(ScoringRun, self.run_id)
            exam = await session.get(Exam, self.exam_id)
            steps = list(await session.scalars(select(ScoringStep).where(ScoringStep.run_id == self.run_id).order_by(ScoringStep.order_no)))
            return {'run_status': run.status, 'exam_status': exam.status,
                'steps': {s.order_no: {'id': s.id, 'status': s.status, 'attempts': s.attempt_count,
                    'history': s.attempts, 'input_hash': s.input_hash, 'result': s.result_json} for s in steps},
                'evidence': await session.scalar(select(func.count()).select_from(Evidence).where(Evidence.workspace_id == self.workspace_id)),
                'snapshots': await session.scalar(select(func.count()).select_from(CompetencySnapshot).where(CompetencySnapshot.workspace_id == self.workspace_id))}

    async def until(self, predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = await self.state()
            if predicate(state):return state
            if self.processes and all(p.poll() is not None for p in self.processes):
                pytest.fail('All fixture processes exited; private logs retained')
            await asyncio.sleep(.08)
        pytest.fail('Durable condition timed out; private process logs and test DB retained')


@pytest_asyncio.fixture
async def assessment_process(tmp_path, monkeypatch):
    url = make_url(os.environ['CAREERPILOT_TEST_DATABASE_URL'])
    assert url.database.endswith('_test')
    database = 'careerpilot_assessment_process_' + uuid4().hex[:12] + '_test'
    options = dict(host=url.host, port=url.port or 5432, user=url.username, password=url.password)
    admin = await asyncpg.connect(database='postgres', **options)
    try:
        assert not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)', database)
        await admin.execute('CREATE DATABASE "' + database + '"')
    finally:await admin.close()
    isolated = url.set(database=database)
    engine = create_async_engine(isolated, poolclass=NullPool)
    async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
    connection = await asyncpg.connect(database=database, **options)
    try:
        for migration in sorted((SERVER / 'migrations').glob('*.sql')):await connection.execute(migration.read_text('utf-8'))
    finally:await connection.close()
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        workspace = Workspace(slug='assessment-process-fixture', name='Explicit assessment process fixture')
        session.add(workspace);await session.flush()
        exam, run, _ = await prepared(session, workspace, monkeypatch, count=2)
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
        'PROGRAMFILES', 'PROGRAMFILES(X86)', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'PATHEXT'}}
    env.update(DATABASE_URL=isolated.render_as_string(hide_password=False), API_TOKEN='assessment-process-fixture',
        DEFAULT_WORKSPACE_SLUG=workspace.slug, LLM_PROVIDER='', LLM_MODEL='', LLM_BASE_URL='', LLM_API_KEY='',
        GITHUB_TOKEN='', GITCODE_TOKEN='', PYTHONIOENCODING='utf-8')
    fixture = Fixture(tmp_path, database, exam.id, run.id, workspace.id, factory, env)
    (tmp_path / 'fixture-identifiers.json').write_text(json.dumps({'verification': 'real_database_and_os_processes_fixture_model_no_live_provider',
        'database': database, 'exam_id': exam.id, 'run_id': run.id, 'workspace_id': workspace.id}), encoding='utf-8')
    try:yield fixture
    finally:
        for process in fixture.processes:fixture.stop(process)
        for stream in fixture.streams:stream.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_worker_kill_preserves_success_and_recovers_only_interrupted_question(assessment_process):
    f = assessment_process
    first = f.start('worker')
    interrupted = await f.until(lambda s: s['steps'][2]['status'] == 'RUNNING')
    assert interrupted['steps'][1]['status'] == 'SUCCEEDED'
    assert interrupted['evidence'] == interrupted['snapshots'] == 0
    f.stop(first)
    (f.folder / 'release-slow').touch()
    second = f.start('worker')
    completed = await f.until(lambda s: s['run_status'] == 'COMPLETED')
    assert second.poll() is None and completed['exam_status'] == 'COMPLETED'
    assert completed['steps'][1]['result'] == interrupted['steps'][1]['result']
    assert completed['steps'][1]['attempts'] == 1 and completed['steps'][2]['attempts'] == 2
    assert any(a['outcome'] == 'OUTCOME_UNKNOWN' for a in completed['steps'][2]['history'])
    assert completed['evidence'] == 2 and completed['snapshots'] == 1


async def ready(process, port):
    deadline = time.monotonic() + 15
    async with httpx.AsyncClient(trust_env=False) as client:
        while time.monotonic() < deadline:
            assert process.poll() is None, 'Isolated API exited; private logs retained'
            try:
                if (await client.get(f'http://127.0.0.1:{port}/healthz', timeout=.5)).status_code == 200:return
            except httpx.HTTPError:pass
            await asyncio.sleep(.1)
    pytest.fail('Isolated API did not become ready')


@pytest.mark.asyncio
async def test_http_disconnect_and_real_api_restart_leave_grading_worker_alive(assessment_process):
    f = assessment_process
    worker = f.start('worker')
    await f.until(lambda s: s['steps'][2]['status'] == 'RUNNING')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    api = f.start('api', port)
    await ready(api, port)
    headers = {'X-API-Token': 'assessment-process-fixture', 'X-Workspace': 'assessment-process-fixture'}
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(f'http://127.0.0.1:{port}/api/v1/exams/{f.exam_id}/submit', headers=headers)
        assert response.status_code == 202 and response.json()['task_id'] == f.run_id
    # The real client is disconnected. API is independently killed/restarted.
    f.stop(api)
    restarted = f.start('api', port)
    await ready(restarted, port)
    assert worker.poll() is None
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.get(f'http://127.0.0.1:{port}/api/v1/exams/{f.exam_id}/report', headers=headers)
        assert response.status_code == 200 and response.json()['score'] is None
        assert response.json()['scoring_task']['id'] == f.run_id
    (f.folder / 'release-slow').touch()
    done = await f.until(lambda s: s['run_status'] == 'COMPLETED')
    assert worker.poll() is None and done['evidence'] == 2 and done['snapshots'] == 1
    assert done['steps'][1]['attempts'] == done['steps'][2]['attempts'] == 1
