"""Real age and PostgreSQL; credentials are synthetic, source DB is runner-created."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
RUN = Path(os.environ.get('F6_EVIDENCE_DIR', ROOT / '.agent-runs/f6-tests'))
PGBIN = Path(os.environ.get('F6_PG_BIN', 'C:/Users/97956/scoop/apps/postgresql/current/bin'))


@pytest_asyncio.fixture
async def owned_setting_rows(db_session):
    from sqlalchemy import delete, select

    from app.models import AppSetting
    before = set(await db_session.scalars(select(AppSetting.key)))
    yield
    await db_session.rollback()
    await db_session.execute(delete(AppSetting).where(AppSetting.key.not_in(before)))
    await db_session.commit()


def command(args, env=None, ok=True):
    RUN.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    result = subprocess.run([str(a) for a in args], env=env, capture_output=True, text=True, encoding='utf-8')
    receipt = {'command': [str(a) for a in args], 'exit_code': result.returncode,
               'elapsed_seconds': time.monotonic() - start, 'stdout': result.stdout, 'stderr': result.stderr}
    with (RUN / 'commands.jsonl').open('a', encoding='utf-8') as file:
        file.write(json.dumps(receipt, ensure_ascii=False) + '\n')
    if ok:
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
    return result


def powershell(script, *args, env=None, ok=True):
    return command(['pwsh', '-NoProfile', '-File', ROOT / 'deploy' / script, *args], env, ok)


def test_backup_requires_external_recipient_before_dump(tmp_path):
    env = {key: value for key, value in os.environ.items() if key != 'CAREERPILOT_BACKUP_RECIPIENTS'}
    result = powershell('backup.ps1', '-Database', 'fixture_test', '-OutDir', tmp_path, env=env, ok=False)
    assert 'external age recipients file is required before pg_dump' in result.stderr
    assert not list(tmp_path.rglob('*.dump'))


def test_retention_empty_unowned_directory_refuses(tmp_path):
    survivor = tmp_path / 'important.txt'
    survivor.write_text('user-owned', encoding='utf-8')
    result = powershell('backup-retention.ps1', '-OutDir', tmp_path, '-Apply', ok=False)
    assert 'No owned backup directory marker' in result.stderr
    assert survivor.read_text() == 'user-owned'


@pytest.mark.asyncio
async def test_actual_encrypted_drill_and_controlled_standby(db_session, test_workspace, owned_setting_rows, tmp_path, monkeypatch):
    from app.models import AppSetting, Evidence, Exam, ExamAnswer, ExamQuestion, Question
    from app.models.assessment import AnswerRevision
    from app.models.private_model import PrivateModelCall
    from app.services.secret_storage import SecretStorageError, open_setting, seal_setting
    from validate_recovery import validate

    if not os.environ.get('AGE_BIN') or not os.environ.get('AGE_KEYGEN_BIN'):
        pytest.fail('Explicit verified real age tools required', pytrace=False)
    key = Fernet.generate_key().decode()
    monkeypatch.setenv('CAREERPILOT_SECRETS_KEY', key)
    secret = 'f6-synthetic-original-secret-9911'
    env = os.environ.copy()
    url = make_url(os.environ['CAREERPILOT_TEST_DATABASE_URL'])
    assert url.database.endswith('_test')
    env.update(PGHOST=url.host, PGPORT=str(url.port or 5432), PGUSER=url.username, PGPASSWORD=url.password or '')
    for name in ['LLM_API_KEY', 'GITHUB_TOKEN', 'GITCODE_TOKEN', 'SEED_MODEL_KEY']:
        env[name] = ''
    identity = tmp_path / 'identity.txt'
    wrong = tmp_path / 'wrong-identity.txt'
    recipients = tmp_path / 'recipients.txt'
    command([env['AGE_KEYGEN_BIN'], '-o', identity], env)
    command([env['AGE_KEYGEN_BIN'], '-o', wrong], env)
    public = command([env['AGE_KEYGEN_BIN'], '-y', identity], env).stdout.strip()
    recipients.write_text(public + '\n', encoding='ascii')
    env['CAREERPILOT_BACKUP_RECIPIENTS'] = str(recipients)
    env['CAREERPILOT_BACKUP_IDENTITY'] = str(identity)
    question = Question(short_code='F6-' + uuid4().hex[:12], workspace_id=test_workspace.id,
                        title='Synthetic DR question', qtype='open', topic='harness', source_type='USER_PRIVATE')
    db_session.add(question)
    exam = Exam(workspace_id=test_workspace.id, title='F6 original', kind='diagnosis', status='COMPLETED')
    db_session.add(exam)
    await db_session.flush()
    eq = ExamQuestion(exam_id=exam.id, question_id=question.id, order_no=1,
                      frozen_snapshot={'original': 'immutable synthetic rubric'})
    db_session.add(eq)
    await db_session.flush()
    answer = ExamAnswer(exam_question_id=eq.id, text_answer='原答保持逐字一致 F6 original answer',
                        transcript='Synthetic original transcript', points=3, verdict='PARTIAL', revision=1)
    db_session.add(answer)
    from app.models.study_chat import StudySession
    from app.models.study_notes import StudyNote, StudyNoteJob
    study = StudySession(workspace_id=test_workspace.id, title='F6 original pending work')
    db_session.add(study)
    await db_session.flush()
    note = StudyNote(workspace_id=test_workspace.id, origin_session_id=study.id, title='F6 preserved note')
    db_session.add(note)
    await db_session.flush()
    note_job = StudyNoteJob(workspace_id=test_workspace.id, session_id=study.id, note_id=note.id,
                            request_id=uuid4().hex, snapshot=[], status='RUNNING', generation=7,
                            lease_until=datetime.now(timezone.utc) - timedelta(hours=1))
    db_session.add(note_job)
    unknown_id = str(uuid4())
    db_session.add(PrivateModelCall(id=unknown_id, workspace_id=test_workspace.id, operation='synthetic unknown spend',
        day=datetime.now().date(), provider='synthetic', endpoint='https://f6.invalid', model='fixture',
        input_price_nano_per_million=1, output_price_nano_per_million=1, daily_limit_nano_cny=10000,
        input_ceiling=10, max_tokens=10, reserved_nano_cny=123, charged_nano_cny=123,
        actual_nano_cny=None, state='UNKNOWN'))
    db_session.add(AnswerRevision(exam_question_id=eq.id, revision=1,
                                 payload={'original_answer': 'immutable revision'}, content_hash='c' * 64))
    db_session.add(Evidence(short_code='EV-F6-' + uuid4().hex[:12], workspace_id=test_workspace.id,
                           kind='EXAM_ANSWER', title='Synthetic original evidence', source_ref=str(eq.id),
                           grade='HIGH', support=0.5, meta=[{'synthetic': True}]))
    for setting in ['llm', f'llm:{test_workspace.id}', 'github', f'github:{test_workspace.id}', f'gitcode:{test_workspace.id}']:
        db_session.add(AppSetting(key=setting, value=seal_setting(setting, {'api_key': secret} if setting.startswith('llm') else {'token': secret})))
    await db_session.commit()
    source = await asyncpg.connect(url.render_as_string(hide_password=False).replace('+asyncpg', ''))
    before = await validate(source)
    await source.close()
    common = ['-HostName', url.host, '-Port', str(url.port or 5432), '-Username', url.username, '-PgBin', PGBIN]
    storage = tmp_path / 'backups'
    start = time.monotonic()
    result = powershell('backup.ps1', '-Database', url.database, '-OutDir', storage, *common, env=env)
    bundle = Path(result.stdout.strip().splitlines()[-1])
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8-sig'))
    (RUN / 'actual-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    assert manifest['tools']['age'] == 'v1.3.2'
    assert manifest['files']['database.dump.age']['sha256'].lower() == hashlib.sha256((bundle / 'database.dump.age').read_bytes()).hexdigest()
    assert secret.encode() not in (bundle / 'database.dump.age').read_bytes()
    assert not list(storage.rglob('*.dump')) and not list(storage.glob('.partial-*'))
    target = 'f6_drill_' + uuid4().hex[:12] + '_test'
    receipt = RUN / ('drill-' + target + '.json')
    powershell('restore.ps1', '-BackupFile', bundle, '-TargetDatabase', target, '-ReceiptFile', receipt,
               '-ConsumersIsolated', '-PythonBin', sys.executable, *common, env=env)
    restored = json.loads(receipt.read_text(encoding='utf-8-sig'))
    assert restored['status'] == 'VALIDATED_QUARANTINED'
    assert restored['validation']['business_fingerprints'] == before['business_fingerprints']
    connection = await asyncpg.connect(url.set(database=target).render_as_string(hide_password=False).replace('+asyncpg', ''))
    raw = await connection.fetchval("SELECT value::text FROM app_settings WHERE key='llm'")
    assert secret not in raw and open_setting('llm', json.loads(raw))['api_key'] == secret
    original = await connection.fetchrow('SELECT id,text_answer,exam_question_id FROM exam_answers WHERE id=$1', answer.id)
    frozen_job = dict(await connection.fetchrow('SELECT * FROM study_note_jobs WHERE id=$1', note_job.id))
    assert original['text_answer'] == answer.text_answer and original['exam_question_id'] == eq.id
    unknown = await connection.fetchrow('SELECT state,charged_nano_cny,actual_nano_cny FROM private_model_calls WHERE id=$1', unknown_id)
    assert dict(unknown) == {'state': 'UNKNOWN', 'charged_nano_cny': 123, 'actual_nano_cny': None}
    assert await connection.fetchval('SELECT count(*) FROM recovery_control WHERE origin=\'RESTORE\' AND access_state=\'QUARANTINED\' AND paid_state=\'BLOCKED\'') == 1
    await connection.close()
    # Independent process reads durable state; no HTTP provider or SMTP is contacted.
    child_env = {**env, 'DATABASE_URL': url.set(database=target).render_as_string(hide_password=False)}
    worker = command([sys.executable, '-m', 'app.project_worker', '--once'], child_env)
    assert 'RECOVERY_QUARANTINED' in worker.stderr
    child = command([sys.executable, ROOT / 'server/tests/recovery_probe.py'], child_env)
    assert 'all boundaries refused' in child.stdout
    check = await asyncpg.connect(url.set(database=target).render_as_string(hide_password=False).replace('+asyncpg', ''))
    assert dict(await check.fetchrow('SELECT * FROM study_note_jobs WHERE id=$1', note_job.id)) == frozen_job
    assert await check.fetchval('SELECT count(*) FROM project_worker_heartbeats') == 0
    await check.close()
    (RUN / 'worker-quarantine.json').write_text(json.dumps({'target': target, 'entrypoint': 'python -m app.project_worker --once',
        'exit_code': worker.returncode, 'note_job_id': note_job.id, 'original_status': 'RUNNING',
        'generation': 7, 'expired_lease_unchanged': True, 'heartbeat_rows': 0,
        'direct_scheduler_reconcile_claim_paths': 'refused by recovery_probe', 'providers_called': 0}, indent=2))
    # Client acknowledgement can fail after a real successful COMMIT. Keep target and reconcile read-only.
    shim = tmp_path / 'ack-tools'
    shim.mkdir()
    (shim / 'psql.cmd').write_text(f'@echo off\n"{PGBIN / "psql.exe"}" %*\nif errorlevel 1 exit /b %errorlevel%\necho %* | findstr /C:"--single-transaction" >nul\nif not errorlevel 1 exit /b 31\nexit /b 0\n')
    ack_env = {**env, 'PATH': str(shim) + os.pathsep + env['PATH']}
    ack_target = 'f6_ack_' + uuid4().hex[:12] + '_test'
    ack_receipt = RUN / 'lost-ack.json'
    powershell('restore.ps1', '-BackupFile', bundle, '-TargetDatabase', ack_target, '-ReceiptFile', ack_receipt,
               '-ConsumersIsolated', '-PythonBin', sys.executable, *common[:-2], env=ack_env, ok=False)
    lost = json.loads(ack_receipt.read_text(encoding='utf-8-sig'))
    assert lost['target_created'] and lost['status'].startswith('UNKNOWN_')
    powershell('restore.ps1', '-Mode', 'Inspect', '-BackupFile', bundle, '-TargetDatabase', ack_target,
               '-ReceiptFile', RUN / 'lost-ack-inspect.json', *common, env=env)
    inspect = json.loads((RUN / 'lost-ack-inspect.json').read_text(encoding='utf-8-sig'))
    assert json.loads(inspect['control'])['access_state'] == 'QUARANTINED'
    monkeypatch.setenv('CAREERPILOT_SECRETS_KEY', Fernet.generate_key().decode())
    with pytest.raises(SecretStorageError):
        open_setting('llm', json.loads(raw))
    monkeypatch.delenv('CAREERPILOT_SECRETS_KEY')
    with pytest.raises(SecretStorageError):
        open_setting('llm', json.loads(raw))
    monkeypatch.setenv('CAREERPILOT_SECRETS_KEY', key)
    # Existing target, wrong key and altered authenticated metadata create no new DB.
    powershell('restore.ps1', '-BackupFile', bundle, '-TargetDatabase', target, '-ReceiptFile', RUN / 'existing.json',
               '-ConsumersIsolated', '-PythonBin', sys.executable, *common, env=env, ok=False)
    for fault in ['wrong-age', 'corrupt', 'metadata', 'wrong-g5', 'invalid-dump']:
        altered = tmp_path / fault / bundle.name
        altered.parent.mkdir()
        shutil.copytree(bundle, altered)
        fault_env = env.copy()
        if fault == 'wrong-age':
            fault_env['CAREERPILOT_BACKUP_IDENTITY'] = str(wrong)
        elif fault == 'wrong-g5':
            fault_env['CAREERPILOT_SECRETS_KEY'] = Fernet.generate_key().decode()
        elif fault == 'corrupt':
            with (altered / 'database.dump.age').open('ab') as file:
                file.write(b'corrupt')
        elif fault == 'metadata':
            altered_manifest = dict(manifest, source_database='forged_test')
            (altered / 'manifest.json').write_text(json.dumps(altered_manifest))
        else:
            raw_meta = tmp_path / 'original-meta.json'
            command([env['AGE_BIN'], '-d', '-i', identity, '-o', raw_meta, bundle / 'metadata.age'], env)
            inner = json.loads(raw_meta.read_text(encoding='utf-8-sig'))
            invalid = tmp_path / 'invalid.dump'
            invalid.write_bytes(b'not a PostgreSQL custom archive')
            cipher = altered / 'database.dump.age'
            cipher.unlink()
            command([env['AGE_BIN'], '-R', recipients, '-o', cipher, invalid], env)
            inner['dump_sha256'] = hashlib.sha256(invalid.read_bytes()).hexdigest()
            inner['cipher_sha256'] = hashlib.sha256(cipher.read_bytes()).hexdigest()
            raw_meta.write_text(json.dumps(inner), encoding='utf-8')
            (altered / 'metadata.age').unlink()
            command([env['AGE_BIN'], '-R', recipients, '-o', altered / 'metadata.age', raw_meta], env)
            invalid_manifest = json.loads((altered / 'manifest.json').read_text(encoding='utf-8-sig'))
            for name in ['database.dump.age', 'metadata.age']:
                content = (altered / name).read_bytes()
                invalid_manifest['files'][name] = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
            (altered / 'manifest.json').write_text(json.dumps(invalid_manifest))
        name = 'f6_fault_' + uuid4().hex[:12] + '_test'
        fault_receipt = RUN / (fault + '.json')
        powershell('restore.ps1', '-BackupFile', altered, '-TargetDatabase', name, '-ReceiptFile', fault_receipt,
                   '-ConsumersIsolated', '-PythonBin', sys.executable, *common, env=fault_env, ok=False)
        observed = json.loads(fault_receipt.read_text(encoding='utf-8-sig'))
        assert observed['target_created'] == (fault == 'wrong-g5')
        assert observed['raw_temp_cleaned']
    # Reviewable plan and current-DB safety backup, exercised only with this synthetic source.
    standby = 'f6_standby_' + uuid4().hex[:12] + '_test'
    plan = RUN / 'standby-plan.json'
    plan_receipt = RUN / 'plan.json'
    safety = tmp_path / 'safety'
    plan_args = ['-BackupFile', bundle, '-TargetDatabase', standby, '-CurrentDatabase', url.database,
                 '-SafetyBackupDir', safety, '-PlanFile', plan, *common]
    powershell('restore.ps1', '-Mode', 'Plan', '-ReceiptFile', plan_receipt, *plan_args, env=env)
    approval = json.loads(plan_receipt.read_text(encoding='utf-8-sig'))['confirmation']
    powershell('restore.ps1', '-Mode', 'Standby', '-ReceiptFile', RUN / 'standby-refused.json',
               '-ConfirmPlan', 'WRONG', '-ConsumersIsolated', '-PythonBin', sys.executable, *plan_args, env=env, ok=False)
    powershell('restore.ps1', '-Mode', 'Standby', '-ReceiptFile', RUN / 'safety-backup-refused.json',
               '-ConfirmPlan', approval, '-ConsumersIsolated', '-RecipientsFile', tmp_path / 'missing-recipients',
               '-PythonBin', sys.executable, *plan_args, env=env, ok=False)
    assert not json.loads((RUN / 'safety-backup-refused.json').read_text(encoding='utf-8-sig'))['target_created']
    powershell('restore.ps1', '-Mode', 'Standby', '-ReceiptFile', RUN / 'standby.json',
               '-ConfirmPlan', approval, '-ConsumersIsolated', '-PythonBin', sys.executable, *plan_args, env=env)
    safety_manifest = json.loads(next(safety.glob('cpbackup-*/manifest.json')).read_text(encoding='utf-8-sig'))
    assert safety_manifest['pinned']
    # Actual backup plus controlled upload failure must skip pruning even with ApplyRetention.
    powershell('backup.ps1', '-Database', url.database, '-OutDir', storage, *common, env=env)
    before_failure = {path.name for path in storage.glob('cpbackup-*')}
    tools = tmp_path / 'failed-remote-tools'
    tools.mkdir()
    (tools / 'ssh.cmd').write_text('@exit /b 0\n')
    signal = tmp_path / 'upload-started'
    release = tmp_path / 'release-upload'
    (tools / 'rsync.cmd').write_text(f'@echo pending>"{signal}"\n@pwsh -NoProfile -Command "while (-not (Test-Path -LiteralPath $env:F6_RELEASE_FILE)) {{ Start-Sleep -Milliseconds 100 }}; exit 12"\n')
    ssh_identity = tmp_path / 'synthetic-ssh-key'
    known_hosts = tmp_path / 'synthetic-known-hosts'
    ssh_identity.write_text('synthetic only')
    known_hosts.write_text('synthetic only')
    offsite = tmp_path / 'offsite-config.json'
    offsite.write_text(json.dumps({'adapter': 'ssh-rsync', 'target': 'backup@f6.invalid:/srv/backup/',
                                 'known_hosts': str(known_hosts), 'identity_file': str(ssh_identity)}))
    fail_env = {**env, 'PATH': str(tools) + os.pathsep + env['PATH'], 'F6_RELEASE_FILE': str(release)}
    uploading_args = ['pwsh', '-NoProfile', '-File', ROOT / 'deploy/backup.ps1', '-Database', url.database,
                      '-OutDir', storage, '-OffsiteConfig', offsite, '-ApplyRetention', *common]
    uploading = subprocess.Popen([str(a) for a in uploading_args], env=fail_env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8')
    try:
        deadline = time.monotonic() + 30
        while not signal.exists() and uploading.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert signal.exists() and uploading.poll() is None
        pending = {path.name for path in storage.glob('cpbackup-*')} - before_failure
        assert len(pending) == 1
        failed_bundle = storage / next(iter(pending))
        assert json.loads((failed_bundle / 'offsite-receipt.json').read_text(encoding='utf-8-sig'))['status'] == 'PENDING'
        # A newer completed bundle makes the uploading one eligible by age. Pending must still pin it.
        powershell('backup.ps1', '-Database', url.database, '-OutDir', storage, *common, env=env)
        powershell('backup-retention.ps1', '-OutDir', storage, '-Apply')
        assert (failed_bundle / 'database.dump.age').exists() and uploading.poll() is None
        during_upload = {path.name for path in storage.glob('cpbackup-*')}
    finally:
        release.write_text('release controlled transport')
        stdout, stderr = uploading.communicate(timeout=30)
        with (RUN / 'commands.jsonl').open('a', encoding='utf-8') as file:
            file.write(json.dumps({'command': [str(a) for a in uploading_args], 'exit_code': uploading.returncode,
                                   'stdout': stdout, 'stderr': stderr, 'controlled_concurrent_upload': True}) + '\n')
    assert uploading.returncode != 0 and 'Offsite transfer failed/unknown' in stderr
    after_failure = {path.name for path in storage.glob('cpbackup-*')}
    assert during_upload == after_failure
    assert json.loads((failed_bundle / 'offsite-receipt.json').read_text(encoding='utf-8-sig'))['prune_suppressed']
    (RUN / 'actual-drill-summary.json').write_text(json.dumps({'source': url.database, 'target': target, 'standby': standby,
        'server_and_client': manifest['server_version'], 'tools': manifest['tools'], 'fixture': before,
        'backup_bytes': manifest['files']['database.dump.age']['bytes'],
        'drill_rto_seconds': restored['elapsed_seconds'], 'full_test_seconds': time.monotonic() - start,
        'actual_offsite': 'NOT_CONFIGURED', 'actual_provider_calls': 0}, ensure_ascii=False, indent=2), encoding='utf-8')


def test_retention_743_union_pins_containment_and_incomplete(tmp_path):
    from datetime import datetime, timedelta, timezone
    storage = tmp_path / 'owned'
    storage.mkdir()
    owner = uuid4().hex
    (storage / '.careerpilot-backups.json').write_text(json.dumps({'owner': 'careerpilot-encrypted-backup-v1', 'owner_id': owner}))
    items = []
    for index in range(125):
        identifier = uuid4().hex
        bundle = storage / ('cpbackup-' + identifier)
        bundle.mkdir()
        created = datetime(2026, 11, 28, 12, tzinfo=timezone.utc) - timedelta(days=index)
        manifest = {'owner': 'careerpilot-encrypted-backup-v1', 'owner_id': owner, 'version': 1,
                    'backup_id': identifier, 'created_at': created.isoformat(), 'files': {}, 'pinned': index == 124}
        for name in ['database.dump.age', 'metadata.age']:
            content = ('hash-verification-fixture-' + name + str(index)).encode()
            (bundle / name).write_bytes(content)
            manifest['files'][name] = {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        (bundle / 'manifest.json').write_text(json.dumps(manifest))
        (bundle / 'offsite-receipt.json').write_text(json.dumps({'version': 1, 'status': 'NOT_CONFIGURED'}))
        items.append((bundle, created))
    sentinel = storage / 'personal-not-a-backup.txt'
    sentinel.write_text('must survive')
    incomplete = storage / ('cpbackup-' + uuid4().hex)
    incomplete.mkdir()
    (incomplete / 'raw-looking-but-user-owned.txt').write_text('do not delete incomplete')
    expected = {items[0][0], items[-1][0]}
    # Crash/missing/malformed/future receipt states fail closed, even outside all 7/4/3 buckets.
    for index, state in [(80, {'version': 1, 'status': 'PENDING'}), (81, None),
                         (82, 'broken-json'), (83, {'version': 999, 'status': 'confirmed_sha256'}),
                         (84, {'version': 1, 'status': 'unknown-future-state'})]:
        receipt = items[index][0] / 'offsite-receipt.json'
        if state is None:
            receipt.unlink()
        elif isinstance(state, str):
            receipt.write_text(state)
        else:
            receipt.write_text(json.dumps(state))
        expected.add(items[index][0])
    for limit, bucket in [(7, lambda d: d.date()), (4, lambda d: d.isocalendar()[:2]), (3, lambda d: (d.year, d.month))]:
        seen = set()
        for path, stamp in items:
            group = bucket(stamp)
            if group in seen:
                continue
            if len(seen) >= limit:
                break
            seen.add(group)
            expected.add(path)
    result = powershell('backup-retention.ps1', '-OutDir', storage)
    # Warnings precede JSON and do not masquerade as deletion success.
    plan = json.loads(result.stdout[result.stdout.index('{'):])
    assert set(map(Path, plan['kept'])) == expected
    assert all(path.exists() for path, _ in items)
    applied = powershell('backup-retention.ps1', '-OutDir', storage, '-Apply')
    output = json.loads(applied.stdout[applied.stdout.index('{'):])
    assert len(output['deleted']) == len(items) - len(expected)
    assert {path for path, _ in items if path.exists()} == expected
    assert incomplete.exists() and sentinel.read_text() == 'must survive'
    powershell('backup-retention.ps1', '-OutDir', Path(storage.anchor), '-Apply', ok=False)


def test_offsite_failure_is_explicit_and_local_bundle_survives(tmp_path):
    # Controlled transport executables. No DNS, SSH server, credentials or actual remote is used.
    fake = tmp_path / 'fake-tools'
    fake.mkdir()
    (fake / 'ssh.cmd').write_text('@exit /b 0\n')
    (fake / 'rsync.cmd').write_text('@exit /b 12\n')
    known = tmp_path / 'known_hosts'
    identity = tmp_path / 'ssh_synthetic_identity'
    known.write_text('synthetic-only')
    identity.write_text('synthetic-only')
    config = tmp_path / 'remote.json'
    config.write_text(json.dumps({'adapter': 'ssh-rsync', 'target': 'backup@f6.invalid:/srv/backup/',
                                  'known_hosts': str(known), 'identity_file': str(identity)}))
    bundle = tmp_path / ('cpbackup-' + uuid4().hex)
    bundle.mkdir()
    for name in ['database.dump.age', 'metadata.age', 'manifest.json']:
        (bundle / name).write_text('synthetic-local-survivor')
    env = {**os.environ, 'PATH': str(fake) + os.pathsep + os.environ['PATH']}
    # Paths generated by pytest contain no shell quoting characters.
    script = f". '{ROOT / 'deploy/backup-common.ps1'}'; Copy-BackupOffsite '{bundle}' '{config}'"
    result = command(['pwsh', '-NoProfile', '-Command', script], env, ok=False)
    assert 'Offsite transfer failed/unknown' in result.stderr
    assert all((bundle / name).exists() for name in ['database.dump.age', 'metadata.age', 'manifest.json'])
    (RUN / 'offsite-simulation.json').write_text(json.dumps({'transport': 'mock ssh/rsync executables',
        'observed_exit': result.returncode, 'local_ciphertext_preserved': True, 'actual_offsite': 'NOT_CONFIGURED',
        'remote_verification': 'NOT_PERFORMED'}, indent=2))
