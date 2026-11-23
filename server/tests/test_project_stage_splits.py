"""Durable source partition recovery, all model/source calls are explicit fixtures."""
import importlib
import importlib.util
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_project_autopilot_engine import SHA, context, drive, fixture_pipeline, runtime


def split_api():
    name = 'app.services.project_stage_splits'
    assert importlib.util.find_spec(name), 'Persistent source split contract is missing'
    return importlib.import_module(name)


async def prepared(monkeypatch):
    api, calls = await fixture_pipeline(monkeypatch, 4)
    ctx = {**context(), 'run_id': 7, 'target_sha': SHA,
           'pipeline_version': api.PIPELINE_VERSION, 'prompt_bundle_version': api.PROMPT_BUNDLE_VERSION}
    artifacts = {}
    for _ in range(20):
        spec = next(s for s in api.plan_steps(ctx, artifacts)
                    if s['key'] not in artifacts and all(k in artifacts for k in s['requires']))
        if spec['stage'] == 'analyze_module':
            break
        artifacts[spec['key']] = (await api.execute_stage(spec['stage'],
            {'context': ctx, 'payload': spec['payload'], 'artifacts': artifacts}, runtime(api)))['artifact']
    assert len(spec['payload']['sources']) == 4
    from app.services.project_jobs import fingerprint
    parent = {'id': 22, 'run_id': 7, 'workspace_id': 1, 'project_id': 2,
        'step_key': spec['key'], 'stage': spec['stage'], 'module_key': spec['module_key'],
        'status': 'BLOCKED', 'input_json': deepcopy(spec['payload']), 'requires': deepcopy(spec['requires']),
        'attempt_count': 2, 'retry_count': 2, 'attempts': [{'outcome': 'OUTPUT_TRUNCATED'}, {'outcome': 'OUTPUT_TRUNCATED'}],
        'uses_model': True}
    parent['input_hash'] = fingerprint({'payload': parent['input_json'], 'model': ctx['model_config_fingerprint'],
        'pipeline': ctx['pipeline_version'], 'prompt': ctx['prompt_bundle_version'], 'target': SHA})
    groups = [parent['input_json']['sources'][:2], parent['input_json']['sources'][2:]]
    return api, ctx, artifacts, parent, groups, calls


@pytest.mark.asyncio
async def test_split_freezes_parent_and_replaces_only_its_graph_node(monkeypatch):
    api, ctx, artifacts, parent, groups, calls = await prepared(monkeypatch)
    helper = split_api(); frozen = deepcopy((ctx, artifacts, parent, groups))
    plan = helper.create_plan(ctx, parent, groups, artifacts=artifacts)
    split_ctx = {**ctx, 'source_splits': [plan]}
    helper.validate_plan(plan, split_ctx, artifacts)
    specs = helper.plan_specs(plan)
    assert len(specs) == 3 and [s['uses_model'] for s in specs] == [True, True, False]
    assert specs[-1]['stage'] == 'merge_analysis_split'
    assert all(plan['fingerprint'] in s['key'] for s in specs)
    assert [s['payload']['sources'] for s in specs[:-1]] == groups
    assert all(s['payload']['analysis_key'] == s['key'] for s in specs[:-1])
    legacy = api.plan_steps(ctx, artifacts)
    updated = api.plan_steps(split_ctx, artifacts)
    assert parent['step_key'] not in {s['key'] for s in updated}
    assert [s for s in updated if s['key'] not in {p['key'] for p in specs}] == [s for s in legacy if s['key'] != parent['step_key']]
    assert (ctx, artifacts, parent, groups) == frozen
    assert api.plan_steps({**ctx, 'source_splits': []}, artifacts) == legacy


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'changed_range', 'empty', 'five_groups', 'foreign_scope', 'bad_hash', 'child_parent', 'successful_parent'])
async def test_invalid_or_recursive_splits_are_rejected(monkeypatch, bad):
    api, ctx, artifacts, parent, groups, _ = await prepared(monkeypatch)
    helper = split_api()
    if bad == 'missing': groups[1].pop()
    elif bad == 'duplicate': groups[1][0] = deepcopy(groups[0][0])
    elif bad == 'changed_range': groups[1][0]['end_line'] += 1
    elif bad == 'empty': groups = [parent['input_json']['sources'], []]
    elif bad == 'five_groups': groups = [[parent['input_json']['sources'][0]]] * 5
    elif bad == 'foreign_scope': parent['workspace_id'] = 99
    elif bad == 'bad_hash': parent['input_hash'] = 'invalid'
    elif bad == 'child_parent': parent['input_json']['source_split'] = {'role': 'child'}
    else: parent['status'] = 'SUCCEEDED'
    with pytest.raises(api.StageError) as caught:
        helper.create_plan(ctx, parent, groups, artifacts=artifacts)
    assert caught.value.category == 'CONFIG_CHANGED'


@pytest.mark.asyncio
async def test_split_executes_children_merges_all_fields_and_resumes_without_parent(monkeypatch):
    api, ctx, artifacts, parent, groups, calls = await prepared(monkeypatch)
    helper = split_api(); plan = helper.create_plan(ctx, parent, groups, artifacts=artifacts)
    ctx = {**ctx, 'source_splits': [plan]}
    original = deepcopy(artifacts)
    result, executed = await drive(api, ctx, runtime(api), artifacts)
    specs = helper.plan_specs(plan); child_keys = [s['key'] for s in specs[:-1]]; merge_key = specs[-1]['key']
    assert parent['step_key'] not in result and parent['step_key'] not in executed
    assert result['report']['coverage_status'] == 'COMPLETE'
    merge = result[merge_key]
    for field in ('claims', 'issues', 'unknowns', 'dependencies', 'read_requests', 'boundary_adjustments'):
        assert merge[field] == [x for k in child_keys for x in result[k].get(field, [])]
    assert merge['dependency_scope_complete'] == all(result[k]['dependency_scope_complete'] for k in child_keys)
    assert merge['source_split']['child_output_fingerprints'] == {k: api.fingerprint(result[k]) for k in child_keys}
    assert all(result[k] == value for k, value in original.items())
    assert len([c for c in calls if c['task'] == 'analyze_module']) == 2
    assert all(c.get('repair_request') for c in calls if c['task'] == 'analyze_module')
    assert all(c['id'].startswith(tuple(child_keys)) for c in merge['claims'])
    again, resumed = await drive(api, ctx, runtime(api), deepcopy(result))
    assert not resumed and again == result
    from app.services.project_run_compatibility import assert_compatible
    assert_compatible(SimpleNamespace(**ctx, result_meta={'source_splits': [plan]}), result)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['missing_child', 'changed_payload', 'changed_source', 'changed_child_result', 'foreign_plan', 'unregistered_artifact', 'missing_child_list'])
async def test_split_checkpoint_guard_fails_before_model_or_publication(monkeypatch, bad):
    api, ctx, artifacts, parent, groups, calls = await prepared(monkeypatch)
    helper = split_api(); plan = helper.create_plan(ctx, parent, groups, artifacts=artifacts)
    ctx = {**ctx, 'source_splits': [plan]}
    result, _ = await drive(api, ctx, runtime(api), artifacts)
    specs = helper.plan_specs(plan); child = specs[0]['key']; merge = specs[-1]
    if bad == 'missing_child': del result[child]
    elif bad == 'changed_payload': result[child]['source_split']['payload_fingerprint'] = 'bad'
    elif bad == 'changed_source': result[api._read_key(groups[0][0]['path'])]['chunks'][0]['text'] = 'changed'
    elif bad == 'changed_child_result': result[child]['claims'][0]['text'] += ' changed'
    elif bad == 'foreign_plan': ctx['source_splits'][0]['identity']['workspace_id'] = 88
    elif bad == 'missing_child_list':
        del result[child]['read_requests']
        # Before merge exists, a malformed checkpoint must not silently default
        # a lost read request list to empty during the merge.
        result = {k: v for k, v in result.items() if not k.startswith(('merge:', 'module:', 'cross:', 'section:', 'candidate-part:')) and k not in ('report', 'candidates')}
    else: ctx['source_splits'] = []
    count = len(calls)
    with pytest.raises(api.StageError) as caught:
        await api.execute_stage('merge_analysis_split', {'context': ctx, 'payload': merge['payload'], 'artifacts': result}, runtime(api))
    assert caught.value.category == 'CONFIG_CHANGED' and len(calls) == count


@pytest.mark.asyncio
async def test_create_recovery_after_module_assembly_is_rejected(monkeypatch):
    api, ctx, artifacts, parent, groups, _ = await prepared(monkeypatch)
    plan = split_api().create_plan(ctx, parent, groups, artifacts=artifacts)
    result, _ = await drive(api, ctx, runtime(api), artifacts)
    with pytest.raises(api.StageError):
        split_api().validate_plan(plan, {**ctx, 'source_splits': [plan]}, result)


@pytest.mark.asyncio
async def test_split_followup_requests_from_every_child_are_not_lost(monkeypatch):
    api, ctx, artifacts, parent, groups, calls = await prepared(monkeypatch)
    # The supplied manifest has a real additional fixture path. It is outside
    # the existing module plan so only the merged read request can discover it.
    artifacts['manifest']['entries'].append({**deepcopy(artifacts['manifest']['entries'][0]),
        'path': 'src/dependency.py', 'blob_oid': 'd' * 40})
    plan = split_api().create_plan(ctx, parent, groups, artifacts=artifacts)
    ctx = {**ctx, 'source_splits': [plan]}
    old_chat = api.AgentAdapter.chat_with
    async def chat(self, cfg, system, user, **kwargs):
        response = await old_chat(self, cfg, system, user, **kwargs)
        request = json.loads(user); value = json.loads(response['content'])
        if request['task'] == 'analyze_module' and any(s['path'] == groups[1][0]['path'] for s in request['sources']):
            value['read_requests'] = [{'path': 'src/dependency.py', 'reason': 'Fixture caller edge requires verification'}]
            value['dependencies'] = deepcopy(value['read_requests'])
            value['unknowns'] = ['Fixture dependency initially unread']
            value['dependency_scope_complete'] = False
        return {**response, 'content': json.dumps(value)}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    # Keep the existing valid plan partition stable: a second planning partition
    # would itself add the dependency as a new module instead of exercising reads.
    monkeypatch.setattr(api, '_manifest_partitions', lambda manifest, budget: [[e for e in manifest['entries'] if e['path'] != 'src/dependency.py']])
    output, executed = await drive(api, ctx, runtime(api), artifacts)
    merge = output[split_api().plan_specs(plan)[-1]['key']]
    assert merge['read_requests'][0]['path'] == 'src/dependency.py'
    module = output['module:' + parent['module_key']]
    assert 'src/dependency.py' in module['dependencies']
    assert api._read_key('src/dependency.py') in executed
    assert f'analyze:{parent["module_key"]}:1:0' in executed
    assert module['dependency_scope_complete'] is False
    assert 'Fixture dependency initially unread' in module['unknowns']


@pytest.mark.asyncio
async def test_inherited_compact_and_current_repair_are_both_preserved(monkeypatch):
    api, ctx, artifacts, parent, groups, calls = await prepared(monkeypatch)
    plan = split_api().create_plan(ctx, parent, groups, artifacts=artifacts)
    ctx = {**ctx, 'source_splits': [plan]}; child = split_api().plan_specs(plan)[0]
    await api.execute_stage('analyze_module', {'context': ctx, 'payload': child['payload'], 'artifacts': artifacts,
        'repair_diagnostic': 'CURRENT_SCHEMA_REPAIR'}, runtime(api))
    request = calls[-1]['repair_request']['previous_validation_error']
    assert 'CURRENT_SCHEMA_REPAIR' in request and '截断' in request


@pytest.mark.asyncio
async def test_source_content_or_chunk_change_before_first_child_fails_closed(monkeypatch):
    api, ctx, artifacts, parent, groups, calls = await prepared(monkeypatch)
    plan = split_api().create_plan(ctx, parent, groups, artifacts=artifacts)
    ctx = {**ctx, 'source_splits': [plan]}
    for target in ('source', 'chunks'):
        broken = deepcopy(artifacts); read = broken[api._read_key(groups[0][0]['path'])]
        if target == 'source': read['source']['content'] += ' # tamper'
        else: read['chunks'][0]['content'] += ' # tamper'
        with pytest.raises(api.StageError) as caught:
            api.plan_steps(ctx, broken)
        assert caught.value.category == 'CONFIG_CHANGED'


@pytest.mark.asyncio
async def test_missing_merge_prevents_module_or_final_report_publication(monkeypatch):
    api, ctx, artifacts, parent, groups, _ = await prepared(monkeypatch)
    plan = split_api().create_plan(ctx, parent, groups, artifacts=artifacts)
    ctx = {**ctx, 'source_splits': [plan]}
    output, _ = await drive(api, ctx, runtime(api), artifacts)
    del output[split_api().plan_specs(plan)[-1]['key']]
    from app.services.project_run_compatibility import assert_compatible
    with pytest.raises(api.StageError) as caught:
        assert_compatible(SimpleNamespace(**ctx, result_meta={'source_splits': [plan]}), output)
    assert caught.value.category == 'CONFIG_CHANGED'
