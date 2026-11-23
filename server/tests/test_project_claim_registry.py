"""Optional lossless downstream encoding; no live model or source database."""
import importlib
import importlib.util
import json
from copy import deepcopy

import pytest
from test_project_autopilot_engine import context, drive, fixture_pipeline, runtime

CONTRACT = 'source-registry-v1'


def codec():
    name = 'app.services.project_claim_registry'
    assert importlib.util.find_spec(name), 'The opt-in lossless claim codec is missing'
    return importlib.import_module(name)


def sample_claims():
    ref = {'source_id': 's' + '1' * 48, 'source_version': 'a' * 40,
           'path': '项目/README.md', 'start_line': 1, 'end_line': 2}
    return [
        {'id': 'claim-a', 'title': '文档声明', 'text': '中文与 emoji 🧪，不改正文。',
         'kind': 'DOC_CLAIM', 'citations': [ref, {**ref, 'start_line': 4, 'end_line': 5}]},
        {'id': 'claim-b', 'title': '另一个提交', 'text': '相同路径、不同版本必须分开。',
         'kind': 'INFERRED', 'citations': [{**ref, 'source_version': 'b' * 40}]},
    ]


def test_registry_roundtrip_preserves_every_claim_and_reference_field():
    api = codec()
    original = sample_claims()
    frozen = deepcopy(original)
    encoded = api.encode_claims(original)
    assert encoded['input_contract_version'] == CONTRACT
    assert len(encoded['source_registry']) == 2
    assert api.decode_claims(encoded) == original == frozen
    assert encoded['claims'][0]['citations'][0]['source_ref'] == encoded['claims'][0]['citations'][1]['source_ref']
    assert encoded['claims'][1]['citations'][0]['source_ref'] != encoded['claims'][0]['citations'][0]['source_ref']
    broken = deepcopy(encoded)
    broken['claims'][0]['citations'][0]['source_ref'] = 'missing'
    with pytest.raises(ValueError):
        api.decode_claims(broken)


def test_group_budget_counts_full_registry_and_roundtrips_without_truncation():
    api = codec()
    claims = [deepcopy(sample_claims()[0]) for _ in range(8)]
    for i, claim in enumerate(claims):
        claim['id'] = f'c{i}'
        for ref in claim['citations']:
            ref['source_id'] = f's{i}'
            ref['path'] = ('long-directory/' * 12) + str(i) + '.py'
    def size(value):
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    limit = max(size(api.encode_claims([c])) for c in claims) + 15
    groups = api.group_claims(claims, limit)
    assert len(groups) == len(claims), 'Registry bytes must participate in splitting'
    assert all(size(api.encode_claims(group)) <= limit for group in groups)
    assert [c for group in groups for c in api.decode_claims(api.encode_claims(group))] == claims
    with pytest.raises(ValueError):
        api.group_claims(claims, 20)


@pytest.mark.asyncio
async def test_opt_in_keys_and_requests_preserve_old_module_checkpoints(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 3)
    ctx = context()
    legacy, _ = await drive(api, ctx, runtime(api))
    retained = {k: deepcopy(v) for k, v in legacy.items()
                if not k.startswith(('cross:', 'section:', 'candidate-part:')) and k not in ('report', 'candidates')}
    frozen = deepcopy(retained)
    previous = api.AgentAdapter.chat_with
    observed = []

    async def inspect(self, cfg, system, user, **kwargs):
        request = json.loads(user)
        if request['task'] in ('verify_cross_module', 'explain_project_section', 'extract_memory_candidates'):
            assert request['input_contract_version'] == CONTRACT
            assert 'source_registry' in request and 'source_ref' in system
            original = codec().decode_claims(request)
            assert all(c['id'].startswith('analyze:') for c in original)
            observed.append((request, system))
        return await previous(self, cfg, system, user, **kwargs)

    monkeypatch.setattr(api.AgentAdapter, 'chat_with', inspect)
    opt = {**ctx, 'claim_input_contract': CONTRACT}
    output, executed = await drive(api, opt, runtime(api), retained)
    assert all(output[k] == v for k, v in frozen.items())
    assert any(k.startswith('cross:' + CONTRACT + ':') for k in executed)
    assert any(k.startswith('section:' + CONTRACT + ':') for k in executed)
    assert any(k.startswith('candidate-part:' + CONTRACT + ':') for k in executed)
    assert observed and output['report']['coverage_status'] == 'COMPLETE'
    for request, system in observed:
        assert len(system) + len(json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) <= 40000
        assert request['prompt_version'] == api.PROMPT_BUNDLE_VERSION
        assert request['task_prompt_version'] == CONTRACT
    default_specs = api.plan_steps(ctx, legacy)
    explicit_legacy = api.plan_steps({**ctx, 'claim_input_contract': 'legacy'}, legacy)
    assert default_specs == explicit_legacy
    assert not any(CONTRACT in s['key'] or 'input_contract_version' in s['payload'] for s in default_specs)


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_kind', ['foreign_claim', 'promoted_document'])
async def test_candidate_outputs_validate_against_original_claims(monkeypatch, bad_kind):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    ctx = {**context(), 'claim_input_contract': CONTRACT}
    if bad_kind == 'promoted_document':
        valid_chat = api.AgentAdapter.chat_with
        async def document_fixture(self, cfg, system, user, **kwargs):
            result = await valid_chat(self, cfg, system, user, **kwargs)
            request, value = json.loads(user), json.loads(result['content'])
            if request['task'] == 'analyze_module':
                for claim in value['claims']: claim['kind'] = 'DOC_CLAIM'
            if request['task'] == 'extract_memory_candidates':
                for candidate in value['candidates']: candidate['fact_type'] = 'DOC_CLAIM'
            return {**result, 'content': json.dumps(value)}
        monkeypatch.setattr(api.AgentAdapter, 'chat_with', document_fixture)
    artifacts, _ = await drive(api, ctx, runtime(api))
    spec = next(s for s in api.plan_steps(ctx, artifacts) if s['stage'] == 'extract_candidates')
    original_claim = artifacts['report']['report']['claims'][0]
    async def invalid(self, cfg, system, user, **kwargs):
        request = json.loads(user)
        assert request['input_contract_version'] == CONTRACT
        assert codec().decode_claims(request)[0] == original_claim
        return {'content': json.dumps({'candidates': [{'fact_key': 'entry', 'kind': 'architecture',
            'content': 'Fixture', 'claim_ids': ['not-supplied' if bad_kind == 'foreign_claim' else original_claim['id']],
            'fact_type': 'CODE_OBSERVED'}]})}
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', invalid)
    with pytest.raises(api.StageError) as exc:
        await api.execute_stage('extract_candidates', {'context': ctx, 'payload': spec['payload'], 'artifacts': artifacts}, runtime(api))
    assert exc.value.category == 'INVALID_CITATION'


@pytest.mark.asyncio
async def test_registry_requires_explicit_context_and_rejects_changed_frozen_input(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    ctx = {**context(), 'claim_input_contract': CONTRACT}
    artifacts, _ = await drive(api, ctx, runtime(api))
    spec = next(s for s in api.plan_steps(ctx, artifacts) if s['stage'] == 'extract_candidates')
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('Invalid contract must fail before any provider call')
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', forbidden)
    with pytest.raises(api.StageError) as exc:
        await api.execute_stage('extract_candidates', {'context': context(), 'payload': spec['payload'], 'artifacts': artifacts}, runtime(api))
    assert exc.value.category == 'CONFIG_CHANGED'
    artifacts['report']['report']['claims'][0]['text'] += ' altered'
    with pytest.raises(api.StageError) as exc:
        await api.execute_stage('extract_candidates', {'context': ctx, 'payload': spec['payload'], 'artifacts': artifacts}, runtime(api))
    assert exc.value.category == 'CONFIG_CHANGED' and calls == []


@pytest.mark.asyncio
async def test_full_request_budget_includes_codec_instruction_and_schema(monkeypatch):
    api, _ = await fixture_pipeline(monkeypatch, 1)
    ctx = {**context(), 'claim_input_contract': CONTRACT}
    artifacts, _ = await drive(api, ctx, runtime(api))
    spec = next(s for s in api.plan_steps(ctx, artifacts) if s['stage'] == 'cross_module')
    raw = [c for m in spec['payload']['module_keys'] for c in artifacts[m]['claims']]
    rt = runtime(api)
    rt.input_chars = len(json.dumps(codec().encode_claims(raw), ensure_ascii=False)) + 10
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError('Full request exceeds the retained runtime budget')
    monkeypatch.setattr(api.AgentAdapter, 'chat_with', forbidden)
    with pytest.raises(api.StageError) as exc:
        await api.execute_stage('cross_module', {'context': ctx, 'payload': spec['payload'], 'artifacts': artifacts}, rt)
    assert exc.value.category == 'INPUT_TOO_LARGE'
    assert exc.value.request_meta['input_chars'] > rt.input_chars
    assert exc.value.request_meta['task_prompt_version'] == CONTRACT
    assert exc.value.request_meta['request_started'] is False and calls == []
