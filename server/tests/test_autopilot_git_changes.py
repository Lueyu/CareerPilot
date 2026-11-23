"""Real local Git commits; fixture source mapping/dependency knowledge, no LLM."""
import json
import shutil
import subprocess

import pytest
from sqlalchemy import func, select

from app.models import Project, ProjectAnalysis
from app.services import project_autopilot_engine as engine
from app.services import project_autopilot_sources as sources
from app.services import project_scheduler as scheduler
from app.services.project_autopilot_config import model_fingerprint

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='Local Git executable required')


class LocalGit:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir()
        self.run('init', '--initial-branch=main')
        self.run('config', 'core.autocrlf', 'false')
        hook_dir = directory / '.git/disabled-fixture-hooks'
        hook_dir.mkdir()
        self.run('config', 'core.hooksPath', str(hook_dir))
        (directory / 'src').mkdir()
        for name, value in {'lib.py': 'VALUE = 1\n', 'consumer.py': 'from lib import VALUE\n',
            'independent.py': 'INDEPENDENT = True\n', 'old_name.py': 'RENAME_ME = True\n',
            'obsolete.py': 'OBSOLETE = True\n'}.items():
            (directory / 'src' / name).write_text(value, encoding='utf-8')
        self.commits = []
        self.commit('fixture baseline')

    def run(self, *args):
        import os
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
        result = subprocess.run(['git', *args], cwd=self.directory, env=env,
            capture_output=True, timeout=15)
        assert result.returncode == 0, 'Controlled local Git command failed'
        return result.stdout.decode('utf-8').strip()

    def commit(self, message):
        self.run('add', '--all')
        self.run('-c', 'user.name=CareerPilot Fixture', '-c', 'user.email=fixture@local.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-m', message)
        revision = self.run('rev-parse', 'HEAD')
        self.commits.append(revision)
        return revision

    def change_lib(self, number):
        (self.directory / 'src/lib.py').write_text(f'VALUE = {number}\n', encoding='utf-8')
        return self.commit(f'fixture dependency revision {number}')

    def manifest(self, revision):
        entries = []
        for item in self.run('ls-tree', '-r', '-z', revision).split('\0'):
            if not item:
                continue
            identity, path = item.split('\t', 1)
            mode, kind, oid = identity.split(' ', 2)
            entries.append({'path': path, 'blob_oid': oid, 'type': kind, 'mode': mode,
                'included': True, 'category': 'source', 'size': int(self.run('cat-file', '-s', oid))})
        return {'entries': entries, 'complete': True, 'target_sha': revision, 'branch': 'main',
            'repository_id': 'local-controlled-fixture', 'tree_sha': self.run('rev-parse', revision + '^{tree}')}


@pytest.fixture
def local_git(tmp_path):
    return LocalGit(tmp_path / 'controlled-git-repo')


def write_evidence(tmp_path, name, value):
    (tmp_path / 'git-change-evidence.json').write_text(json.dumps({
        'test': name, 'verification': 'actual_local_git_objects_fixture_adapters_no_remote_git_or_model',
        **value}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def test_real_git_dependency_changes_invalidate_dependents_and_rename_delete_are_not_hidden(local_git, tmp_path):
    repo = local_git
    first = repo.commits[0]
    base = repo.manifest(first)
    context = {'workspace_id': 1, 'project_id': 1, 'mode': 'incremental',
        'model_config_fingerprint': 'explicit-dependency-map-fixture', 'project': {'id': 1}}
    modules = []
    for key, path, dependencies in [('lib', 'src/lib.py', []), ('consumer', 'src/consumer.py', ['src/lib.py']),
        ('independent', 'src/independent.py', []), ('rename', 'src/old_name.py', []), ('delete', 'src/obsolete.py', [])]:
        module = {'key': key, 'paths': [path], 'dependencies': dependencies, 'dependency_scope_complete': True,
            'coverage_status': 'COMPLETE', 'provenance': engine._provenance(context), 'fixture_semantics': True}
        module['fingerprint'] = engine._module_fingerprint(context, base, module, dependencies)
        modules.append(module)
    context['baseline'] = {'snapshot': {'manifest': base, 'modules': modules}}
    second = repo.change_lib(2)
    target = repo.manifest(second)
    target['diff'] = sources.diff_manifests(base, target)
    # This is a real ancestral relation and unchanged consumers have real blob IDs.
    repo.run('merge-base', '--is-ancestor', first, second)
    before_map = {e['path']: e['blob_oid'] for e in base['entries']}
    after_map = {e['path']: e['blob_oid'] for e in target['entries']}
    assert before_map['src/consumer.py'] == after_map['src/consumer.py']
    assert before_map['src/lib.py'] != after_map['src/lib.py']
    reusable = {m['key']: engine._reusable_module(context, target, m) is not None for m in modules}
    assert reusable == {'lib': False, 'consumer': False, 'independent': True, 'rename': True, 'delete': True}
    assert repo.run('diff', '--name-status', first, second).splitlines() == ['M\tsrc/lib.py']
    for relative in ('src/old_name.py', 'src/new_name.py', 'src/obsolete.py'):
        (repo.directory / relative).resolve().relative_to(repo.directory.resolve())
    (repo.directory / 'src/old_name.py').rename(repo.directory / 'src/new_name.py')
    (repo.directory / 'src/obsolete.py').unlink()
    third = repo.commit('fixture rename and delete')
    moved = repo.manifest(third)
    moved['diff'] = sources.diff_manifests(target, moved)
    changes = moved['diff']['changes']
    assert any(c['status'] == 'renamed' and c['previous_path'] == 'src/old_name.py' and c['path'] == 'src/new_name.py' for c in changes)
    assert any(c['status'] == 'deleted' and c['path'] == 'src/obsolete.py' for c in changes)
    # The production policy conservatively invalidates semantic reuse for a
    # topology change; it never silently treats an absent old path as unchanged.
    assert all(engine._reusable_module(context, moved, m) is None for m in modules)
    write_evidence(tmp_path, 'dependency_invalidation_and_rename_delete', {
        'commit_ids': repo.commits, 'base_tree': base['tree_sha'], 'changed_tree': target['tree_sha'],
        'real_blobs_before': before_map, 'real_blobs_after': after_map,
        'dependency_change_cache_reuse': reusable, 'rename_delete_changes': changes,
        'rename_delete_conservative_invalidation': True,
        'semantic_dependency_map_is_fixture': True, 'model_calls': 0})


@pytest.mark.asyncio
async def test_real_git_heads_schedule_one_incremental_and_coalesce_pending_updates(local_git, tmp_path, db_session, test_workspace, monkeypatch):
    repo = local_git
    config = {'provider': 'fixture', 'model': 'fixture', 'base_url': 'https://fixture.invalid', 'api_key': 'fixture'}
    pipeline, prompt = scheduler.versions()
    project = Project(workspace_id=test_workspace.id, name='Controlled local Git fixture', owner='fixture', repo='controlled',
        repo_url='https://github.com/fixture/controlled', branch='main', meta={'kind': 'github'}, generation=1)
    db_session.add(project)
    await db_session.flush()
    baseline = ProjectAnalysis(project_id=project.id, workspace_id=test_workspace.id, request_key='git-fixture-baseline',
        status='COMPLETED', report_status='VALID', coverage_status='COMPLETE', source_revision=repo.commits[0],
        pipeline_version=pipeline, prompt_bundle_version=prompt, model_config_fingerprint=model_fingerprint(config),
        generation=1, snapshot={'manifest': repo.manifest(repo.commits[0]),
            'provenance': engine._provenance({'workspace_id': test_workspace.id, 'project_id': project.id})},
        report={'fixture_semantics': True})
    db_session.add(baseline)
    await db_session.flush()
    project.latest_valid_analysis_id = baseline.id
    project.last_seen_sha = baseline.source_revision
    await db_session.commit()
    source_checks = []

    async def fixture_config(*args, **kwargs):
        return config, {}, {'budget': {'max_model_calls': 32}, 'check_interval_hours': 6}

    async def local_head(*args, **kwargs):
        revision = repo.run('rev-parse', 'HEAD')
        source_checks.append(revision)
        return {'target_sha': revision, 'branch': repo.run('branch', '--show-current')}

    monkeypatch.setattr(scheduler, 'runtime_config', fixture_config)
    monkeypatch.setattr(scheduler, 'probe_repository', local_head)
    initial = await scheduler.check_project(db_session, project.id, test_workspace.id)
    assert initial == {'status': 'UNCHANGED', 'run_id': None}
    second = repo.change_lib(2)
    changed = await scheduler.check_project(db_session, project.id, test_workspace.id)
    active = await db_session.get(ProjectAnalysis, changed['run_id'])
    assert active.mode == 'incremental' and active.target_sha == second
    repeated = await scheduler.check_project(db_session, project.id, test_workspace.id)
    assert repeated['run_id'] == active.id
    active.status = 'RUNNING'  # Explicit fixture run state; OS recovery is tested separately.
    await db_session.commit()
    third = repo.change_lib(3)
    pending_one = await scheduler.check_project(db_session, project.id, test_workspace.id)
    assert pending_one['run_id'] == active.id and project.pending_update['target_sha'] == third
    fourth = repo.change_lib(4)
    pending_latest = await scheduler.check_project(db_session, project.id, test_workspace.id)
    assert pending_latest['run_id'] == active.id and project.pending_update['target_sha'] == fourth
    count_before = await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(ProjectAnalysis.project_id == project.id))
    assert count_before == 2
    active.status = 'COMPLETED'
    active.report_status = 'VALID'
    active.source_revision = second
    active.snapshot = {'manifest': repo.manifest(second),
        'provenance': engine._provenance({'workspace_id': test_workspace.id, 'project_id': project.id})}
    active.report = {'fixture_semantics': True}
    project.latest_valid_analysis_id = active.id
    await db_session.commit()
    followup = await scheduler.check_project(db_session, project.id, test_workspace.id)
    latest = await db_session.get(ProjectAnalysis, followup['run_id'])
    assert latest.id != active.id and latest.mode == 'incremental' and latest.target_sha == fourth
    assert project.pending_update is None
    count_after = await db_session.scalar(select(func.count()).select_from(ProjectAnalysis).where(ProjectAnalysis.project_id == project.id))
    assert count_after == 3
    latest.status = 'COMPLETED'
    latest.report_status = 'VALID'
    latest.source_revision = fourth
    latest.snapshot = {'manifest': repo.manifest(fourth),
        'provenance': engine._provenance({'workspace_id': test_workspace.id, 'project_id': project.id})}
    project.latest_valid_analysis_id = latest.id
    await db_session.commit()
    unchanged_again = await scheduler.check_project(db_session, project.id, test_workspace.id)
    assert unchanged_again['status'] == 'UNCHANGED'
    write_evidence(tmp_path, 'actual_heads_incremental_and_coalescing', {'commit_ids': repo.commits,
        'head_checks': source_checks, 'unchanged_before_and_after_created_runs': 0,
        'first_change_created_incremental_runs': 1, 'multiple_new_heads_while_busy_created_runs': 0,
        'coalesced_latest_target': fourth, 'followup_incremental_runs': 1,
        'total_runs_including_baseline': count_after, 'model_calls': 0,
        'source_adapter_maps_real_local_git': True, 'run_completion_is_explicit_fixture': True})
