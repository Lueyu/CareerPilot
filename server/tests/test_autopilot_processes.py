"""Real PostgreSQL/OS fault tests with explicitly fake semantic stage outputs.

No model reliability or actual repository-analysis quality is claimed. Every test
creates a fresh isolated database and retains it for recovery inspection.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import AnalysisStep, AppSetting, Base, Project, ProjectAnalysis, WorkerHeartbeat, Workspace
from app.services import project_autopilot_engine as analysis_engine
from app.services import project_jobs as jobs
from app.services.project_autopilot_config import CONSENT_VERSION

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent

HARNESS = r'''
import asyncio,json,os,sys,time
from pathlib import Path
sys.path[:0]=json.loads(os.environ['FIXTURE_PYTHON_PATHS'])
from app.services import project_autopilot_engine as analysis
from app.services.llm import AgentAdapter
import httpx

async def forbidden(*args,**kwargs):
    raise AssertionError('Process fixture must never call a real source or model')
AgentAdapter.chat=forbidden
AgentAdapter.chat_with=forbidden
httpx.AsyncClient.request=forbidden

folder=Path(os.environ['FIXTURE_FOLDER'])
def plan(context,artifacts):
    return [
      {'key':'resolve','stage':'resolve','requires':[]},
      {'key':'module:fast','stage':'analyze_module','requires':['resolve'],'payload':{'name':'fast'}},
      {'key':'module:slow','stage':'analyze_module','requires':['module:fast'],'payload':{'name':'slow'}},
      {'key':'report','stage':'report','requires':['module:slow']},
      {'key':'candidates','stage':'candidates','requires':['report']}]

async def execute(stage,inputs,runtime):
    name=inputs.get('payload',{}).get('name',stage)
    with (folder/'calls.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps({'stage':stage,'name':name,'pid':os.getpid(),'at':time.time()})+'\n')
    if name=='slow':
        if os.environ.get('FIXTURE_MODE')=='timeout_once' and not (folder/'timeout.recorded').exists():
            (folder/'timeout.recorded').write_text('controlled timeout, no provider called')
            raise analysis.StageError('REQUEST_TIMEOUT','controlled fixture timeout')
        while not (folder/'release-slow').exists():
            await asyncio.sleep(.05)
    if stage=='resolve': artifact={'target_sha':'f'*40,'branch':'main'}
    elif stage=='report': artifact={'source_revision':'f'*40,'coverage_status':'COMPLETE',
        'snapshot':{'files':[{'path':'fixture.py','content':'fixture_only = True'}]},
        'report':{'summary':'Explicit process-mechanism fixture','claims':[],'issues':[],'sections':[]}}
    elif stage=='candidates': artifact={'candidates':[]}
    else: artifact={'fixture':True,'module':name,'claims':[]}
    artifact['provenance']=analysis._provenance(inputs['context'],runtime)
    return {'artifact':artifact,'usage':{'known':False},'request_meta':{'adapter':'explicit-process-fixture'}}

analysis.plan_steps=plan
analysis.execute_stage=execute
from app import project_worker
asyncio.run(project_worker.serve(poll_seconds=.05,lease_seconds=1.2,heartbeat_seconds=.2))
'''


@dataclass
class Fixture:
    folder: Path
    database: str
    workspace_id: int
    workspace_slug: str
    project_id: int
    run_id: int
    factory: object = field(repr=False)
    environment: dict = field(repr=False)
    processes: list = field(default_factory=list, repr=False)
    streams: list = field(default_factory=list, repr=False)

    def start(self, kind, mode='block', port=None):
        env = dict(self.environment)
        env.update(FIXTURE_FOLDER=str(self.folder), FIXTURE_MODE=mode,
            FIXTURE_PYTHON_PATHS=json.dumps([str(SERVER), str(Path(sys.executable).parents[1] / 'Lib/site-packages')]))
        script = self.folder / f'{kind}-harness.py'
        if kind == 'worker':
            script.write_text(HARNESS, encoding='utf-8')
        else:
            script.write_text("import sys,os,json\nsys.path[:0]=json.loads(os.environ['FIXTURE_PYTHON_PATHS'])\n"
                f"import uvicorn\nuvicorn.run('app.main:app',host='127.0.0.1',port={port},log_level='warning')\n", encoding='utf-8')
        out = (self.folder / f'{kind}-{len(self.processes)}.out.log').open('wb')
        err = (self.folder / f'{kind}-{len(self.processes)}.err.log').open('wb')
        self.streams.extend([out, err])
        # Base executable plus explicit venv site-packages avoids Windows venv
        # redirector children: Popen's held handle identifies the actual process.
        process = subprocess.Popen([sys._base_executable, str(script)], cwd=SERVER, env=env,
            stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        self.processes.append(process)
        return process

    async def state(self):
        async with self.factory() as session:
            run = await session.get(ProjectAnalysis, self.run_id)
            steps = list(await session.scalars(select(AnalysisStep).where(AnalysisStep.run_id == self.run_id)))
            beats = list(await session.scalars(select(WorkerHeartbeat)))
            return {'status': run.status, 'steps': {s.step_key: {'status': s.status, 'attempts': s.attempt_count,
                'artifact': s.output_json, 'history': s.attempts, 'error_category': s.error_category,
                'lease_expires_at': s.lease_expires_at} for s in steps},
                'heartbeats': {b.pid: b.last_seen_at for b in beats}}

    async def until(self, predicate, timeout=25):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = await self.state()
            if predicate(result):
                return result
            if self.processes and all(p.poll() is not None for p in self.processes):
                pytest.fail('All fixture processes exited before the durable condition; private fixture logs retained')
            await asyncio.sleep(.08)
        pytest.fail('Durable condition timed out; fixture database and process logs retained')

    def stop(self, process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


@pytest_asyncio.fixture
async def process_fixture(tmp_path):
    # This URL is set by conftest, which rejects a personal DB; do not read a
    # restored personal DB's settings or inherit live provider credentials.
    url = make_url(os.environ['CAREERPILOT_TEST_DATABASE_URL'])
    assert url.database.endswith('_test')
    database = 'careerpilot_process_' + uuid4().hex[:14] + '_test'
    options = dict(host=url.host, port=url.port or 5432, user=url.username, password=url.password)
    admin = await asyncpg.connect(database='postgres', **options)
    try:
        assert not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)', database)
        await admin.execute('CREATE DATABASE "' + database + '"')
    finally:
        await admin.close()
    isolated = url.set(database=database)
    engine = create_async_engine(isolated, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    connection = await asyncpg.connect(database=database, **options)
    try:
        for migration in sorted((SERVER / 'migrations').glob('*.sql')):
            await connection.execute(migration.read_text('utf-8'))
    finally:
        await connection.close()
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as session:
        workspace = Workspace(slug='process-fixture', name='Explicit OS process fixture')
        session.add(workspace)
        await session.flush()
        project = Project(workspace_id=workspace.id, name='OS checkpoint fixture', owner='fixture', repo='repo',
            repo_url='https://github.com/fixture/repo', branch='main', meta={'kind': 'github'},
            next_check_at=jobs.utcnow() + timedelta(days=1))
        session.add(project)
        session.add(AppSetting(key=f'project-autopilot:{workspace.id}', value={'enabled': True,
            'consent_version': CONSENT_VERSION, 'budget': {'max_model_calls': 32, 'max_source_bytes': 10000}}))
        await session.flush()
        run = await jobs.enqueue(session, project, uuid4().hex,
            pipeline_version=analysis_engine.PIPELINE_VERSION, prompt_bundle_version=analysis_engine.PROMPT_BUNDLE_VERSION)
        await session.commit()
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
        'PROGRAMFILES', 'PROGRAMFILES(X86)', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'PATHEXT'}}
    env.update(DATABASE_URL=isolated.render_as_string(hide_password=False), API_TOKEN='process-fixture-token',
        LLM_PROVIDER='', LLM_API_KEY='', LLM_BASE_URL='', LLM_MODEL='', GITHUB_TOKEN='', GITCODE_TOKEN='',
        PYTHONIOENCODING='utf-8', DEFAULT_WORKSPACE_SLUG=workspace.slug)
    fixture = Fixture(tmp_path, database, workspace.id, workspace.slug, project.id, run.id, factory, env)
    (tmp_path / 'fixture-identifiers.json').write_text(json.dumps({'verification': 'fixture_stages_real_database_and_os_processes_no_model',
        'database': database, 'workspace_id': workspace.id, 'project_id': project.id, 'run_id': run.id}), encoding='utf-8')
    try:
        yield fixture
    finally:
        for process in fixture.processes:
            fixture.stop(process)
        for stream in fixture.streams:
            stream.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_killed_worker_resumes_only_unfinished_module_from_durable_checkpoints(process_fixture):
    f = process_fixture
    first = f.start('worker')
    running = await f.until(lambda s: s['steps'].get('module:slow', {}).get('status') == 'RUNNING')
    assert running['steps']['module:fast']['attempts'] == 1
    f.stop(first)
    (f.folder / 'release-slow').touch()
    second = f.start('worker')
    complete = await f.until(lambda s: s['status'] == 'COMPLETED')
    assert second.poll() is None
    assert complete['steps']['resolve']['attempts'] == 1
    assert complete['steps']['module:fast']['attempts'] == 1
    assert complete['steps']['module:slow']['attempts'] == 2
    assert any(a['outcome'] == 'OUTCOME_UNKNOWN' for a in complete['steps']['module:slow']['history'])
    assert complete['steps']['module:fast']['artifact']['module'] == 'fast'


@pytest.mark.asyncio
async def test_one_module_timeout_retries_without_replaying_completed_modules(process_fixture):
    f = process_fixture
    (f.folder / 'release-slow').touch()
    worker = f.start('worker', mode='timeout_once')
    failed = await f.until(lambda s: s['steps'].get('module:slow', {}).get('status') == 'RETRY_WAIT')
    assert failed['status'] == 'RETRY_WAIT'
    assert failed['steps']['module:slow']['error_category'] == 'REQUEST_TIMEOUT'
    assert failed['steps']['module:fast']['status'] == 'SUCCEEDED'
    complete = await f.until(lambda s: s['status'] == 'COMPLETED')
    assert worker.poll() is None
    assert complete['steps']['module:fast']['attempts'] == 1
    assert complete['steps']['module:slow']['attempts'] == 2


async def api_ready(process, port):
    deadline = time.monotonic() + 18
    async with httpx.AsyncClient(trust_env=False) as client:
        while time.monotonic() < deadline:
            assert process.poll() is None, 'Isolated real API exited during startup'
            try:
                result = await client.get(f'http://127.0.0.1:{port}/healthz', timeout=.5)
                if result.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.1)
    pytest.fail('Isolated real API failed to become healthy')


@pytest.mark.asyncio
async def test_browser_disconnect_and_real_api_restart_preserve_worker_run(process_fixture):
    f = process_fixture
    worker = f.start('worker')
    await f.until(lambda s: s['steps'].get('module:slow', {}).get('status') == 'RUNNING')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    api = f.start('api', port=port)
    await api_ready(api, port)
    headers = {'X-API-Token': 'process-fixture-token', 'X-Workspace': f.workspace_slug}
    path = f'/api/v1/projects/{f.project_id}/analyses/{f.run_id}'
    async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', headers=headers, trust_env=False) as browser:
        response = await browser.get(path)
        assert response.status_code == 200
    before = await f.state()
    assert before['status'] == 'RUNNING', 'API startup recovery changed a durable Worker run'
    heartbeat = before['heartbeats'][worker.pid]
    # Browser client disconnected; the separate Worker continues renewing leases.
    await f.until(lambda s: s['heartbeats'].get(worker.pid, heartbeat) > heartbeat)
    f.stop(api)
    restarted = f.start('api', port=port)
    await api_ready(restarted, port)
    state = await f.state()
    assert worker.poll() is None and state['status'] == 'RUNNING'
    assert state['steps']['module:slow']['attempts'] == 1
    (f.folder / 'release-slow').touch()
    complete = await f.until(lambda s: s['status'] == 'COMPLETED')
    assert complete['steps']['module:slow']['attempts'] == 1
