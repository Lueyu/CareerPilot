"""Checkpoint, provenance and configuration contracts; no real provider or database calls."""
import importlib
import importlib.util
import json
import re
from copy import deepcopy

import httpx
import pytest

from app.i18n import message

SHA = 'a' * 40


def engine():
    name = 'app.services.project_autopilot_engine'
    assert importlib.util.find_spec(name), 'Autopilot requires a resumable stage graph'
    return importlib.import_module(name)


def context():
    return {'workspace_id': 1, 'project_id': 2, 'project': {'id': 2, 'name': 'Fixture', 'kind': 'github', 'owner': 'fixture', 'repo': 'example', 'branch': 'main'},
            'mode': 'full', 'model_config_fingerprint': 'model-fixture', 'budget': {'input_chars': 40000}}


def runtime(api):
    return api.RuntimeContext(1, 2, {'provider': 'fixture', 'model': 'deepseek-v4-flash', 'base_url': 'https://fixture.invalid', 'api_key': 'private-fixture'}, {})


async def fixture_pipeline(monkeypatch, count=24):
    api = engine()
    calls = []
    async def probe(project, credentials):
        return {'target_sha': SHA, 'tree_sha': 'b' * 40, 'repository_id': 'fixture:1', 'branch': 'main', 'provider': 'github', 'parents': []}
    async def manifest(project, snapshot, cursor, credentials):
        return {'entries': [{'path': f'src/f{i}.py', 'blob_oid': f'{i:040x}', 'type': 'blob', 'mode': '100644', 'size': 40,
                             'included': True, 'category': 'source', 'reason': 'fixture'} for i in range(count)], 'next_cursor': None, 'complete': True, 'provider_verified': True}
    async def read(project, snapshot, entry, credentials, **kwargs):
        return {'path': entry['path'], 'blob_oid': entry['blob_oid'], 'source_revision': SHA, 'content': 'def run():\n    return 1\n',
                'line_count': 2, 'truncated': False, 'content_sha256': 'fixture', 'category': 'source', 'download_bytes': 24, 'source_bytes': 24}
    async def chat(self, cfg, system, user, **kwargs):
        assert 'reasoning_effort' not in kwargs, 'Never silently lower configured reasoning'
        value = json.loads(user)
        calls.append(value)
        task = value['task']
        if task == 'plan_modules':
            response = {'modules': [{'name': 'Core', 'purpose': 'Read the supplied implementation',
                         'paths': [e['path'] for e in value['entries']]}]}
        elif task == 'analyze_module':
            source = value['sources'][0]
            citation = {'source_id': source['source_id'], 'start_line': source['start_line'], 'end_line': source['start_line']}
            response = {'claims': [{'id': 'C1', 'title': 'Entry', 'text': 'The file defines a function.', 'kind': 'CODE_OBSERVED', 'citations': [citation]}],
                        'issues': [], 'dependencies': [], 'dependency_scope_complete': True, 'unknowns': [], 'read_requests': []}
        elif task == 'verify_cross_module':
            response = {'observations': [{'text': 'The listed modules expose code entry points.', 'claim_ids': [value['claims'][0]['id']]}], 'conflicts': [], 'unknowns': []}
        elif task == 'explain_project_section':
            response = {'paragraphs': [{'text': '项目提供源码中的处理入口，具体行为见引用。', 'claim_ids': [value['claims'][0]['id']]}], 'unknowns': []}
        elif task == 'extract_memory_candidates':
            response = {'candidates': [{'fact_key': 'entry', 'kind': 'architecture', 'content': '项目包含代码入口。', 'claim_ids': [value['claims'][0]['id']], 'fact_type': 'CODE_OBSERVED'}]}
        elif task == 'analyze_change_impact':
            response = {'affected_paths': [c['path'] for c in value['changes']], 'unknown_dependencies': False, 'reasons': []}
        else:
            raise AssertionError(task)
        return {'content': json.dumps(response), 'usage': {'total_tokens': 10}}
    monkeypatch.setattr(api.sources, 'probe_repository', probe)
    monkeypatch.setattr(api.sources, 'fetch_manifest_page', manifest)
    monkeypatch.setattr(api.sources, 'read_source', read)
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    return api, calls


async def drive(api, ctx, rt, artifacts=None):
    artifacts = artifacts if artifacts is not None else {}
    executions, known_specs = [], {}
    for _ in range(500):
        specs = api.plan_steps(ctx, artifacts)
        assert specs == api.plan_steps(deepcopy(ctx), deepcopy(artifacts))
        for spec in specs:
            if spec['key'] in known_specs:
                assert spec == known_specs[spec['key']], 'Discovered step inputs/dependencies must remain immutable'
            known_specs[spec['key']] = deepcopy(spec)
        runnable = [s for s in specs if s['key'] not in artifacts and all(k in artifacts for k in s['requires'])]
        if not runnable:
            return artifacts, executions
        spec = runnable[0]
        out = await api.execute_stage(spec['stage'], {'context': ctx, 'payload': spec['payload'], 'artifacts': artifacts}, rt)
        assert set(out) == {'artifact', 'usage', 'request_meta'}
        artifacts[spec['key']] = out['artifact']
        executions.append(spec['key'])
    raise AssertionError('Stage graph did not terminate')


@pytest.mark.asyncio
async def test_stage_graph_covers_more_than_twenty_files_and_assembles_report(monkeypatch):
    api, calls = await fixture_pipeline(monkeypatch)
    artifacts, executed = await drive(api, context(), runtime(api))
    assert 'report' in artifacts and 'candidates' in artifacts
    output = artifacts['report']
    assert output['coverage_status'] == 'COMPLETE' and output['source_revision'] == SHA
    assert len(output['snapshot']['files']) == 24
    assert output['report']['sections'] and output['report']['claims']
    assert all(c['source_version'] == SHA and c['source_id'] for c in output['report']['architecture'][0]['citations'])
    assert artifacts['candidates']['candidates'][0]['mtype'] == 'ProjectUnderstanding'
    assert 'private-fixture' not in json.dumps(artifacts)
    assert not any(p.get('task') == 'final_report' for p in calls)
    assert len(executed) == len(set(executed))


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['en', None])
async def test_project_model_requests_use_one_language_and_preserve_source(monkeypatch, language):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    quote = '# 原始源码注释'
    original_read = api.sources.read_source
    original_chat = api.AgentAdapter.chat_with
    prompts = {}

    async def read(*args, **kwargs):
        return {**await original_read(*args, **kwargs), 'content': quote + '\nreturn 1\n'}

    async def chat(self, cfg, system, user, **kwargs):
        request = json.loads(user)
        prompts[request['task']] = (system, request)
        return await original_chat(self, cfg, system, user, **kwargs)

    monkeypatch.setattr(api.sources, 'read_source', read)
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    rt = runtime(api)
    rt.language = language
    await drive(api, {**context(), 'claim_input_contract': 'source-registry-v1'}, rt)
    rt.repair_diagnostic = 'ORIGINAL_VALIDATION_ERROR'
    await api._ask('analyze_change_impact', {'changes': []}, api.ImpactResponse, rt)

    assert set(prompts) == {'plan_modules', 'analyze_module', 'verify_cross_module',
                            'explain_project_section', 'extract_memory_candidates', 'analyze_change_impact'}
    assert quote in json.dumps(prompts['analyze_module'][1], ensure_ascii=False)
    for task, (system, _) in prompts.items():
        if language == 'en':
            assert not re.search(r'[\u3400-\u9fff]', system), task
        assert message('language', language) in system
    repair = prompts['analyze_change_impact'][1]['repair_request']
    assert repair['previous_validation_error'] == 'ORIGINAL_VALIDATION_ERROR'
    if language == 'en':
        assert not re.search(r'[\u3400-\u9fff]', repair['instructions'])


@pytest.mark.asyncio
async def test_planning_projection_reduces_model_input_without_losing_manifest_or_sources(monkeypatch):
    api, calls = await fixture_pipeline(monkeypatch, 96)
    artifacts, _ = await drive(api, context(), runtime(api))
    manifest = artifacts['manifest']
    original_entries = {entry['path']: entry for entry in manifest['entries']}
    planning = [call for call in calls if call['task'] == 'plan_modules']
    assert len(planning) > 1, 'The regression must cover multiple unchanged manifest partitions'
    supplied_paths = [entry['path'] for call in planning for entry in call['entries']]
    assert len(supplied_paths) == len(set(supplied_paths)) == 96
    assert set(supplied_paths) == set(original_entries)
    for call in planning:
        assert all(set(entry) == {'path', 'category', 'size'} for entry in call['entries'])
        assert call['entries'] == [{key: original_entries[entry['path']][key]
                                   for key in ('path', 'category', 'size')} for entry in call['entries']]
        old_request = {**call, 'entries': [original_entries[entry['path']] for entry in call['entries']]}
        assert len(api._json(call)) < len(api._json(old_request)) * 0.7
        saved_plan = next(value for key, value in artifacts.items()
                          if key.startswith('plan:') and value['paths'] == [entry['path'] for entry in call['entries']])
        assert sorted(path for module in saved_plan['modules'] for path in module['paths']) == sorted(saved_plan['paths'])
    snapshot = artifacts['report']['snapshot']
    assert snapshot['manifest'] == manifest
    assert all(entry['blob_oid'] and entry['type'] == 'blob' and entry['mode'] == '100644'
               and entry['included'] and entry['reason'] == 'fixture' for entry in manifest['entries'])
    registry = snapshot['source_registry']
    assert len(registry) == 96
    assert {source['path'] for source in registry.values()} == set(original_entries)
    for key, value in artifacts.items():
        if key.startswith('read:'):
            assert registry[value['source']['source_id']] == value['source']
    assert artifacts['report']['coverage_status'] == 'COMPLETE'


@pytest.mark.asyncio
@pytest.mark.parametrize('mistake', ['missing', 'duplicate'])
async def test_projected_planning_still_rejects_inexact_path_coverage(monkeypatch, mistake):
    api, _ = await fixture_pipeline(monkeypatch, 3)
    artifacts, _ = await drive(api, context(), runtime(api))
    plan = next(spec for spec in api.plan_steps(context(), artifacts) if spec['stage'] == 'plan_modules')
    before = deepcopy(artifacts)
    async def invalid_plan(self, cfg, system, user, **kwargs):
        paths = [entry['path'] for entry in json.loads(user)['entries']]
        paths = paths[:-1] if mistake == 'missing' else [*paths[:-1], paths[0]]
        return {'content': json.dumps({'modules': [{'name': 'Core', 'purpose': 'Inspect', 'paths': paths}]})}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', invalid_plan)
    with pytest.raises(api.StageError) as caught:
        await api.execute_stage('plan_modules', {'context': context(), 'payload': plan['payload'], 'artifacts': artifacts}, runtime(api))
    assert caught.value.category == 'INVALID_SCHEMA'
    assert artifacts == before


@pytest.mark.asyncio
async def test_resume_uses_successful_artifacts_without_repeating_reads(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 2)
    artifacts, executed = await drive(api, context(), runtime(api))
    saved = {k: v for k, v in artifacts.items() if not k.startswith(('section:', 'report', 'candidates'))}
    again, resumed = await drive(api, context(), runtime(api), saved)
    assert again['report']['coverage_status'] == 'COMPLETE'
    assert not any(k.startswith(('read:', 'analyze:', 'plan:', 'module:')) for k in resumed)


@pytest.mark.asyncio
async def test_invalid_source_version_or_outside_chunk_line_is_rejected(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    original = api.AgentAdapter.chat_with
    async def invalid(self, cfg, system, user, **kwargs):
        result = await original(self, cfg, system, user, **kwargs)
        if json.loads(user)['task'] == 'analyze_module':
            value = json.loads(result['content'])
            value['claims'][0]['citations'][0]['end_line'] = 9000
            result['content'] = json.dumps(value)
        return result
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', invalid)
    with pytest.raises(api.StageError) as exc:
        await drive(api, context(), runtime(api))
    assert exc.value.category == 'INVALID_CITATION'


@pytest.mark.asyncio
async def test_adapter_adds_safe_metadata_without_changing_legacy_return(monkeypatch):
    from app.services.llm import AgentAdapter
    original = httpx.AsyncClient
    responses = [httpx.Response(200, json={'choices': [{'message': {'content': '{"x":1}'}, 'finish_reason': 'length'}]}, headers={'x-request-id': 'request-1'}),
                 httpx.Response(429, text='private response body', headers={'retry-after': '9', 'x-request-id': 'request-2'})]
    async def handler(request): return responses.pop(0)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    adapter = AgentAdapter()
    cfg = {'provider': 'fixture', 'model': 'fixture', 'api_key': 'fixture', 'base_url': 'https://fixture.invalid'}
    assert await adapter.chat_with(cfg, 's', 'u') == {'content': '{"x":1}'}
    assert getattr(adapter, 'last_response_meta', {}).get('finish_reason') == 'length'
    assert adapter.last_response_meta['request_id'] == 'request-1'
    assert await adapter.chat_with(cfg, 's', 'u') is None
    assert adapter.last_error['retry_after'] == 9 and adapter.last_error['request_id'] == 'request-2'
    assert 'private' not in json.dumps(adapter.last_error)


def test_stage_specs_explicitly_distinguish_model_work():
    api = engine()
    spec = api.plan_steps(context(), {})[0]
    assert spec['uses_model'] is False


@pytest.mark.asyncio
async def test_incremental_reuses_unaffected_module_with_original_source_version(monkeypatch):
    api, calls = await fixture_pipeline(monkeypatch, 2)
    original_chat = api.AgentAdapter.chat_with
    async def separate(self, cfg, system, user, **kwargs):
        value = json.loads(user)
        if value['task'] == 'plan_modules':
            return {'content': json.dumps({'modules': [{'name': e['path'], 'purpose': 'Inspect module', 'paths': [e['path']]} for e in value['entries']]})}
        return await original_chat(self, cfg, system, user, **kwargs)
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', separate)
    first, _ = await drive(api, context(), runtime(api))
    old_probe, old_manifest, old_read = api.sources.probe_repository, api.sources.fetch_manifest_page, api.sources.read_source
    target = 'b' * 40
    async def probe(p, c): return {**await old_probe(p, c), 'target_sha': target, 'parents': [SHA]}
    async def manifest(*args):
        value = await old_manifest(*args)
        value['entries'][0]['blob_oid'] = 'f' * 40
        return value
    async def read(p, s, e, c, **kw): return {**await old_read(p, s, e, c, **kw), 'source_revision': target}
    monkeypatch.setattr(api.sources, 'probe_repository', probe)
    monkeypatch.setattr(api.sources, 'fetch_manifest_page', manifest)
    monkeypatch.setattr(api.sources, 'read_source', read)
    ctx = {**context(), 'mode': 'incremental', 'base_sha': SHA, 'target_sha': target,
           'baseline': {**first['report'], 'artifacts': first}}
    result, executed = await drive(api, ctx, runtime(api))
    reused = [m for m in result['report']['snapshot']['modules'] if m.get('compatibility')]
    assert len(reused) == 1 and reused[0]['paths'] == ['src/f1.py']
    assert reused[0]['sources'][0]['source_revision'] == SHA
    assert reused[0]['compatibility']['target_sha'] == target
    assert len([k for k in executed if k.startswith('read:')]) == 1
    assert any(c.get('task') == 'analyze_change_impact' for c in calls)
    full, full_executed = await drive(api, {**ctx, 'mode': 'full'}, runtime(api))
    assert len([k for k in full_executed if k.startswith('read:')]) == 2
    assert not any(m.get('compatibility') for m in full['report']['snapshot']['modules'])
    foreign = deepcopy(ctx)
    foreign['baseline']['snapshot']['modules'][0]['provenance']['workspace_id'] = 999
    assert api._reusable_module(foreign, result['manifest'], result['report']['snapshot']['modules'][0]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,status,category', [('http', 401, 'AUTH_BLOCKED'), ('http', 429, 'RATE_LIMITED'),
    ('http', 503, 'PROVIDER_5XX'), ('http', 413, 'INPUT_TOO_LARGE'), ('http', 400, 'PROVIDER_REJECTED'),
    ('network', None, 'NETWORK_TRANSIENT'), ('timeout', None, 'REQUEST_TIMEOUT')])
async def test_model_failures_are_classified_without_retry(monkeypatch, kind, status, category):
    api = engine()
    calls = []
    async def fail(self, *args, **kwargs):
        calls.append(1)
        self.last_error = {'kind': kind, 'status': status, 'retry_after': 3, 'request_id': 'req-123'}
        return None
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', fail)
    with pytest.raises(api.StageError) as exc:
        await api._ask('plan_modules', {'entries': []}, api.PlanResponse, runtime(api))
    assert exc.value.category == category and len(calls) == 1
    assert exc.value.retry_after == 3 and exc.value.request_id == 'req-123'


@pytest.mark.asyncio
async def test_output_truncation_and_schema_injection_never_become_artifacts(monkeypatch):
    api = engine()
    async def truncate(self, *args, **kwargs):
        self.last_response_meta = {'finish_reason': 'length', 'request_id': 'r1'}
        return {'content': '{"modules":[]'}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', truncate)
    with pytest.raises(api.StageError) as exc:
        await api._ask('plan_modules', {}, api.PlanResponse, runtime(api))
    assert exc.value.category == 'OUTPUT_TRUNCATED'
    async def injection(self, *args, **kwargs):
        return {'content': json.dumps({'modules': [], 'private secret instruction': 'print key'})}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', injection)
    with pytest.raises(api.StageError) as exc:
        await api._ask('plan_modules', {}, api.PlanResponse, runtime(api))
    assert exc.value.category == 'INVALID_SCHEMA' and 'private secret' not in str(exc.value)


@pytest.mark.asyncio
async def test_budget_checks_prevent_model_calls_and_unknown_usage_stays_unknown(monkeypatch):
    api = engine()
    calls = []
    async def respond(*args, **kwargs):
        calls.append(1)
        return {'content': '{"modules":[{"name":"m","purpose":"inspect","paths":["a.py"]}]}'}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', respond)
    rt = runtime(api)
    rt.input_chars = 100
    with pytest.raises(api.StageError) as exc:
        await api._ask('plan_modules', {}, api.PlanResponse, rt)
    assert exc.value.category == 'INPUT_TOO_LARGE' and not calls
    rt.input_chars = 40000
    _, usage, meta = await api._ask('plan_modules', {}, api.PlanResponse, rt)
    assert usage['known'] is False and meta['input_tokens_estimate'] > 0
    assert 'not_actual_tokens' in meta['token_estimate_method']


@pytest.mark.asyncio
async def test_large_manifest_does_not_reappear_in_each_module_request(monkeypatch):
    api, calls = await fixture_pipeline(monkeypatch, 1)
    ctx, rt = context(), runtime(api)
    artifacts, _ = await drive(api, ctx, rt)
    spec = next(s for s in api.plan_steps(ctx, artifacts) if s['stage'] == 'analyze_module')
    bigger = deepcopy(artifacts)
    bigger['manifest']['entries'].extend({'path': f'directory_{i}/module_longname_{i}.py', 'included': True, 'blob_oid': f'{i:040x}', 'type': 'blob'} for i in range(3000))
    out = await api.execute_stage('analyze_module', {'context': ctx, 'payload': spec['payload'], 'artifacts': bigger}, rt)
    assert out['request_meta']['input_chars'] <= rt.input_chars
    full, partial = (call for call in calls if call['task'] == 'analyze_module')
    assert full['available_paths'] == ['src/f0.py'] and full['path_catalog_partial'] is False
    assert partial['path_catalog_partial'] is True and len(partial['available_paths']) < 3001
    assert full['path_catalog_note'] != partial['path_catalog_note']


@pytest.mark.asyncio
async def test_followup_read_does_not_change_existing_source_step_dependencies(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 2)
    previous = api.AgentAdapter.chat_with
    async def chat(self, cfg, system, user, **kwargs):
        p = json.loads(user)
        if p['task'] == 'plan_modules':
            return {'content': json.dumps({'modules': [{'name': e['path'], 'purpose': 'Inspect', 'paths': [e['path']]} for e in p['entries']]})}
        result = await previous(self, cfg, system, user, **kwargs)
        if p['task'] == 'analyze_module' and p['sources'][0]['path'] == 'src/f0.py':
            value = json.loads(result['content'])
            value['read_requests'] = [{'path': 'src/f1.py', 'reason': 'Check called implementation'}]
            result['content'] = json.dumps(value)
        return result
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    result, _ = await drive(api, context(), runtime(api))
    assert result['report']['coverage_status'] == 'COMPLETE'
    caller = next(m for m in result['report']['snapshot']['modules'] if m['paths'] == ['src/f0.py'])
    assert 'src/f1.py' in caller['dependencies'], 'Actually read followups must invalidate source-dependent cache even if model omits an edge'


@pytest.mark.asyncio
@pytest.mark.parametrize('unsafe', ['../other.py', '/root/file.py', 'src\\file.py', 'src/invalid\x00.py'])
async def test_module_unsafe_optional_paths_still_fail(monkeypatch, unsafe):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    previous = api.AgentAdapter.chat_with

    async def chat(self, cfg, system, user, **kwargs):
        result = await previous(self, cfg, system, user, **kwargs)
        if json.loads(user)['task'] == 'analyze_module':
            value = json.loads(result['content'])
            value['read_requests'] = [{'path': unsafe, 'reason': 'Check untrusted path'}]
            result['content'] = json.dumps(value)
        return result

    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    with pytest.raises(api.StageError) as error:
        await drive(api, context(), runtime(api))
    assert error.value.category == 'INVALID_CITATION'


@pytest.mark.asyncio
async def test_unmatched_optional_paths_keep_cited_claims_and_disable_reuse(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    previous = api.AgentAdapter.chat_with
    original_manifest = api.sources.fetch_manifest_page

    async def manifest(*args, **kwargs):
        page = await original_manifest(*args, **kwargs)
        page['entries'].append({'path': 'src/excluded.py', 'included': False, 'category': 'excluded',
                                'type': 'blob', 'mode': '100644', 'blob_oid': 'f' * 40,
                                'size': 10, 'reason': 'Excluded fixture'})
        return page

    async def chat(self, cfg, system, user, **kwargs):
        result = await previous(self, cfg, system, user, **kwargs)
        if json.loads(user)['task'] == 'analyze_module':
            value = json.loads(result['content'])
            value['read_requests'] = [{'path': 'src/missing.py', 'reason': 'Check optional implementation'}]
            value['dependencies'] = [{'path': 'src/excluded.py', 'reason': 'Consider excluded dependency'}]
            result['content'] = json.dumps(value)
        return result

    monkeypatch.setattr(api.sources, 'fetch_manifest_page', manifest)
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    artifacts, _ = await drive(api, context(), runtime(api))
    output = artifacts['report']
    module = output['snapshot']['modules'][0]
    assert output['coverage_status'] == 'COMPLETE'
    assert module['claims'][0]['citations'][0]['path'] == 'src/f0.py'
    assert module['dependency_scope_complete'] is False
    assert module['dependencies'] == []
    assert len(module['unknowns']) == 2
    assert set(module['unknowns']) <= set(output['report']['unknowns'])
    assert {source['path'] for source in module['sources']} == {'src/f0.py'}

    ctx = {**context(), 'mode': 'incremental', 'baseline': {'snapshot': output['snapshot'], 'artifacts': artifacts}}
    incremental_manifest = {**artifacts['manifest'], 'diff': {'complete': True, 'requires_full': False, 'changes': []}}
    assert api._reusable_module(ctx, incremental_manifest, module) is None
    assert api._impact_input(ctx, incremental_manifest) is None


@pytest.mark.asyncio
async def test_failed_module_keeps_other_module_and_resumes_only_missing_work(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 2)
    previous = api.AgentAdapter.chat_with
    failed = False
    async def chat(self, cfg, system, user, **kwargs):
        nonlocal failed
        p = json.loads(user)
        if p['task'] == 'plan_modules':
            return {'content': json.dumps({'modules': [{'name': e['path'], 'purpose': 'Inspect', 'paths': [e['path']]} for e in p['entries']]})}
        if p['task'] == 'analyze_module' and p['sources'][0]['path'] == 'src/f1.py' and not failed:
            failed = True
            self.last_error = {'kind': 'timeout'}
            return None
        return await previous(self, cfg, system, user, **kwargs)
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    saved = {}
    with pytest.raises(api.StageError):
        await drive(api, context(), runtime(api), saved)
    done = [k for k in saved if k.startswith('module:')]
    assert len(done) == 1 and 'report' not in saved
    resumed, executed = await drive(api, context(), runtime(api), saved)
    assert resumed['report']['coverage_status'] == 'COMPLETE'
    assert not set(done) & set(executed)
    assert len([k for k in executed if k.startswith('analyze:')]) == 1


@pytest.mark.asyncio
async def test_dependency_change_unknown_dependencies_and_model_change_invalidate_cache(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 2)
    first, _ = await drive(api, context(), runtime(api))
    original = first['report']['snapshot']['modules'][0]
    base = {'snapshot': first['report']['snapshot'], 'artifacts': first}
    ctx = {**context(), 'mode': 'incremental', 'baseline': base}
    manifest = deepcopy(first['manifest'])
    manifest['diff'] = {'complete': True, 'requires_full': False, 'changes': []}
    assert api._reusable_module(ctx, manifest, original)
    assert not api._reusable_module({**ctx, 'model_config_fingerprint': 'other'}, manifest, original)
    original['dependency_scope_complete'] = False
    assert not api._reusable_module(ctx, manifest, original)
    original['dependency_scope_complete'] = True
    manifest['entries'][1]['blob_oid'] = 'f' * 40
    manifest['diff']['changes'] = [{'status': 'modified', 'path': 'src/f1.py'}]
    assert not api._reusable_module(ctx, manifest, original)
    manifest['diff'] = {'complete': False, 'requires_full': True, 'changes': []}
    assert not api._reusable_module(ctx, manifest, original)


@pytest.mark.asyncio
async def test_issue_candidates_are_bound_to_report_issue_and_hypotheses_cannot_be_promoted(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    previous = api.AgentAdapter.chat_with
    async def chat(self, cfg, system, user, **kwargs):
        p = json.loads(user)
        result = await previous(self, cfg, system, user, **kwargs)
        value = json.loads(result['content'])
        if p['task'] == 'analyze_module':
            value['issues'] = [{'title': 'Risk', 'skill_key': 'reliability', 'severity': 'medium', 'detail': 'A possible timeout risk.',
                'impact': 'Waiting may persist.', 'recommendation': 'Inspect timeout configuration.', 'status': 'hypothesis',
                'citations': value['claims'][0]['citations'], 'interview_questions': ['How is waiting bounded?']}]
        if p['task'] == 'extract_memory_candidates':
            issue_claim = next(c for c in p['claims'] if ':issue' in c['id'])
            assert any(i['claim_id'] == issue_claim['id'] for i in p['issues'])
            value = {'candidates': [{'fact_key': 'timeout', 'kind': 'risk', 'content': '存在待验证的超时风险。',
                'claim_ids': [issue_claim['id']], 'fact_type': 'INFERRED'}]}
        return {'content': json.dumps(value)}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    artifacts, _ = await drive(api, context(), runtime(api))
    candidate = artifacts['candidates']['candidates'][0]
    assert candidate['issue_id'] == 'I1' and candidate['mtype'] == 'ProjectIssue'
    candidate_spec = next(s for s in api.plan_steps(context(), artifacts) if s['stage'] == 'extract_candidates')
    async def promote(self, cfg, system, user, **kwargs):
        result = await chat(self, cfg, system, user, **kwargs)
        value = json.loads(result['content'])
        value['candidates'][0]['fact_type'] = 'CODE_OBSERVED'
        return {'content': json.dumps(value)}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', promote)
    with pytest.raises(api.StageError) as exc:
        await api.execute_stage('extract_candidates', {'context': context(), 'payload': candidate_spec['payload'], 'artifacts': artifacts}, runtime(api))
    assert exc.value.category == 'INVALID_CITATION'


@pytest.mark.asyncio
async def test_empty_truncated_output_is_distinct_from_schema_error(monkeypatch):
    api = engine()
    async def truncate(self, *args, **kwargs):
        self.last_response_meta = {'finish_reason': 'length'}
        self.last_error = {'kind': 'invalid_response'}
        return None
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', truncate)
    with pytest.raises(api.StageError) as exc:
        await api._ask('plan_modules', {}, api.PlanResponse, runtime(api))
    assert exc.value.category == 'OUTPUT_TRUNCATED'


@pytest.mark.asyncio
async def test_large_change_history_is_summarized_for_one_chapter_without_losing_report_diff(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    ctx, rt = context(), runtime(api)
    artifacts, _ = await drive(api, ctx, rt)
    spec = next(s for s in api.plan_steps(ctx, artifacts) if s['stage'] == 'explain_section' and s['payload']['section'] == 'changes')
    artifacts['manifest']['diff']['changes'] = [{'status': 'modified', 'path': 'long_directory/' + str(i) + 'x' * 50} for i in range(3000)]
    before = deepcopy(artifacts['manifest']['diff'])
    out = await api.execute_stage(spec['stage'], {'context': ctx, 'payload': spec['payload'], 'artifacts': artifacts}, rt)
    assert out['request_meta']['input_chars'] <= rt.input_chars
    assert artifacts['manifest']['diff'] == before


@pytest.mark.asyncio
async def test_worker_repair_diagnostic_requests_compact_output_without_model_override(monkeypatch):
    api, calls = await fixture_pipeline(monkeypatch, 1)
    artifacts, _ = await drive(api, context(), runtime(api))
    spec = next(s for s in api.plan_steps(context(), artifacts) if s['stage'] == 'analyze_module')
    calls.clear()
    await api.execute_stage(spec['stage'], {'context': context(), 'payload': spec['payload'], 'artifacts': artifacts,
        'repair_diagnostic': '模型输出被服务端截断'}, runtime(api))
    assert calls[0].get('repair_request', {}).get('output_strategy') == 'smaller_response_complete_schema'


@pytest.mark.asyncio
async def test_read_stage_rejects_wrong_commit_from_adapter(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    original = api.sources.read_source
    async def wrong(*args, **kw): return {**await original(*args, **kw), 'source_revision': 'f' * 40}
    monkeypatch.setattr(api.sources, 'read_source', wrong)
    with pytest.raises(api.StageError) as exc:
        await drive(api, context(), runtime(api))
    assert exc.value.category == 'INVALID_CITATION'


@pytest.mark.asyncio
@pytest.mark.parametrize('response', ['{"modules":', '{"modules":[]}'])
async def test_invalid_json_and_schema_preserve_safe_known_request_usage(monkeypatch, response):
    api = engine()
    async def invalid(self, *args, **kwargs):
        self.last_response_meta = {'request_id': 'request-known', 'status': 200}
        return {'content': response, 'usage': {'prompt_tokens': 12, 'completion_tokens': 7, 'total_tokens': 19}}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', invalid)
    with pytest.raises(api.StageError) as exc:
        await api._ask('plan_modules', {}, api.PlanResponse, runtime(api))
    assert exc.value.usage['total_tokens'] == 19
    assert exc.value.request_id == 'request-known'
    assert exc.value.request_meta['output_bytes'] == len(response.encode())
    assert exc.value.request_meta['input_bytes'] > 0
    assert response not in json.dumps(exc.value.request_meta)


@pytest.mark.asyncio
async def test_invalid_citation_retains_successful_http_usage_and_metadata(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    previous = api.AgentAdapter.chat_with
    async def bad_ref(self, *args, **kwargs):
        result = await previous(self, *args, **kwargs)
        value = json.loads(result['content'])
        if 'claims' in value:
            value['claims'][0]['citations'][0]['end_line'] = 999
            result['content'] = json.dumps(value)
            self.last_response_meta = {'request_id': 'citation-request'}
        return result
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', bad_ref)
    with pytest.raises(api.StageError) as exc:
        await drive(api, context(), runtime(api))
    assert exc.value.category == 'INVALID_CITATION'
    assert exc.value.usage['total_tokens'] == 10
    assert exc.value.request_meta['task'] == 'analyze_module'
    assert exc.value.request_id == 'citation-request'


@pytest.mark.asyncio
async def test_total_source_budget_counts_pre_redaction_downloads_and_resumes(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 2)
    old_read, old_manifest = api.sources.read_source, api.sources.fetch_manifest_page
    downloads = []
    async def manifest(*args):
        value = await old_manifest(*args)
        for e in value['entries']: e['size'] = 100
        return value
    async def read(*args, **kwargs):
        downloads.append((args[2]['path'], kwargs['max_bytes']))
        return {**await old_read(*args, **kwargs), 'download_bytes': 100, 'source_bytes': 100}
    monkeypatch.setattr(api.sources, 'fetch_manifest_page', manifest)
    monkeypatch.setattr(api.sources, 'read_source', read)
    rt, saved = runtime(api), {}
    rt.max_source_bytes = 150
    with pytest.raises(api.StageError) as exc:
        await drive(api, context(), rt, saved)
    assert exc.value.category == 'BUDGET_EXHAUSTED'
    assert downloads == [('src/f0.py', 150)], 'Do not start the second known-oversized download'
    assert len([k for k in saved if k.startswith('read:')]) == 1
    rt.max_source_bytes = 250
    result, _ = await drive(api, context(), rt, saved)
    assert downloads == [('src/f0.py', 150), ('src/f1.py', 150)]
    assert result['report']['snapshot']['coverage']['source_download_bytes'] == 200


@pytest.mark.asyncio
async def test_empty_completion_keeps_reported_usage_without_changing_none_contract(monkeypatch):
    api = engine()
    original = httpx.AsyncClient
    async def handler(request):
        return httpx.Response(200, json={'id': 'completion-1', 'choices': [{'message': {'content': ''}, 'finish_reason': 'length'}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 22, 'total_tokens': 33}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(api.StageError) as exc:
        await api._ask('plan_modules', {}, api.PlanResponse, runtime(api))
    assert exc.value.category == 'OUTPUT_TRUNCATED'
    assert exc.value.usage['total_tokens'] == 33 and exc.value.request_id == 'completion-1'


@pytest.mark.asyncio
async def test_visible_source_normalization_is_disclosed_to_model_and_report(monkeypatch):
    api, calls = await fixture_pipeline(monkeypatch, 1)
    original = api.sources.read_source
    async def read(*args, **kwargs):
        return {**await original(*args, **kwargs), 'encoding': 'utf-8', 'normalizations': [
            {'kind': 'visible_control_escape', 'codepoint': 'U+0000', 'line': 2, 'source_column': 12,
             'display_column': 12, 'replacement': '\\u0000'}]}
    monkeypatch.setattr(api.sources, 'read_source', read)
    result, _ = await drive(api, context(), runtime(api))
    module_request = next(p for p in calls if p['task'] == 'analyze_module')
    assert module_request['sources'][0]['normalizations'][0]['codepoint'] == 'U+0000'
    assert any('转义' in note for note in result['report']['report']['unknowns'])
    assert any('转义' in p['text'] for s in result['report']['report']['sections'] if s['id'] == 'limitations' for p in s['paragraphs'])


@pytest.mark.asyncio
@pytest.mark.parametrize(('task', 'expected'), [('plan_modules', 'flash'), ('analyze_module', 'strong')])
async def test_project_stage_uses_coach_auto_tier_and_traces_dispatched_model(monkeypatch, task, expected):
    api = engine()
    rt = runtime(api)
    assert rt.max_source_bytes is None
    base = dict(rt.llm_config)
    rt.llm_config['auto_profiles'] = {tier: {**base, 'model': tier + '-fixture'} for tier in ('flash', 'strong')}
    calls = []
    async def chat(self, config, *args, **kwargs):
        calls.append(config['model'])
        return {'content': '{"candidates":[]}'}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    _, _, meta = await api._ask(task, {}, api.CandidateResponse, rt)
    assert calls == [expected + '-fixture']
    assert (meta['routing_mode'], meta['tier'], meta['model']) == ('auto', expected, calls[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(('task', 'repair', 'missing', 'preferred', 'served'), [
    ('plan_modules', None, 'flash', 'flash', 'strong'),
    ('verify_cross_module', None, 'strong', 'strong', 'flash'),
    ('plan_modules', 'INVALID_SCHEMA', None, 'strong', 'strong'),
])
async def test_project_auto_repair_and_missing_profile(monkeypatch, task, repair, missing, preferred, served):
    api = engine()
    rt = runtime(api)
    rt.repair_diagnostic = repair
    base = dict(rt.llm_config)
    rt.llm_config['auto_profiles'] = {
        tier: {**base, 'model': tier, 'api_key': '' if tier == missing else 'fixture'}
        for tier in ('flash', 'strong')
    }

    async def answer(self, config, *args, **kwargs):
        assert config['model'] == served
        return {'content': '{"candidates":[]}'}

    monkeypatch.setattr(api.AgentAdapter, 'chat_with', answer)
    _, _, meta = await api._ask(task, {}, api.CandidateResponse, rt)
    assert (meta['requested_tier'], meta['tier'], meta['routing_policy']) == (preferred, served, 'project-stage-v2')
