"""Reject incompatible durable checkpoints before planning or using their outputs."""
from collections.abc import Mapping

from app.generation_i18n import saved_language
from app.services import project_autopilot_engine as engine
from app.services.project_report_i18n import report_message


def assert_compatible(run, artifacts):
    """A deployment must not silently mix old checkpoints with current rules.

    The run pins pipeline and prompt versions. Each successful stage additionally
    pins its schema and source-safety policy. Missing provenance is unknown, not
    permission to reuse an old result. No payload or attacker-controlled version
    strings are included in the user-facing error.
    """
    expected = {
        'pipeline_version': engine.PIPELINE_VERSION,
        'prompt_bundle_version': engine.PROMPT_BUNDLE_VERSION,
        'schema_version': engine.SCHEMA_VERSION,
        'safety_policy_version': engine.SAFETY_POLICY_VERSION,
    }

    def reject():
        raise engine.StageError(
            'CONFIG_CHANGED',
            '分析流程、提示词、结果结构或源码过滤规则已变化，或旧检查点缺少版本记录。'
            '已保存的历史进度保留；请取消旧任务并完整重新分析，不能混用不同版本的结果。',
        )

    if any(getattr(run, field, None) != expected[field]
           for field in ('pipeline_version', 'prompt_bundle_version')):
        reject()
    if not isinstance(artifacts, Mapping):
        reject()
    run_meta = getattr(run, 'result_meta', None) or {}
    language = saved_language(run_meta.get('generation_language'))
    for key, artifact in artifacts.items():
        provenance = artifact.get('provenance') if isinstance(artifact, Mapping) else None
        if not isinstance(provenance, Mapping) or any(provenance.get(field) != value
                                                     for field, value in expected.items()):
            reject()
        generated = key in {'manifest', 'impact', 'report', 'candidates'} or key.startswith(
            ('plan:', 'analyze:', 'merge:', 'module:', 'cross:', 'section:', 'candidate-part:'))
        if generated and saved_language(provenance.get('generation_language')) != language:
            raise engine.StageError('CONFIG_CHANGED', report_message('checkpoint.language_changed', language))
    # Both Worker entry points call this before consuming/publishing outputs.
    # An opt-in must never relabel old downstream outputs, including already
    # assembled reports that would otherwise bypass the later planning guard.
    engine._claim_contract({'claim_input_contract': run_meta.get('claim_input_contract', 'legacy')}, artifacts)
    engine.stage_splits.validate_splits({
        **{key: getattr(run, key, None) for key in engine.stage_splits.IDENTITY_FIELDS},
        'run_id': getattr(run, 'id', None) or getattr(run, 'run_id', None),
        'source_splits': run_meta.get('source_splits', []),
    }, artifacts)
