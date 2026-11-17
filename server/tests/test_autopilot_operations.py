"""Windows process lifecycle acceptance with local fake modules, no DB or LLM."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which('powershell')
pytestmark = pytest.mark.skipif(os.name != 'nt' or not POWERSHELL, reason='Windows launcher acceptance')


def invoke(script, *args, env=None):
    # Windows detached children can inherit pipe handles after PowerShell exits.
    # Files let us wait for the launcher itself without waiting on its services.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        result = subprocess.run([POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
            *map(str, args)], stdout=output, stderr=errors, stdin=subprocess.DEVNULL, timeout=50, env=env)
        output.seek(0)
        errors.seek(0)
        return subprocess.CompletedProcess(result.args, result.returncode,
            output.read().decode('utf-8', errors='replace'), errors.read().decode('utf-8', errors='replace'))


def alive(pid):
    result = subprocess.run([POWERSHELL, '-NoProfile', '-Command',
        f'if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}'],
        capture_output=True, timeout=15)
    return result.returncode == 0


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture
def sandbox(tmp_path):
    deploy = tmp_path / 'deploy'
    deploy.mkdir()
    for script in (ROOT / 'deploy').glob('*.ps1'):
        shutil.copy2(script, deploy / script.name)
    server = tmp_path / 'server'
    app = server / 'app'
    app.mkdir(parents=True)
    (tmp_path / 'web').mkdir()
    for path in [server / 'bootstrap.py', server / 'migrate.py', app / '__init__.py', app / 'seed.py']:
        path.write_text('', encoding='utf-8')
    (app / 'config.py').write_text('class Settings:\n    api_token="fixture-token"\ndef get_settings():\n    return Settings()\n', encoding='utf-8')
    (app / 'project_worker.py').write_text('''import json,os,time
from pathlib import Path
Path('worker.started.json').write_text(json.dumps({'pid':os.getpid(),'cwd':str(Path.cwd()),'workspace':os.getenv('DEFAULT_WORKSPACE_SLUG'),'args':__import__('sys').argv[1:],'started':time.time()}))
if os.getenv('FIXTURE_WORKER_FAIL'): raise SystemExit(23)
while True: time.sleep(.1)
''', encoding='utf-8')
    (server / 'uvicorn.py').write_text('''import json,os,sys,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
Path('api.started.json').write_text(json.dumps({'pid':os.getpid(),'started':time.time()}))
if os.getenv('FIXTURE_API_FAIL'):
    time.sleep(1.5)
    raise SystemExit(24)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(b'{"ok":true}')
    def log_message(self,*args): pass
HTTPServer(('127.0.0.1',int(sys.argv[sys.argv.index('--port')+1])),Handler).serve_forever()
''', encoding='utf-8')
    env = {name: value for name, value in os.environ.items() if name.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
        'PROGRAMFILES', 'PROGRAMFILES(X86)', 'SYSTEMDRIVE', 'TEMP', 'TMP', 'PATHEXT'}}
    for name in ('LLM_PROVIDER', 'LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'):
        env[name] = ''
    env['DATABASE_URL'] = 'postgresql+asyncpg://disabled:disabled@127.0.0.1:1/never_connect_test'
    try:
        yield tmp_path, env
    finally:
        # Only fixture module processes from this sandbox are candidates; compare
        # actual creation time to our marker before stopping a reused PID.
        for name in ('worker.started.json', 'api.started.json'):
            path = server / name
            if not path.exists():
                continue
            marker = json.loads(path.read_text())
            pid = int(marker['pid'])
            epoch = float(marker['started'])
            code = f'''$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue
if ($p -and [Math]::Abs(($p.StartTime.ToUniversalTime()-[datetime]'1970-01-01').TotalSeconds-{epoch}) -lt 3) {{ $p.Kill() }}'''
            subprocess.run([POWERSHELL, '-NoProfile', '-Command', code], capture_output=True, timeout=15)


def launch(root, env, *extra):
    return invoke(root / 'deploy/start-personal.ps1', '-PythonPath', sys.executable, '-NoWeb', '-Port', unused_port(), *extra, env=env)


def test_personal_launcher_starts_independent_worker_and_records_truthful_state(sandbox):
    root, env = sandbox
    result = launch(root, env)
    assert result.returncode == 0, result.stderr
    state = json.loads((root / 'personal/runtime/processes.json').read_text('utf-8-sig'))
    assert state.get('worker_pid'), 'Default personal startup must launch an independent Worker'
    worker = json.loads((root / 'server/worker.started.json').read_text())
    assert worker['cwd'] == str(root / 'server')
    assert worker['workspace'] == 'personal'
    assert alive(worker['pid']) and alive(state['api_pid'])
    assert state['worker_health'] == 'process_alive'
    assert (root / 'personal/runtime/worker.out.log').exists()
    assert (root / 'personal/runtime/worker.err.log').exists()


def test_initialize_only_and_no_worker_do_not_start_worker(sandbox):
    root, env = sandbox
    initialized = launch(root, env, '-InitializeOnly')
    assert initialized.returncode == 0, initialized.stderr
    assert not (root / 'server/worker.started.json').exists()
    result = launch(root, env, '-NoWorker')
    assert result.returncode == 0, result.stderr
    assert not (root / 'server/worker.started.json').exists()


def test_failed_api_start_reaps_only_new_worker_and_api(sandbox):
    root, env = sandbox
    env['FIXTURE_API_FAIL'] = '1'
    result = launch(root, env)
    assert result.returncode != 0
    api = json.loads((root / 'server/api.started.json').read_text())
    worker_file = root / 'server/worker.started.json'
    assert worker_file.exists(), 'Worker must start independently before API readiness'
    worker = json.loads(worker_file.read_text())
    assert not alive(api['pid'])
    assert not alive(worker['pid']), 'Failed launch must not leak its newly created Worker'
    assert not (root / 'personal/runtime/processes.json').exists()


def test_failed_worker_start_reaps_new_api(sandbox):
    root, env = sandbox
    env['FIXTURE_WORKER_FAIL'] = '1'
    result = launch(root, env)
    assert result.returncode != 0
    for name in ('api.started.json', 'worker.started.json'):
        marker = root / 'server' / name
        if marker.exists():
            assert not alive(json.loads(marker.read_text())['pid']), 'Failed startup leaked a fixture process'
    assert not (root / 'personal/runtime/processes.json').exists()


def test_standalone_worker_reused_survives_api_failure_and_can_be_stopped(sandbox):
    root, env = sandbox
    start = root / 'deploy/start-project-worker.ps1'
    stop = root / 'deploy/stop-project-worker.ps1'
    assert start.exists() and stop.exists(), 'Independent maintenance commands are required'
    first = invoke(start, '-PythonPath', sys.executable, env=env)
    assert first.returncode == 0, first.stderr
    initial = json.loads((root / 'server/worker.started.json').read_text())
    env['FIXTURE_API_FAIL'] = '1'
    failed = launch(root, env)
    assert failed.returncode != 0
    assert alive(initial['pid']), 'API restart failure must preserve the existing Worker'
    again = invoke(start, '-PythonPath', sys.executable, env=env)
    assert again.returncode == 0, again.stderr
    assert json.loads((root / 'server/worker.started.json').read_text())['pid'] == initial['pid']
    stopped = invoke(stop, env=env)
    assert stopped.returncode == 0, stopped.stderr
    assert not alive(initial['pid'])


def test_worker_stop_refuses_unknown_pid(sandbox):
    root, env = sandbox
    stop = root / 'deploy/stop-project-worker.ps1'
    assert stop.exists(), 'Safe independent stop command is required'
    runtime = root / 'personal/runtime'
    runtime.mkdir(parents=True)
    (runtime / 'worker.json').write_text(json.dumps({'pid': os.getpid(), 'started_at': '2000-01-01T00:00:00Z',
        'python_path': sys.executable, 'project_dir': str(root), 'module': 'app.project_worker'}))
    result = invoke(stop, env=env)
    assert result.returncode != 0
    assert alive(os.getpid())


def test_worker_stop_rejects_matching_pid_with_wrong_module_command(sandbox):
    root, env = sandbox
    unrelated = subprocess.Popen([sys._base_executable, '-c', 'import time; time.sleep(120)'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        runtime = root / 'personal/runtime'
        runtime.mkdir(parents=True)
        setup = root / 'make-unrelated-record.ps1'
        quoted = str(root).replace("'", "''")
        setup.write_text(f". '{quoted}/deploy/personal-processes.ps1'\n"
            f"$identity = New-CareerPilotProcessIdentity (Get-Process -Id {unrelated.pid}) '{quoted}' 'app.project_worker'\n"
            f"Write-CareerPilotState '{quoted}/personal/runtime/worker.json' $identity\n", encoding='utf-8')
        created = invoke(setup, env=env)
        assert created.returncode == 0, created.stderr
        result = invoke(root / 'deploy/stop-project-worker.ps1', env=env)
        assert result.returncode != 0, 'Matching PID/time alone must not authorize stopping a non-Worker module'
        assert unrelated.poll() is None
    finally:
        if unrelated.poll() is None:
            unrelated.terminate()
        unrelated.wait(timeout=10)


def test_api_restart_reuses_the_running_worker(sandbox):
    root, env = sandbox
    first = launch(root, env)
    assert first.returncode == 0, first.stderr
    state = json.loads((root / 'personal/runtime/processes.json').read_text('utf-8-sig'))
    first_worker = json.loads((root / 'server/worker.started.json').read_text())
    stop_api = root / 'stop-fixture-api.ps1'
    quoted = str(root).replace("'", "''")
    stop_api.write_text(f". '{quoted}/deploy/personal-processes.ps1'\n"
        f"$identity = New-CareerPilotProcessIdentity (Get-Process -Id {state['api_pid']}) '{quoted}' 'uvicorn'\n"
        f"Stop-CareerPilotOwnedProcess $identity '{quoted}'\n", encoding='utf-8')
    stopped = invoke(stop_api, env=env)
    assert stopped.returncode == 0, stopped.stderr
    assert alive(first_worker['pid'])
    second = launch(root, env)
    assert second.returncode == 0, second.stderr
    latest = json.loads((root / 'personal/runtime/processes.json').read_text('utf-8-sig'))
    assert latest['worker_pid'] == state['worker_pid']
    assert latest['api_pid'] != state['api_pid']
    assert json.loads((root / 'server/worker.started.json').read_text()) == first_worker


def test_deploy_powershell_scripts_parse():
    code = "$errors=@(); Get-ChildItem -LiteralPath '" + str(ROOT / 'deploy').replace("'", "''") + "' -Filter *.ps1 | ForEach-Object { $tokens=$null; $parseErrors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$parseErrors); $errors += $parseErrors }; if ($errors.Count) { $errors | Out-String | Write-Error; exit 1 }"
    result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
