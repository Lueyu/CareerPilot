"""Transient failures must not erase unresolved compact-repair constraints."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app import project_worker as worker


@pytest.mark.asyncio
async def test_worker_keeps_truncation_repair_after_following_request_timeout(monkeypatch):
    step = SimpleNamespace(error_category='REQUEST_TIMEOUT', error='SECRET_PROVIDER_ERROR',
        attempts=[{'outcome': 'OUTPUT_TRUNCATED', 'error': 'SECRET_RESPONSE'}, {'outcome': 'REQUEST_TIMEOUT'}])
    run = SimpleNamespace(model_config_fingerprint='', budget={})
    project = SimpleNamespace(id=2)
    claim = SimpleNamespace(run_id=1, project_id=2, workspace_id=1, step_id=180,
                            stage='explain_section', payload={'section': 'fixture'})
    captured, completed = [], []

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, model, identity):
            return {worker.ProjectAnalysis: run, worker.Project: project, worker.AnalysisStep: step}[model]

    async def heartbeat(*args): return True
    async def config(*args):
        return {'provider': 'fixture', 'model': 'fixture', 'base_url': 'https://fixture.invalid', 'api_key': 'fixture-only'}, {}, {}
    async def context(*args): return {}, {}
    async def stage(stage_name, inputs, runtime):
        captured.append(deepcopy(inputs))
        return {'artifact': {}, 'usage': {'model_calls': 0}, 'request_meta': {}}
    async def complete(*args): completed.append(True)
    monkeypatch.setattr(worker.jobs, 'heartbeat', heartbeat)
    monkeypatch.setattr(worker.jobs, 'complete_step', complete)
    monkeypatch.setattr(worker, 'runtime_config', config)
    monkeypatch.setattr(worker, 'context_for', context)
    monkeypatch.setattr(worker, 'assert_compatible', lambda *args: None)
    monkeypatch.setattr(worker.analysis, 'execute_stage', stage)
    before = deepcopy(step.__dict__)
    await worker.execute_claim(claim, 'fixture-worker', factory=Session)
    assert captured[0]['repair_diagnostic'], 'A transport timeout must not erase the unresolved prior truncation'
    assert '截断' in captured[0]['repair_diagnostic']
    assert 'SECRET' not in captured[0]['repair_diagnostic']
    assert completed == [True] and step.__dict__ == before


@pytest.mark.parametrize('category', ['REQUEST_TIMEOUT', 'NETWORK_TRANSIENT', 'PROVIDER_5XX', 'RATE_LIMITED', 'WORKER_LOST', 'OUTCOME_UNKNOWN', None])
def test_transient_or_worker_failure_keeps_last_unresolved_truncation(category):
    step = SimpleNamespace(error_category=category, error='SECRET_EXCEPTION', attempts=[
        {'outcome': 'OUTPUT_TRUNCATED', 'request_meta': {'untrusted': 'SECRET_BODY'}}, {'outcome': category}])
    before = deepcopy(step.__dict__)
    message = worker._repair_diagnostic(step)
    assert message and '截断' in message and 'SECRET' not in message
    assert step.__dict__ == before


def test_current_repair_category_takes_priority_without_copying_untrusted_error_fields():
    step = SimpleNamespace(error_category='INVALID_CITATION', error='SECRET_RESPONSE.bad_field',
        attempts=[{'outcome': 'OUTPUT_TRUNCATED'}])
    message = worker._repair_diagnostic(step)
    assert '引用' in message and '截断' in message and 'SECRET' not in message
    assert message.index('引用') < message.index('截断')


def test_schema_failure_then_timeout_keeps_earlier_truncation_constraint():
    step = SimpleNamespace(error_category='REQUEST_TIMEOUT', attempts=[
        {'outcome': 'OUTPUT_TRUNCATED'}, {'outcome': 'INVALID_SCHEMA'}, {'outcome': 'REQUEST_TIMEOUT'}])
    message = worker._repair_diagnostic(step)
    assert 'JSON' in message and '截断' in message
    assert message.index('JSON') < message.index('截断')


@pytest.mark.parametrize('history', [[], None, {'outcome': 'OUTPUT_TRUNCATED'},
    [{'outcome': 'OUTPUT_TRUNCATED'}, {'outcome': 'SUCCEEDED'}, {'outcome': 'REQUEST_TIMEOUT'}]])
def test_no_unresolved_repair_or_completed_history_never_reuses_old_constraints(history):
    step = SimpleNamespace(error_category='REQUEST_TIMEOUT', attempts=history)
    assert worker._repair_diagnostic(step) is None


def test_new_repair_after_success_is_kept_but_older_repair_is_not():
    step = SimpleNamespace(error_category='NETWORK_TRANSIENT', attempts=[
        {'outcome': 'OUTPUT_TRUNCATED'}, {'outcome': 'SUCCEEDED'},
        {'outcome': 'INVALID_CITATION'}, {'outcome': 'NETWORK_TRANSIENT'}])
    message = worker._repair_diagnostic(step)
    assert '引用' in message and '截断' not in message


def test_malformed_or_untrusted_attempt_fields_are_not_copied_to_diagnostic():
    step = SimpleNamespace(error_category=['SECRET_CATEGORY'], error='SECRET_BODY', attempts=[
        {'outcome': 'OUTPUT_TRUNCATED'}, None, 'SECRET_RESPONSE', {'outcome': ['SECRET_FIELD']},
        {'outcome': 'UNKNOWN_UNTRUSTED_FIELD', 'error': 'SECRET_ERROR'}])
    message = worker._repair_diagnostic(step)
    assert message and '截断' in message and 'SECRET' not in message


def test_completed_step_has_no_pending_repair_even_if_old_error_fields_remain():
    step = SimpleNamespace(status='SUCCEEDED', error_category='OUTPUT_TRUNCATED',
                           attempts=[{'outcome': 'OUTPUT_TRUNCATED'}, {'outcome': 'SUCCEEDED'}])
    assert worker._repair_diagnostic(step) is None


def test_unresolved_categories_are_deduplicated_bounded_and_current_first():
    step = SimpleNamespace(error_category='INVALID_SCHEMA', error='SECRET_BODY', attempts=[
        {'outcome': category, 'error': 'SECRET_ERROR'}
        for category in ('OUTPUT_TRUNCATED', 'INVALID_SCHEMA', 'INVALID_CITATION', 'REQUEST_TIMEOUT') * 100])
    before = deepcopy(step.__dict__)
    message = worker._repair_diagnostic(step)
    assert message.count('截断') == message.count('JSON') == message.count('上次模型引用') == 1
    assert message.index('JSON') < message.index('上次模型引用') < message.index('截断')
    assert message.count('上次模型') == 3 and len(message) <= 600 and 'SECRET' not in message
    assert step.__dict__ == before


def test_citation_diagnostic_covers_paths_claim_links_and_fact_boundaries():
    step = SimpleNamespace(error_category='INVALID_CITATION', attempts=[])
    message = worker._repair_diagnostic(step)
    assert all(term in message for term in ('来源', '行号', '依赖路径', 'claim_ids', '事实类型'))
