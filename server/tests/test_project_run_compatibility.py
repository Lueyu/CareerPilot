"""Checkpoint versions must match before planning or consuming persisted outputs."""
import importlib.util
from types import SimpleNamespace

import pytest

from app.services import project_autopilot_engine as engine


def check():
    assert importlib.util.find_spec('app.services.project_run_compatibility'), 'checkpoint compatibility guard is missing'
    from app.services.project_run_compatibility import assert_compatible
    return assert_compatible


def current_run():
    return SimpleNamespace(pipeline_version=engine.PIPELINE_VERSION,
                           prompt_bundle_version=engine.PROMPT_BUNDLE_VERSION)


def provenance():
    return {'pipeline_version':engine.PIPELINE_VERSION,'prompt_bundle_version':engine.PROMPT_BUNDLE_VERSION,
            'schema_version':engine.SCHEMA_VERSION,'safety_policy_version':engine.SAFETY_POLICY_VERSION}


def test_new_run_and_complete_current_checkpoint_versions_are_accepted():
    assert check()(current_run(), {}) is None
    assert check()(current_run(), {'module:one':{'provenance':provenance(),'claims':[]}}) is None


def test_english_run_requires_locale_on_persisted_generated_stages():
    run = current_run()
    run.result_meta = {'generation_language': 'en'}
    assert check()(run, {'resolve': {'provenance': provenance()}}) is None
    for locale in ({}, {'generation_language': 'zh-CN'}):
        with pytest.raises(engine.StageError, match='different language'):
            check()(run, {'module:one': {'provenance': {**provenance(), **locale}}})
    assert check()(run, {'module:one': {'provenance': {**provenance(), 'generation_language': 'en'}}}) is None


@pytest.mark.parametrize('field', ['pipeline_version','prompt_bundle_version'])
def test_run_created_by_old_pipeline_or_prompt_cannot_resume(field):
    run=current_run();setattr(run,field,'older-version')
    with pytest.raises(engine.StageError) as caught:
        check()(run, {})
    assert caught.value.category=='CONFIG_CHANGED' and caught.value.safe_for_user


@pytest.mark.parametrize('field', ['pipeline_version','prompt_bundle_version','schema_version','safety_policy_version'])
def test_current_run_cannot_consume_an_old_successful_stage(field):
    old=provenance();old[field]='old-version-with-secret-content'
    with pytest.raises(engine.StageError) as caught:
        check()(current_run(), {'attacker-controlled-stage-name':{'provenance':old}})
    assert caught.value.category=='CONFIG_CHANGED'
    assert 'secret-content' not in str(caught.value) and 'attacker-controlled' not in str(caught.value)


@pytest.mark.parametrize('artifact', [{}, {'provenance':{}}, {'provenance':None}, None])
def test_unversioned_persisted_outputs_are_not_silently_assumed_compatible(artifact):
    with pytest.raises(engine.StageError) as caught:
        check()(current_run(), {'legacy-checkpoint':artifact})
    assert caught.value.category=='CONFIG_CHANGED'


def test_deployment_prompt_update_invalidates_previously_current_artifacts(monkeypatch):
    run=current_run();artifacts={'module:one':{'provenance':provenance()}}
    check()(run,artifacts)
    monkeypatch.setattr(engine,'PROMPT_BUNDLE_VERSION','future-prompt-version')
    with pytest.raises(engine.StageError):check()(run,artifacts)
