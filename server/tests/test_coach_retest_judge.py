from types import SimpleNamespace

from app.services import assessment_jobs as jobs
from app.services import study_routing


def test_independent_open_coach_retest_uses_configured_judge(monkeypatch):
    cfg={'model':'base','provider':'fixture','base_url':'https://local.invalid','api_key':'local'}
    monkeypatch.setattr(study_routing,'get_settings',lambda: SimpleNamespace(study_strong_model='reviewer',study_strong_base_url='',study_strong_provider='',study_strong_api_key=''))
    route=jobs.scoring_route([(None,{'qtype':'open','study_practice_id':5})]);assert route=='study_strong'
    assert jobs.scoring_config(cfg,route)=={**cfg,'model':'reviewer'}
    assert jobs.scoring_route([(None,{'qtype':'open','assessment_mode':'PRACTICE'})])=='default'
    assert jobs.scoring_route([(None,{'qtype':'mcq','study_practice_id':5})])=='default'
    assert jobs.scoring_config(cfg,'default') is cfg

def test_absent_strong_config_never_uses_base_for_evidence(monkeypatch):
    monkeypatch.setattr(study_routing,'get_settings',lambda: SimpleNamespace(study_strong_model=''))
    assert not jobs.scoring_config({'model':'flash','api_key':'key'},'study_strong').get('model')
