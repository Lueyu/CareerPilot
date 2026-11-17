"""A sourced independent review can quarantine a claim without rewriting history."""
from copy import deepcopy

import pytest

from app.services import project_autopilot_engine as api

SHA = 'a' * 40


def data():
    ref = {'source_id': 's1', 'path': 'main.py', 'source_version': SHA,
           'start_line': 1, 'end_line': 2}
    original = {'claims': [
        {'id': 'disputed', 'title': 'Original model claim', 'text': 'Original text',
         'kind': 'CODE_OBSERVED', 'citations': [ref]},
        {'id': 'supported', 'title': 'Another claim', 'text': 'Another text',
         'kind': 'CODE_OBSERVED', 'citations': [ref]}],
        'issues': [{'claim_id': 'disputed'}, {'claim_id': 'supported'}], 'unknowns': [],
        'sources': [{'source_id': 's1', 'path': 'main.py', 'source_revision': SHA, 'line_count': 20}]}
    review = {'review_id': 'review-1', 'claim_id': 'disputed', 'action': 'QUARANTINE',
        'workspace_id': 1, 'project_id': 2, 'target_sha': SHA,
        'reason': 'Independent source comparison contradicts this claim.',
        'reviewer': 'independent_source_audit', 'citations': [ref]}
    return original, {'workspace_id': 1, 'project_id': 2, 'claim_reviews': [review]}


def test_quarantine_retains_original_claim_but_excludes_it_from_synthesis():
    original, ctx = data()
    before = deepcopy(original)
    result = api._apply_claim_reviews(original, ctx, SHA)
    assert original == before
    assert [c['id'] for c in result['claims']] == ['supported']
    assert result['issues'] == [{'claim_id': 'supported'}]
    assert result['quarantined_claims'][0]['original_claim'] == before['claims'][0]
    assert result['quarantined_claims'][0]['review'] == ctx['claim_reviews'][0]
    assert 'review-1' in result['unknowns'][0]
    assert result['claim_review_fingerprint']
    assert api._apply_claim_reviews(result, ctx, SHA) == result


@pytest.mark.parametrize('change', [
    {'workspace_id': 9}, {'project_id': 9}, {'target_sha': 'b' * 40},
    {'citations': []}, {'citations': [{'source_id': 's1', 'path': 'main.py',
        'source_version': SHA, 'start_line': 19, 'end_line': 25}]},
    {'citations': [{'source_id': 's1', 'path': 'main.py',
        'source_version': 'b' * 40, 'start_line': 1, 'end_line': 2}]},
])
def test_review_must_match_current_scope_and_actually_read_source(change):
    original, ctx = data()
    ctx['claim_reviews'][0].update(change)
    with pytest.raises(api.StageError) as exc:
        api._apply_claim_reviews(original, ctx, SHA)
    assert exc.value.category == 'INVALID_CITATION'


def test_unrelated_reviews_and_absent_reviews_do_not_change_other_modules():
    original, ctx = data()
    ctx['claim_reviews'][0]['claim_id'] = 'another-module-claim'
    assert api._apply_claim_reviews(original, ctx, SHA) == original
    ctx['claim_reviews'] = []
    assert api._apply_claim_reviews(original, ctx, SHA) == original


@pytest.mark.asyncio
async def test_reviewed_claim_cannot_reappear_in_report_or_memory_generation(monkeypatch):
    from test_project_autopilot_engine import fixture_pipeline, drive, context, runtime
    engine, _ = await fixture_pipeline(monkeypatch, count=2)
    ctx = context()
    artifacts, _ = await drive(engine, ctx, runtime(engine))
    raw_key = next(k for k in artifacts if k.startswith('analyze:'))
    raw = artifacts[raw_key]
    raw['claims'].append({**deepcopy(raw['claims'][0]), 'id': raw['claims'][0]['id'] + '-keep'})
    retained = {k: deepcopy(v) for k, v in artifacts.items()
                if k.split(':')[0] not in {'module', 'cross', 'section', 'candidate-part', 'candidates', 'report'}}
    frozen = deepcopy(retained)
    bad = raw['claims'][0]
    ctx['claim_reviews'] = [{'review_id': 'fixture-review', 'claim_id': bad['id'],
        'action': 'QUARANTINE', 'workspace_id': 1, 'project_id': 2, 'target_sha': SHA,
        'reason': 'Source contradicts this reviewed claim.', 'reviewer': 'independent_source_audit',
        'citations': bad['citations']}]
    captured = []
    original_chat = engine.AgentAdapter.chat_with
    async def inspect(self, cfg, system, user, **kw):
        import json
        request = json.loads(user)
        if 'claims' in request:
            captured.append(request)
            assert bad['id'] not in {c['id'] for c in request['claims']}
        return await original_chat(self, cfg, system, user, **kw)
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', inspect)
    completed, _ = await drive(engine, ctx, runtime(engine), retained)
    report = completed['report']['report']
    assert completed['report']['coverage_status'] == 'COMPLETE'
    assert bad['id'] not in {c['id'] for c in report['claims']}
    assert report['quarantined_claims'][0]['original_claim'] == bad
    assert any('fixture-review' in p['text'] for s in report['sections']
               if s['id'] == 'limitations' for p in s['paragraphs'])
    assert all(completed[k] == v for k, v in frozen.items())
    assert any(r['task'] == 'extract_memory_candidates' for r in captured)
