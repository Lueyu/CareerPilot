"""Isolated OS/restore harness for learning feedback; no live model calls.

This file provides test infrastructure, not completed learning-loop acceptance.
Seed callbacks must use the real learning services once their contracts exist.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import asyncpg
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from test_assessment_processes import Fixture, ready

SERVER = Path(__file__).resolve().parents[1]
CONFIG = {'provider':'fixture','model':'assessment-fixture',
          'base_url':'https://fixture.invalid','api_key':'fixture-not-a-live-key'}

COMMON_HARNESS = r'''
import asyncio,json,os,sys,time
from pathlib import Path
sys.path[:0]=json.loads(os.environ['FIXTURE_PYTHON_PATHS'])
import httpx
from app.services.llm import AgentAdapter
async def forbidden(*args,**kwargs):raise AssertionError('Explicit fixture: external calls forbidden')
AgentAdapter.chat=forbidden
AgentAdapter.chat_with=forbidden
httpx.AsyncClient.request=forbidden
'''

WORKER_HARNESS = COMMON_HARNESS + r'''
from app.services import assessment_jobs as jobs,assessment_worker as worker
from test_assessment_reliability import result_for
folder=Path(os.environ['FIXTURE_FOLDER'])
async def config(*args,**kwargs):return {'provider':'fixture','model':'assessment-fixture','base_url':'https://fixture.invalid','api_key':'fixture-not-a-live-key'},{},{}
jobs.runtime_config=config
worker.runtime_config=config
worker.OUTBOX_ROOT=folder/'private-outbox'
async def evaluate(claim,cfg,repair=None):
    with (folder/'model-calls.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps({'run_id':claim.run_id,'step_id':claim.step_id,'pid':os.getpid(),'time':time.time()})+'\n')
    gate=folder/'slow-step-id.txt'
    if gate.exists() and claim.step_id==int(gate.read_text('ascii')):
        while not (folder/'release-slow').exists():await asyncio.sleep(.05)
    result=result_for(claim,criterion_score=2)
    if claim.payload.get('learning_activity_kind')=='PROJECT':
        from app.services.assessment_rubric import validate_judgement
        from app.services.project_practice import validate_review_result
        fields=json.loads(result['raw_response'])
        answer=claim.payload['submitted_answer']['text_answer']
        source_text=claim.payload['project_source_text']
        lines=[line for line in source_text.splitlines() if line.startswith('[') and ':L' in line]
        assert lines,'Project fixture needs actual normalized source citations'
        for row in fields['criterion_scores']:
            if row['criterion_id'] in ('project_requirements','project_verification'):
                row['answer_quotes']=[next((line for line in lines if 'test_' in line),lines[0])
                    if row['criterion_id']=='project_verification' else lines[0]]
        validated=validate_judgement(fields,rubric=claim.payload['rubric'],text=answer,
            question_id=claim.payload['question_id'],answer_revision_id=claim.payload['answer_revision_id'])
        result={**validated,'raw_response':json.dumps(fields),'usage':{'known':False},
            'request_meta':{'adapter':'explicit-project-fixture-no-network'}}
        result=validate_review_result(result,claim.payload)
    return result
worker.evaluate=evaluate
from app import project_worker
asyncio.run(project_worker.serve(poll_seconds=.05,lease_seconds=1.2,heartbeat_seconds=.2))
'''


@dataclass
class LearningProcesses(Fixture):
    """Owns only handles it spawned; inherited state reads actual scoring tables."""

    def start(self, kind, port=None):
        assert kind in ('worker','api')
        env=dict(self.env)
        env.update(FIXTURE_FOLDER=str(self.folder),FIXTURE_PYTHON_PATHS=json.dumps([
            str(SERVER),str(SERVER/'tests'),str(Path(sys.executable).parents[1]/'Lib/site-packages')]))
        script=self.folder/f'{kind}-harness.py'
        script.write_text(WORKER_HARNESS if kind=='worker' else COMMON_HARNESS+
            f"\nimport uvicorn\nuvicorn.run('app.main:app',host='127.0.0.1',port={int(port)},log_level='warning')\n",encoding='utf-8')
        out=(self.folder/f'{kind}-{len(self.processes)}.out.log').open('wb')
        err=(self.folder/f'{kind}-{len(self.processes)}.err.log').open('wb')
        self.streams.extend([out,err])
        process=subprocess.Popen([sys._base_executable,str(script)],cwd=SERVER,env=env,
            stdin=subprocess.DEVNULL,stdout=out,stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.processes.append(process)
        return process


def ident(value):
    assert re.fullmatch(r'[a-z_][a-z0-9_]*',value)
    return '"'+value+'"'


def test_target(prefix):
    name='careerpilot_learning_'+prefix+'_'+uuid4().hex[:12]+'_test'
    assert re.fullmatch(r'[a-z][a-z0-9_]{0,57}_test',name)
    return name


def options(url):
    return dict(host=url.host,port=url.port or 5432,user=url.username,password=url.password)


@asynccontextmanager
async def isolated_learning_processes(folder, seed):
    """seed(session) returns workspace/exam/run IDs after real service submission.

    This callback must not manufacture a feedback result or overwrite history.
    All recorded content in this harness is explicitly a test fixture.
    """
    url=make_url(os.environ['CAREERPILOT_TEST_DATABASE_URL'])
    assert url.database.endswith('_test')
    database=test_target('process')
    admin=await asyncpg.connect(database='postgres',**options(url))
    try:
        assert not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)',database)
        await admin.execute('CREATE DATABASE '+ident(database))
    finally:await admin.close()
    isolated=url.set(database=database)
    engine=create_async_engine(isolated,poolclass=NullPool)
    processes=None
    try:
        async with engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
        connection=await asyncpg.connect(database=database,**options(url))
        try:
            for migration in sorted((SERVER/'migrations').glob('*.sql')):
                await connection.execute(migration.read_text('utf-8'))
        finally:await connection.close()
        factory=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
        async with factory() as session:
            ids=await seed(session)
            await session.commit()
        env={k:v for k,v in os.environ.items() if k.upper() in {
            'PATH','SYSTEMROOT','WINDIR','COMSPEC','USERPROFILE','APPDATA','LOCALAPPDATA',
            'PROGRAMFILES','PROGRAMFILES(X86)','SYSTEMDRIVE','TEMP','TMP','PATHEXT'}}
        env.update(DATABASE_URL=isolated.render_as_string(hide_password=False),
            API_TOKEN='learning-process-fixture',DEFAULT_WORKSPACE_SLUG=ids['workspace_slug'],
            LLM_PROVIDER='',LLM_MODEL='',LLM_BASE_URL='',LLM_API_KEY='',GITHUB_TOKEN='',
            GITCODE_TOKEN='',PYTHONIOENCODING='utf-8')
        processes=LearningProcesses(folder,database,ids['exam_id'],ids['run_id'],ids['workspace_id'],factory,env)
        (folder/'fixture-identifiers.json').write_text(json.dumps({
            'verification':'real_os_and_database_fixture_model_no_live_provider',
            'database':database,**ids}),encoding='utf-8')
        yield processes,ids
    finally:
        if processes:
            for process in processes.processes:processes.stop(process)
            for stream in processes.streams:stream.close()
        await engine.dispose()


async def database_snapshot(connection):
    columns=await connection.fetch("SELECT table_name,column_name,data_type,udt_name FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position")
    tables={}
    for name in sorted({r['table_name'] for r in columns}):
        values=await connection.fetch('SELECT to_jsonb(t)::text FROM public.'+ident(name)+' t')
        canonical=sorted(json.dumps(json.loads(r[0]),ensure_ascii=False,sort_keys=True,separators=(',',':')) for r in values)
        tables[name]={'rows':len(values),'sha256':hashlib.sha256(('\n'.join(canonical)+'\n').encode()).hexdigest()}
    sequences=[]
    for row in await connection.fetch("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"):
        seq=await connection.fetchrow('SELECT last_value,is_called FROM public.'+ident(row[0]))
        sequences.append([row[0],seq['last_value'],seq['is_called']])
    keys=await connection.fetch('''SELECT con.conname,child.relname AS child,parent.relname AS parent,con.convalidated,
        ARRAY(SELECT att.attname FROM unnest(con.conkey) WITH ORDINALITY k(num,ord)
            JOIN pg_attribute att ON att.attrelid=con.conrelid AND att.attnum=k.num ORDER BY k.ord) AS child_columns,
        ARRAY(SELECT att.attname FROM unnest(con.confkey) WITH ORDINALITY k(num,ord)
            JOIN pg_attribute att ON att.attrelid=con.confrelid AND att.attnum=k.num ORDER BY k.ord) AS parent_columns
        FROM pg_constraint con JOIN pg_class child ON child.oid=con.conrelid
        JOIN pg_namespace ns ON ns.oid=child.relnamespace JOIN pg_class parent ON parent.oid=con.confrelid
        WHERE con.contype='f' AND ns.nspname='public' ORDER BY child.relname,con.conname''')
    checks=[]
    for key in keys:
        nonnull=' AND '.join('c.'+ident(c)+' IS NOT NULL' for c in key['child_columns'])
        match=' AND '.join('c.'+ident(a)+'=p.'+ident(b) for a,b in zip(key['child_columns'],key['parent_columns']))
        count=await connection.fetchval('SELECT count(*) FROM public.'+ident(key['child'])+
            ' c WHERE '+nonnull+' AND NOT EXISTS (SELECT 1 FROM public.'+ident(key['parent'])+' p WHERE '+match+')')
        checks.append({**dict(key),'orphans':count})
    assert all(k['orphans']==0 for k in checks)
    return {'columns':[dict(r) for r in columns],'tables':tables,'sequences':sequences,'foreign_keys':checks}


async def transactional_dump_restore(source_url, folder):
    """Export snapshot, compare every public table/sequence/FK in a new test DB.

    Caller must stop its held Worker before invocation to preserve orphan-lease
    test setup and must separately verify domain joins and outbox attachments.
    """
    assert source_url.database.endswith('_test')
    target=test_target('restore')
    assert target!=source_url.database
    folder.mkdir(parents=True,exist_ok=False)
    archive=folder/'learning-feedback-fixture.dump'
    env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
    env.update(PGHOST=source_url.host,PGPORT=str(source_url.port or 5432),
        PGUSER=source_url.username,PGPASSWORD=source_url.password)
    async def invoke(args):
        result=await asyncio.to_thread(subprocess.run,args,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        assert result.returncode==0,'PostgreSQL tool failed; private stderr intentionally withheld'
        return result.stdout.decode('utf-8',errors='replace')
    source=await asyncpg.connect(database=source_url.database,server_settings={'default_transaction_read_only':'on'},**options(source_url))
    admin=await asyncpg.connect(database='postgres',**options(source_url))
    restored=None
    try:
        assert not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)',target)
        async with source.transaction(isolation='repeatable_read',readonly=True):
            snapshot=await source.fetchval('SELECT pg_export_snapshot()')
            before=await database_snapshot(source)
            await invoke(['pg_dump','--no-password','--dbname='+source_url.database,
                '--snapshot='+snapshot,'--format=custom','--file='+str(archive)])
            listing=await invoke(['pg_restore','--list',str(archive)])
            entries=sum(bool(l.strip()) and not l.startswith(';') for l in listing.splitlines())
            assert archive.stat().st_size>0 and entries>0
            checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
            (folder/'learning-feedback-fixture.dump.sha256').write_text(checksum+'\n',encoding='ascii')
            await admin.execute('CREATE DATABASE '+ident(target))
            await invoke(['pg_restore','--no-password','--dbname='+target,
                '--single-transaction','--exit-on-error','--no-owner','--no-privileges',str(archive)])
            restored=await asyncpg.connect(database=target,server_settings={'default_transaction_read_only':'on'},**options(source_url))
            async with restored.transaction(isolation='repeatable_read',readonly=True):
                after=await database_snapshot(restored)
            assert before==after
        report={'status':'PASS','verification':'fixture_database_restore_not_live_model_acceptance',
            'source_database':source_url.database,'target_database':target,'archive_bytes':archive.stat().st_size,
            'sha256':checksum,'toc_entries':entries,'all_tables_equal':before['tables'],
            'sequences_equal':len(before['sequences']),'foreign_keys_equal_no_orphans':len(before['foreign_keys']),
            'model_calls':0,'source_read_only':True,'application_recovery_not_yet_checked':True,
            'outbox_attachments_not_yet_checked':True}
        (folder/'restore-metadata.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return source_url.set(database=target),report
    finally:
        for conn in (restored,source,admin):
            if conn:await conn.close()
