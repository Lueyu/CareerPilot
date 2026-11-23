"""Document evidence must not become a code fact when stages derive reports."""
import json
from copy import deepcopy

import pytest

from app.services import project_autopilot_engine as engine

SHA = 'a' * 40


def fixture_inputs():
    context = {'workspace_id': 1, 'project_id': 2, 'project': {'id': 2}, 'mode': 'full',
               'model_config_fingerprint': 'fixture-model'}
    module = {'key': 'm1', 'name': 'Documentation and implementation', 'purpose': 'Inspect supplied evidence',
              'paths': ['docs/design.md', 'src/app.py']}
    artifacts = {'resolve': {'target_sha': SHA}, 'manifest': {'entries': [], 'complete': True}}
    for number, path in enumerate(module['paths'], 1):
        _, category, _ = engine.sources.classify_path(path, 'blob', '100644')
        source = {'source_id': f's{number}', 'source_revision': SHA, 'path': path, 'category': category,
                  'content': 'The document states that tests passed.' if category == 'documentation' else 'return value',
                  'blob_oid': str(number) * 40}
        artifacts[engine._read_key(path)] = {'source': source, 'chunks': engine._split_source(source)}
        artifacts['manifest']['entries'].append({'path': path, 'included': True, 'category': category,
                                                 'blob_oid': source['blob_oid'], 'mode': '100644'})
    runtime = engine.RuntimeContext(1, 2, {'provider': 'fixture', 'model': 'fixture', 'base_url': 'https://fixture.invalid',
                                         'api_key': 'not-a-real-credential'}, {})
    return context, module, artifacts, runtime


def ref(number=1):
    return {'source_id': f's{number}', 'start_line': 1, 'end_line': 1,
            'path': 'docs/design.md' if number == 1 else 'src/app.py', 'source_version': SHA}


def claim(cid='C1', kind='CODE_OBSERVED', citations=None):
    return {'id': cid, 'title': 'Document verification statement', 'text': 'The document states that tests passed; no tests ran here.',
            'kind': kind, 'citations': [ref()] if citations is None else citations}


def part(claims):
    return {'claims': claims, 'issues': [], 'unknowns': [], 'dependencies': [], 'dependency_scope_complete': True}


async def assemble_module(claims, customize=None, previous_adjustments=None):
    context, module, artifacts, runtime = fixture_inputs()
    artifacts['analyze:m1'] = part(claims)
    if previous_adjustments is not None:
        artifacts['analyze:m1']['boundary_adjustments'] = previous_adjustments
    if customize:
        customize(artifacts)
    before = deepcopy(artifacts)
    result = await engine.execute_stage('module', {'context': context, 'artifacts': artifacts, 'payload': {
        'module': module, 'analysis_keys': ['analyze:m1'], 'read_paths': module['paths']}}, runtime)
    assert artifacts == before, 'Deriving a module must not mutate successful analysis/read checkpoints'
    return result['artifact']


@pytest.mark.asyncio
async def test_new_analysis_downgrades_model_code_claim_and_observed_issue_with_document_only_refs(monkeypatch):
    context, module, artifacts, runtime = fixture_inputs()
    citation = {k: ref()[k] for k in ('source_id', 'start_line', 'end_line')}
    response = {'claims': [{**claim(), 'citations': [citation]}], 'issues': [{
        'title': 'Unverified document statement', 'skill_key': 'reliability', 'severity': 'low',
        'detail': 'The supplied document reports tests passed; execution evidence was not supplied.',
        'impact': 'Readers could confuse a document statement with reproduced results.',
        'recommendation': 'Keep the statement attributed to documentation.', 'status': 'observed',
        'citations': [citation], 'interview_questions': ['What evidence would establish an executed test result?']}],
        'dependencies': [], 'dependency_scope_complete': True, 'unknowns': [], 'read_requests': []}
    calls = []
    async def fake_chat(self, cfg, system, user, **kwargs):
        calls.append(json.loads(user)['task'])
        return {'content': json.dumps(response)}
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', fake_chat)
    before = deepcopy(artifacts)
    source = artifacts[engine._read_key('docs/design.md')]['chunks'][0]
    selected = {k: source[k] for k in ('path', 'source_id', 'source_version', 'start_line', 'end_line')}
    result = await engine.execute_stage('analyze_module', {'context': context, 'artifacts': artifacts,
        'payload': {'module': module, 'sources': [selected], 'analysis_key': 'analyze:m1'}}, runtime)
    output = result['artifact']
    assert [c['kind'] for c in output['claims']] == ['DOC_CLAIM', 'DOC_CLAIM']
    assert {a['claim_id'] for a in output['boundary_adjustments']} == {'analyze:m1:C1', 'analyze:m1:issue0'}
    assert all(a['from'] == 'CODE_OBSERVED' and a['to'] == 'DOC_CLAIM' for a in output['boundary_adjustments'])
    assert output['issues'][0]['status'] == 'observed', 'A label correction must not invent a new model judgement'
    assert artifacts == before and response['claims'][0]['kind'] == 'CODE_OBSERVED'
    assert calls == ['analyze_module']


@pytest.mark.asyncio
async def test_module_normalizes_saved_document_claims_without_rewriting_old_artifacts():
    output = await assemble_module([claim('old:issue0'), claim('old:inference', 'INFERRED')])
    assert [c['kind'] for c in output['claims']] == ['DOC_CLAIM', 'INFERRED']
    assert output['boundary_adjustments'] == [{
        'claim_id': 'old:issue0', 'from': 'CODE_OBSERVED', 'to': 'DOC_CLAIM',
        'reason': 'all_citations_are_documentation',
        'sources': [{'source_id': 's1', 'source_version': SHA, 'path': 'docs/design.md'}]}]


@pytest.mark.asyncio
async def test_module_keeps_previously_recorded_boundary_adjustments_without_duplication():
    previous = [{'claim_id': 'old:issue0', 'from': 'CODE_OBSERVED', 'to': 'DOC_CLAIM',
                 'reason': 'all_citations_are_documentation',
                 'sources': [{'source_id': 's1', 'source_version': SHA, 'path': 'docs/design.md'}]}]
    output = await assemble_module([claim('old:issue0', 'DOC_CLAIM')], previous_adjustments=previous)
    assert output.get('boundary_adjustments') == previous


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['code_only', 'mixed', 'no_refs', 'missing_category', 'wrong_version', 'wrong_path', 'inferred', 'unknown', 'already_document'])
async def test_module_never_guesses_documentation_from_partial_unknown_or_mixed_evidence(case):
    citations = [ref()]
    kind = 'CODE_OBSERVED'
    customize = None
    if case == 'code_only': citations = [ref(2)]
    elif case == 'mixed': citations = [ref(), ref(2)]
    elif case == 'no_refs': citations = []
    elif case == 'missing_category':
        def customize(artifacts):
            return artifacts[engine._read_key('docs/design.md')]['source'].pop('category')
    elif case == 'wrong_version': citations[0]['source_version'] = 'b' * 40
    elif case == 'wrong_path': citations[0]['path'] = 'docs/unrelated.md'
    elif case == 'inferred': kind = 'INFERRED'
    elif case == 'unknown': kind, citations = 'UNASSESSED', []
    elif case == 'already_document': kind = 'DOC_CLAIM'
    output = await assemble_module([claim(kind=kind, citations=citations)], customize=customize)
    assert output['claims'][0]['kind'] == kind
    assert not output.get('boundary_adjustments')


@pytest.mark.asyncio
async def test_incremental_reuse_normalizes_document_fact_from_old_module_without_rewriting_checkpoint(monkeypatch):
    context, module, artifacts, runtime = fixture_inputs()
    old = {**module, 'module_key': module['key'], 'analyzed_sha': SHA, 'coverage_status': 'COMPLETE',
           'claims': [claim('old:document')], 'issues': [], 'unknowns': [], 'dependencies': [],
           'dependency_scope_complete': True,
           'sources': [artifacts[engine._read_key(path)]['source'] for path in module['paths']],
           'provenance': engine._provenance(context)}
    old['fingerprint'] = engine._module_fingerprint(context, artifacts['manifest'], module, [])
    target = 'b' * 40
    manifest = {**deepcopy(artifacts['manifest']), 'diff': {'complete': True, 'requires_full': False, 'changes': []}}
    incremental = {**context, 'mode': 'incremental', 'base_sha': SHA, 'target_sha': target,
        'baseline': {'snapshot': {'modules': [old], 'manifest': deepcopy(artifacts['manifest'])}}}
    stages = {'resolve': {'target_sha': target}, 'manifest': manifest}
    before_context, before_stages = deepcopy(incremental), deepcopy(stages)

    async def no_model_or_source_call(*args, **kwargs):
        raise AssertionError('A compatible module reuse must not call the model or source provider')
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', no_model_or_source_call)
    monkeypatch.setattr(engine.sources, 'read_source', no_model_or_source_call)

    # Use the production compatibility check and execute_stage branch, not a mocked helper.
    assert engine._reusable_module(incremental, manifest, module) is old
    result = await engine.execute_stage('reuse_module', {'context': incremental, 'artifacts': stages,
        'payload': {'module': module}}, runtime)
    derived = result['artifact']
    assert derived['claims'][0]['kind'] == 'DOC_CLAIM'
    assert derived['boundary_adjustments'][0]['claim_id'] == 'old:document'
    assert derived['compatibility']['target_sha'] == target
    assert derived['compatibility']['reused_from_sha'] == SHA
    assert derived['sources'][0]['source_revision'] == SHA
    assert result['usage']['model_calls'] == 0
    assert incremental == before_context and stages == before_stages
    assert old['claims'][0]['kind'] == 'CODE_OBSERVED' and 'boundary_adjustments' not in old
