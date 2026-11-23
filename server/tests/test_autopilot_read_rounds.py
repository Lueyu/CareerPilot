"""Finite dependency exploration with existing checkpoint identities; fixture model only."""
import json
from copy import deepcopy

import pytest
from test_project_autopilot_engine import context, drive, fixture_pipeline, runtime


async def dependency_fixture(monkeypatch, *, cycle=False):
    api, calls = await fixture_pipeline(monkeypatch, 4)
    previous = api.AgentAdapter.chat_with

    async def chat(self, cfg, system, user, **kwargs):
        prompt = json.loads(user)
        if prompt['task'] == 'plan_modules':
            return {'content': json.dumps({'modules': [
                {'name': entry['path'], 'purpose': 'Follow the implementation dependency',
                 'paths': [entry['path']]} for entry in prompt['entries']]})}
        result = await previous(self, cfg, system, user, **kwargs)
        if prompt['task'] == 'analyze_module':
            value = json.loads(result['content'])
            path = prompt['sources'][0]['path']
            index = int(path.removeprefix('src/f').removesuffix('.py'))
            requested = (['src/f1.py', 'src/f1.py', 'src/f0.py'] if index == 0 else
                         ['src/f0.py', 'src/f0.py', path]) if cycle else (
                             [f'src/f{index + 1}.py'] if index < 3 else [])
            value['read_requests'] = [{'path': p, 'reason': 'Inspect the next actual dependency'}
                                      for p in requested]
            result['content'] = json.dumps(value)
        return result

    monkeypatch.setattr(api.AgentAdapter, 'chat_with', chat)
    ctx = context()
    ctx['budget'].update(max_model_calls=500, max_source_bytes=8388608)
    return api, ctx, calls


@pytest.mark.asyncio
async def test_four_layer_dependency_completes_without_implicit_three_round_pause(monkeypatch):
    api, ctx, calls = await dependency_fixture(monkeypatch)
    original_budget = deepcopy(ctx['budget'])
    artifacts, executed = await drive(api, ctx, runtime(api))
    root = next(m for m in artifacts['report']['snapshot']['modules'] if m['paths'] == ['src/f0.py'])
    assert root['dependencies'] == ['src/f1.py', 'src/f2.py', 'src/f3.py']
    assert artifacts['report']['coverage_status'] == 'COMPLETE'
    assert 'candidates' in artifacts
    assert any(key == f"analyze:{root['key']}:3:0" for key in executed)
    assert len(calls) < ctx['budget']['max_model_calls']
    assert ctx['budget'] == original_budget, 'Dependency discovery must not increase authorized budgets'


@pytest.mark.asyncio
async def test_explicit_three_round_limit_still_pauses_deeper_dependency(monkeypatch):
    api, ctx, _ = await dependency_fixture(monkeypatch)
    ctx['budget']['module_read_rounds'] = 3
    artifacts = {}
    with pytest.raises(api.StageError) as exc:
        await drive(api, ctx, runtime(api), artifacts)
    assert exc.value.category == 'BUDGET_EXHAUSTED'
    assert any(key.startswith('analyze:') for key in artifacts)
    assert 'report' not in artifacts and 'candidates' not in artifacts


@pytest.mark.asyncio
async def test_repeated_requests_and_cycles_finish_on_unique_paths(monkeypatch):
    api, ctx, _ = await dependency_fixture(monkeypatch, cycle=True)
    artifacts, executed = await drive(api, ctx, runtime(api))
    assert artifacts['report']['coverage_status'] == 'COMPLETE'
    assert len(executed) == len(set(executed))
    assert len([key for key in executed if key.startswith('read:')]) == 4
    for module in artifacts['report']['snapshot']['modules']:
        paths = [source['path'] for source in module['sources']]
        assert len(paths) == len(set(paths))
        analyzed = [key for key in executed if key.startswith(f"analyze:{module['key']}:")]
        assert len(analyzed) <= 3


@pytest.mark.asyncio
async def test_existing_first_three_round_checkpoint_inputs_stay_identical(monkeypatch):
    api, ctx, _ = await dependency_fixture(monkeypatch)
    legacy = deepcopy(ctx)
    legacy['budget']['module_read_rounds'] = 3
    artifacts, known, rounds = {}, {}, set()
    for _ in range(100):
        specs = api.plan_steps(ctx, artifacts)
        assert specs == api.plan_steps(legacy, artifacts)
        for spec in specs:
            if spec['key'] in known:
                assert known[spec['key']] == spec
            known[spec['key']] = deepcopy(spec)
        runnable = [s for s in specs if s['key'] not in artifacts
                    and all(k in artifacts for k in s['requires'])]
        assert runnable
        spec = runnable[0]
        if spec['stage'] == 'analyze_module':
            round_index = int(spec['key'].split(':')[2])
            rounds.add(round_index)
            if round_index == 2:
                assert rounds == {0, 1, 2}
                return
        out = await api.execute_stage(spec['stage'],
            {'context': ctx, 'payload': spec['payload'], 'artifacts': artifacts}, runtime(api))
        artifacts[spec['key']] = out['artifact']
    raise AssertionError('Fixture did not exercise the third existing round')
