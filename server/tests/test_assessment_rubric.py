import importlib
import json
from copy import deepcopy

import pytest


def api():
    return importlib.import_module('app.services.assessment_rubric')


def legacy():
    return {'max_points': 5.0, 'points': [{'title': 'Mechanism', 'detail': 'Explain retrieval before generation'},
                                         {'title': 'Boundary', 'detail': 'Explain refusal without supporting sources'}]}


def compiled():
    return api().compile_rubric(legacy(), question_id=7, title='Explain grounded answers', skill_ids=[4])


def response(rubric, text='Retrieve sources. Refuse unsupported answers.'):
    return {'question_id': 7, 'answer_revision_id': 'a1', 'rubric_version': rubric['rubric_version'],
            'judge_version': api().JUDGE_VERSION,
            'criterion_scores': [{'criterion_id': c['criterion_id'], 'score': 2, 'max_score': 4,
                'answer_quotes': [text], 'satisfied_anchors': [c['criterion_id'] + ':2'],
                'missing_points': ['The detailed validation method is missing.'],
                'reasoning_summary': 'The mechanism is partly explained.', 'why_not_higher': 'The boundary is incomplete.'}
                for c in rubric['criteria']],
            'critical_errors': [], 'strengths': ['Partial mechanism'], 'improvements': ['Explain the boundary']}


def test_legacy_compilation_is_explicitly_uncalibrated_versioned_and_immutable():
    source = legacy(); before = deepcopy(source)
    rubric = api().compile_rubric(source, question_id=7, title='Explain grounded answers', skill_ids=[4])
    assert source == before
    assert rubric['calibration_status'] == 'LEGACY_UNCALIBRATED'
    assert rubric['rubric_version'] == api().rubric_version(rubric)
    assert sum(c['weight'] for c in rubric['criteria']) == 100
    assert all([a['score'] for a in c['score_anchors']] == [0, 1, 2, 3, 4] for c in rubric['criteria'])
    assert rubric['critical_errors'] == []
    changed = api().compile_rubric(source, question_id=7, title='Another requirement', skill_ids=[4])
    assert changed['question_version'] != rubric['question_version']
    assert changed['rubric_version'] != rubric['rubric_version']


@pytest.mark.parametrize('bad', [{}, {'points': []}, {'points': [{'title': 'same'}, {'title': 'same'}]},
                                  {'points': [{'title': 'x'}], 'max_points': float('nan')}])
def test_missing_or_ambiguous_rubric_never_falls_back_to_generic_standard(bad):
    with pytest.raises(api().AssessmentJudgeError) as caught:
        api().compile_rubric(bad, question_id=7, title='Question')
    assert caught.value.category == 'INVALID_RUBRIC'


def test_deterministic_score_preserves_partial_credit_and_applies_only_predefined_caps():
    rubric = compiled()
    rubric['criteria'][0]['weight'], rubric['criteria'][1]['weight'] = 75, 25
    rubric['critical_errors'] = [{'error_id': 'E1', 'description': 'Reverses the required sequence',
                                 'criterion_id': rubric['criteria'][0]['criterion_id'], 'cap_score': 1, 'cap_overall': 40}]
    rubric['rubric_version'] = api().rubric_version(rubric)
    raw = response(rubric)
    result = api().validate_judgement(raw, rubric=rubric, text=raw['criterion_scores'][0]['answer_quotes'][0],
                                    question_id=7, answer_revision_id='a1')
    assert result['overall_score'] == 50 and result['points'] == 2.5
    raw['critical_errors'] = [{'error_id': 'E1', 'answer_quotes': ['Retrieve sources.'], 'reasoning_summary': 'Fixture detected error'}]
    result = api().validate_judgement(raw, rubric=rubric, text='Retrieve sources. Refuse unsupported answers.',
                                    question_id=7, answer_revision_id='a1')
    assert result['overall_score'] == 31.25
    assert result['points'] == 1.5625
    assert result['points'] / result['max_points'] * 100 == result['overall_score']
    assert result['criterion_scores'][0]['score'] == 1 and result['criterion_scores'][0]['judge_score'] == 2
    assert result['missing'] == ['Mechanism', 'Boundary']


@pytest.mark.parametrize('mutation,category', [
    ('no_quote', 'INVALID_QUOTE'), ('invented_quote', 'INVALID_QUOTE'), ('missing_criterion', 'INVALID_SCHEMA'),
    ('duplicate_criterion', 'INVALID_SCHEMA'), ('bad_anchor', 'INVALID_SCHEMA'), ('bad_id', 'INVALID_SCHEMA'),
    ('new_cap', 'INVALID_SCHEMA'), ('numeric_confidence', 'INVALID_SCHEMA'), ('invented_total', 'INVALID_SCHEMA')])
def test_judge_rejects_unsupported_or_malformed_assessment(mutation, category):
    rubric = compiled(); raw = response(rubric)
    if mutation == 'no_quote': raw['criterion_scores'][0]['answer_quotes'] = []
    elif mutation == 'invented_quote': raw['criterion_scores'][0]['answer_quotes'] = ['The reference says this.']
    elif mutation == 'missing_criterion': raw['criterion_scores'].pop()
    elif mutation == 'duplicate_criterion': raw['criterion_scores'][1] = deepcopy(raw['criterion_scores'][0])
    elif mutation == 'bad_anchor': raw['criterion_scores'][0]['satisfied_anchors'] = ['c1:4']
    elif mutation == 'bad_id': raw['answer_revision_id'] = 'other-answer'
    elif mutation == 'new_cap': raw['critical_errors'] = [{'error_id': 'invented', 'answer_quotes': ['Retrieve sources.'], 'reasoning_summary': 'bad'}]
    elif mutation == 'numeric_confidence': raw['confidence'] = .99
    elif mutation == 'invented_total': raw['overall_score'] = 100
    with pytest.raises(api().AssessmentJudgeError) as caught:
        api().validate_judgement(raw, rubric=rubric, text='Retrieve sources. Refuse unsupported answers.', question_id=7, answer_revision_id='a1')
    assert caught.value.category == category


def test_uncalibrated_is_a_quality_label_not_a_probability_or_automatic_zero():
    rubric = compiled(); raw = response(rubric)
    result = api().validate_judgement(raw, rubric=rubric, text='Retrieve sources. Refuse unsupported answers.', question_id=7, answer_revision_id='a1')
    assert result['confidence_status'] == 'UNCALIBRATED'
    assert result['needs_human_review'] and result['validation_status'] == 'VALID'
    assert result['points'] > 0 and 'confidence' not in result


@pytest.mark.asyncio
async def test_strict_judge_keeps_full_answer_and_returns_typed_schema_failure_with_private_response():
    from app.services.llm_judge import judge_frozen_answer
    rubric = compiled(); text = 'x' * 2100 + ' exact tail'
    class Adapter:
        last_response_meta = {'request_id': 'fixture-request', 'finish_reason': 'stop'}
        last_usage = None
        async def chat(self, system, user, **kwargs):
            payload = json.loads(user)
            assert payload['answer'] == text
            assert kwargs['timeout'] == 90.0 and 'reasoning_effort' not in kwargs
            return {'content': '{"wrong":true}', 'usage': {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14}}
    with pytest.raises(api().AssessmentJudgeError) as caught:
        await judge_frozen_answer(Adapter(), question_id=7, answer_revision_id='a1', title='Question', skill_name='RAG', rubric=rubric, text=text)
    err = caught.value
    assert err.category == 'INVALID_SCHEMA' and err.raw_response == '{"wrong":true}'
    assert err.usage['total_tokens'] == 14 and err.request_meta['request_id'] == 'fixture-request'
    assert err.request_meta['input_chars'] > len(text)
    assert 'wrong' not in str(err) and 'wrong' not in repr(err)


@pytest.mark.asyncio
async def test_legacy_reference_is_explicitly_not_sent_but_full_scoring_standard_is_kept():
    from app.services.llm_judge import judge_frozen_answer
    rubric = api().compile_rubric(legacy(), question_id=7, title='Question', reference_answer='legacy-reference-marker ' * 6000)
    class Adapter:
        last_response_meta = {}
        async def chat(self, system, user, **kwargs):
            payload = json.loads(user)
            assert 'legacy-reference-marker' not in user
            assert payload['reference_input_policy'] == 'frozen_criteria_authoritative_legacy_reference_not_sent'
            assert payload['rubric']['criteria'] == rubric['criteria']
            return {'content': json.dumps(response(rubric))}
    result = await judge_frozen_answer(Adapter(), question_id=7, answer_revision_id='a1', title='Question', skill_name='RAG', rubric=rubric, text='Retrieve sources. Refuse unsupported answers.')
    assert result['validation_status'] == 'VALID'
    assert result['request_meta']['prompt_sha256'] and result['request_meta']['prompt_version']


@pytest.mark.asyncio
async def test_rate_limit_retains_safe_adapter_error_retry_metadata():
    from app.services.llm_judge import judge_frozen_answer
    class Adapter:
        last_response_meta = {}
        last_error = {'kind': 'http', 'status': 429, 'retry_after': 17, 'request_id': 'fixture-rate-limit'}
        async def chat(self, *args, **kwargs): return None
    with pytest.raises(api().AssessmentJudgeError) as caught:
        await judge_frozen_answer(Adapter(), question_id=7, answer_revision_id='a1', title='Question', skill_name='RAG', rubric=compiled(), text='Answer')
    assert caught.value.category == 'RATE_LIMITED'
    assert caught.value.request_meta['retry_after'] == 17
    assert caught.value.request_meta['request_id'] == 'fixture-rate-limit'


def test_question_specific_candidates_and_required_sample_types_are_not_claimed_as_gold():
    from pathlib import Path
    data = json.loads((Path(__file__).resolve().parents[2] / 'docs/assessment/rubric-candidates.json').read_text(encoding='utf8'))
    assert data['human_reviewed'] is False and data['is_gold_standard'] is False
    assert {q['question_id'] for q in data['questions']} == {1, 13, 53}
    weights = []
    for q in data['questions']:
        rubric = api().validate_rubric(q['rubric'])
        assert rubric['calibration_status'] == 'AI_DRAFT'
        assert len(rubric['criteria']) == 5
        weights.append(tuple(c['weight'] for c in rubric['criteria']))
        sections = rubric['reference_standard']['sections']
        assert len(sections) >= 10
        assert {cid for section in sections for cid in section['criterion_ids']} == {c['criterion_id'] for c in rubric['criteria']}
        assert len(q['calibration_candidates']) == 13
        by_kind = {sample['kind']: sample for sample in q['calibration_candidates']}
        assert {'blank', 'core_correct_missing_boundary', 'reasonable_alternative'} <= set(by_kind)
        assert by_kind['blank']['answer'] == ''
        assert by_kind['core_correct_missing_boundary']['answer'].strip()
        assert by_kind['reasonable_alternative']['answer'].strip()
        assert by_kind['reasonable_alternative']['answer'] != by_kind['adequate_candidate']['answer']
        assert all(x['expected_status'] == 'PENDING_HUMAN_REVIEW' for x in by_kind.values())
        assert all(x['human_reviewed'] is False and x['expected_score'] is None for x in q['calibration_candidates'])
    assert len(set(weights)) == 3, 'Question objectives determine weights, not a universal template'


@pytest.mark.parametrize('mutation', ['weights', 'anchor', 'reference_ids', 'reference_shape', 'cap_target'])
def test_invalid_frozen_rubric_cannot_become_a_valid_standard(mutation):
    rubric = compiled()
    if mutation == 'weights': rubric['criteria'][0]['weight'] += 1
    elif mutation == 'anchor': rubric['criteria'][0]['score_anchors'][3]['anchor_id'] = 'unknown:3'
    elif mutation == 'reference_ids': rubric['reference_standard']['sections'] = [{'text': 'Reference', 'criterion_ids': [['nested']]}]
    elif mutation == 'reference_shape': rubric['reference_standard']['sections'] = None
    elif mutation == 'cap_target': rubric['critical_errors'] = [{'error_id': 'E1', 'description': 'bad', 'criterion_id': 'unknown', 'cap_score': 1}]
    rubric['rubric_version'] = api().rubric_version(rubric)
    with pytest.raises(api().AssessmentJudgeError) as caught:
        api().validate_rubric(rubric)
    assert caught.value.category == 'INVALID_RUBRIC'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['duplicate_json', 'truncated', 'timeout'])
async def test_strict_failure_keeps_safe_attempt_metadata_and_no_valid_score(kind):
    import asyncio
    from app.services.llm_judge import judge_frozen_answer
    class Adapter:
        last_response_meta = {'request_id': 'fixture-1', 'finish_reason': 'length' if kind == 'truncated' else 'stop'}
        async def chat(self, *args, **kwargs):
            if kind == 'timeout':
                await asyncio.sleep(1)
            return {'content': '{"question_id":7,"question_id":8}', 'usage': {'completion_tokens': 9}}
    expected = {'duplicate_json': 'INVALID_JSON', 'truncated': 'OUTPUT_TRUNCATED', 'timeout': 'REQUEST_TIMEOUT'}[kind]
    with pytest.raises(api().AssessmentJudgeError) as caught:
        await judge_frozen_answer(Adapter(), question_id=7, answer_revision_id='a1', title='Question', skill_name='RAG', rubric=compiled(), text='Answer', timeout=.001 if kind == 'timeout' else 90)
    assert caught.value.category == expected
    assert caught.value.request_meta['request_started']
    assert caught.value.request_meta['elapsed_ms'] >= 0
    if kind != 'timeout': assert caught.value.usage['completion_tokens'] == 9
