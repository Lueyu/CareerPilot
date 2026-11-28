from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import get_settings
from app.models import AppSetting, Workspace
from app.services.project_autopilot_config import CONSENT_VERSION, runtime_config
from app.services.secret_storage import seal_setting

REVISION = 'd' * 40


@pytest.fixture
def repository_settings(monkeypatch, secrets_key):
    from app.services import recovery_control
    monkeypatch.setattr(recovery_control, 'require_normal', AsyncMock(return_value='a' * 32))
    settings = get_settings()
    monkeypatch.setattr(settings, 'default_workspace_slug', 'personal')
    monkeypatch.setattr(settings, 'workspace_token_hashes', {'scoped': 'a' * 64, 'other': 'b' * 64})
    monkeypatch.setattr(settings, 'github_token', 'fixture-installation-github')
    monkeypatch.setattr(settings, 'gitcode_token', 'fixture-installation-gitcode')
    return {
        'github': {'token': 'fixture-owner-github'},
        'github:22': {'token': 'fixture-other-github'},
        'gitcode:22': {'token': 'fixture-other-gitcode'},
    }


class SettingsSession:
    def __init__(self, slug, rows):
        self.slug = slug
        self.rows = rows

    async def scalar(self, query):
        column = query.column_descriptions[0]['expr']
        if column is Workspace.slug:
            return self.slug
        assert column is Workspace.id
        return 11 if self.slug in self.rows.get('_tenant_boundaries', {'scoped', 'other'}) else None

    async def get(self, model, key):
        assert model is AppSetting
        value = self.rows.get(key)
        return SimpleNamespace(value=seal_setting(key, value)) if value is not None else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize('require_consent', [True, False])
async def test_scoped_runtime_credentials_default_to_anonymous(repository_settings, require_consent):
    repository_settings['project-autopilot:11'] = {'enabled': True, 'consent_version': CONSENT_VERSION}
    session = SettingsSession('scoped', repository_settings)
    _, credentials, _ = await runtime_config(session, 11, require_consent=require_consent)
    assert not any(credentials.values())


@pytest.mark.asyncio
async def test_scoped_runtime_uses_only_its_own_token(repository_settings):
    repository_settings['github:11'] = {'token': 'fixture-scoped-github'}
    repository_settings['gitcode:11'] = {'token': '   '}
    session = SettingsSession('scoped', repository_settings)
    _, credentials, _ = await runtime_config(session, 11, require_consent=False)
    assert credentials['github_token'] == 'fixture-scoped-github'
    assert not credentials['gitcode_token']


@pytest.mark.asyncio
async def test_personal_runtime_keeps_database_and_installation_fallbacks(repository_settings):
    session = SettingsSession('personal', repository_settings)
    _, credentials, _ = await runtime_config(session, 11, require_consent=False)
    assert credentials == {
        'github_token': 'fixture-owner-github',
        'gitcode_token': 'fixture-installation-gitcode',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('slug', [None, 'other-local-workspace', 'personal'])
async def test_runtime_requires_an_unscoped_personal_workspace_for_fallback(repository_settings, slug, monkeypatch):
    if slug == 'personal':
        repository_settings['_tenant_boundaries'] = {'personal'}
    session = SettingsSession(slug, repository_settings)
    _, credentials, _ = await runtime_config(session, 11, require_consent=False)
    assert not any(credentials.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['github', 'gitcode'])
@pytest.mark.parametrize('configured', [False, True])
async def test_worker_repository_requests_use_workspace_credentials(repository_settings, monkeypatch, provider, configured):
    from app import project_worker as worker

    own_token = f'fixture-scoped-{provider}' if configured else None
    if configured:
        repository_settings[f'{provider}:11'] = {'token': own_token}
    claim = SimpleNamespace(workspace_id=11, project_id=2, run_id=3, step_id=4, stage='resolve', payload={})
    run = SimpleNamespace(result_meta={'source_transfer_consent': CONSENT_VERSION}, model_config_fingerprint='', budget={})
    project = {'kind': provider, 'owner': 'example', 'repo': 'fixture'}
    records = {
        (worker.ProjectAnalysis, 3): run,
        (worker.Project, 2): SimpleNamespace(id=2),
        (worker.AnalysisStep, 4): SimpleNamespace(),
    }

    class Session(SettingsSession):
        async def get(self, model, key):
            if model is AppSetting:
                return await super().get(model, key)
            return records[model, key]

    completed = AsyncMock()
    failed = AsyncMock()
    monkeypatch.setattr(worker.jobs, 'heartbeat', AsyncMock(return_value=True))
    monkeypatch.setattr(worker.jobs, 'complete_step', completed)
    monkeypatch.setattr(worker.jobs, 'fail_step', failed)
    monkeypatch.setattr(worker, 'context_for', AsyncMock(return_value=({'project': project}, {})))
    monkeypatch.setattr(worker, 'assert_compatible', lambda *_: None)
    requests = []

    async def send(transport, request):
        requests.append((request.headers.get('Authorization'), request.headers.get('PRIVATE-TOKEN')))
        if request.url.path.endswith('/commits/main'):
            return httpx.Response(200, json={'sha': REVISION})
        return httpx.Response(200, json={'id': 10, 'default_branch': 'main'})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    await worker.execute_claim(claim, 'fixture-worker', factory=lambda: Session('scoped', repository_settings))
    expected = (f'Bearer {own_token}', None) if provider == 'github' and own_token else (None, own_token)
    assert requests == [expected, expected]
    failed.assert_not_awaited()
    assert completed.await_args.args[2]['target_sha'] == REVISION


@pytest.mark.asyncio
@pytest.mark.parametrize('slug, own_token, expected', [
    ('scoped', None, None),
    ('scoped', 'fixture-workspace-github', 'Bearer fixture-workspace-github'),
    ('personal', None, 'Bearer fixture-owner-github'),
])
async def test_learning_preview_uses_workspace_repository_access(repository_settings, monkeypatch, slug, own_token, expected):
    from app.api.v1 import learning_projects as api

    if own_token:
        repository_settings['github:11'] = {'token': own_token}
    contract = {'project_id': 2, 'baseline_revision': 'a' * 40, 'allowed_paths': ['app.py'],
                'baseline_files': [{'path': 'app.py', 'content': 'print(1)\n'}]}
    activity = SimpleNamespace(id=3, goal_id=2, version=1, snapshot={'project_contract': contract})
    project = SimpleNamespace(owner='example', repo='fixture', meta={})
    session = SettingsSession(slug, repository_settings)
    session.scalar = AsyncMock(side_effect=[project, slug, 11 if slug == 'scoped' else None, None])
    session.commit = AsyncMock()
    snapshots = []
    session.add = snapshots.append
    session.flush = AsyncMock()
    monkeypatch.setattr(api, '_activity', AsyncMock(return_value=activity))
    requests = []

    async def send(transport, request):
        requests.append(request.headers.get('Authorization'))
        if '/commits/' in request.url.path:
            return httpx.Response(200, json={'sha': REVISION})
        if '/git/trees/' in request.url.path:
            return httpx.Response(200, json={'tree': [{'path': 'app.py', 'type': 'blob'}]})
        assert request.url.path.endswith('/contents/app.py')
        return httpx.Response(200, text='print(2)\n')

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    result = await api.preview_project(3, api.PreviewIn(source_kind='github_commit', base_revision='a' * 40,
        commit_sha=REVISION), ws=Workspace(id=11, slug=slug), session=session)
    assert result['remote_verified'] is True
    assert len(snapshots) == 1 and snapshots[0].workspace_id == 11
    assert requests == [expected, expected, expected]


@pytest.mark.asyncio
async def test_asset_sync_reads_anonymously_for_scoped_workspace(repository_settings, monkeypatch):
    from app.api.v1 import career_assets as api

    session = SettingsSession('scoped', repository_settings)
    session.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None)))
    session.commit = AsyncMock()
    created = []
    session.add = created.append
    monkeypatch.setattr(api, 'start_trace', AsyncMock(return_value=SimpleNamespace(started_at=None, short_code='TRACE')))
    monkeypatch.setattr(api, 'finish_trace', AsyncMock())
    monkeypatch.setattr(api, 'add_span', lambda *args, **kwargs: None)
    monkeypatch.setattr(get_settings(), 'portal_target_repo', 'example/fixture')
    requests = []

    async def send(transport, request):
        requests.append(request.headers.get('Authorization'))
        if request.url.path.endswith('/contents/docs/fixture.md'):
            return httpx.Response(200, text='fixture asset')
        if '/git/trees/' in request.url.path:
            return httpx.Response(200, json={'tree': [{'path': 'docs/fixture.md', 'type': 'blob'}]})
        return httpx.Response(200, json={'default_branch': 'main'})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    result = await api.sync_assets(ws=Workspace(id=11, slug='scoped'), session=session)
    assert result['synced'] == ['docs/fixture.md']
    assert len(created) == 1 and created[0].workspace_id == 11
    assert requests == [None, None, None]


@pytest.mark.asyncio
async def test_scoped_build_refresh_does_not_fall_back_to_owner(repository_settings, monkeypatch):
    from app.api.v1 import public_content as api
    from app.models.public_content import ContentPublication

    publication = ContentPublication(target_repo='example/fixture', head_sha=REVISION)
    session = SettingsSession('scoped', repository_settings)
    session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: publication))
    session.commit = AsyncMock()
    requests = []

    async def send(transport, request):
        requests.append(request.headers.get('Authorization'))
        assert request.url.path.endswith('/actions/runs')
        return httpx.Response(200, json={'workflow_runs': [{'status': 'completed'}]})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', send)
    result = await api.refresh_build(1, ws=Workspace(id=11, slug='scoped'), session=session)
    assert result['build_status'] == 'COMPLETED'
    assert requests == [None]
