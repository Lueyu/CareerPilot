"""Opt-in durable source partitioning; never rewrites a failed stage or calls a model."""
import hashlib
import json
import re
from copy import deepcopy

CONTRACT_VERSION = 'source-split-v1'
LIST_FIELDS = ('claims', 'issues', 'unknowns', 'dependencies', 'read_requests', 'boundary_adjustments')
IDENTITY_FIELDS = ('run_id', 'workspace_id', 'project_id', 'target_sha', 'model_config_fingerprint',
                   'pipeline_version', 'prompt_bundle_version')


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode()).hexdigest()


def row_fingerprint(value):
    """The persisted queue uses spaced JSON; do not replace it with engine hashes."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _require(condition):
    if not condition:
        from app.services.project_autopilot_engine import StageError
        raise StageError('CONFIG_CHANGED', '源码拆分恢复计划、冻结输入或来源检查点不一致；原阶段保留，不能继续或发布')


def _identity(context):
    result = {field: context.get(field) for field in IDENTITY_FIELDS}
    _require(all(result.values()))
    return result


def _input_hash(payload, identity):
    return row_fingerprint({'payload': payload, 'model': identity['model_config_fingerprint'],
        'pipeline': identity['pipeline_version'], 'prompt': identity['prompt_bundle_version'],
        'target': identity['target_sha']})


def _source_fingerprints(refs, artifacts, target):
    values = []
    for ref in refs:
        _require(isinstance(ref, dict) and set(ref) == {'source_id', 'path', 'start_line', 'end_line'})
        _require(isinstance(ref['path'], str) and isinstance(ref['source_id'], str))
        _require(type(ref['start_line']) is int and type(ref['end_line']) is int and 1 <= ref['start_line'] <= ref['end_line'])
        read = artifacts.get('read:' + _fingerprint(ref['path'])[:24])
        _require(isinstance(read, dict) and isinstance(read.get('source'), dict))
        source = read['source']
        _require(source.get('source_revision') == target and source.get('source_id') == ref['source_id'] and source.get('path') == ref['path'])
        matches = [chunk for chunk in read.get('chunks', []) if all(chunk.get(k) == v for k, v in ref.items())]
        _require(len(matches) == 1 and matches[0].get('source_version') == target)
        values.append({'ref': deepcopy(ref), 'source_version': target,
            'source_content_sha256': source.get('content_sha256'),
            'source_text_sha256': hashlib.sha256(source['content'].encode('utf-8')).hexdigest(),
            'source_fingerprint': _fingerprint(source), 'chunk_fingerprint': _fingerprint(matches[0])})
    return values


def _validate_shape(plan):
    _require(isinstance(plan, dict) and plan.get('contract_version') == CONTRACT_VERSION)
    _require(plan.get('fingerprint') == _fingerprint({k: v for k, v in plan.items() if k != 'fingerprint'}))
    parent = plan['parent']; payload = parent['input_json']; groups = plan['source_groups']
    _require(parent['stage'] == 'analyze_module' and parent['status'] == 'BLOCKED' and parent['uses_model'] is True)
    _require(re.fullmatch(r'analyze:[^:]+:\d+:\d+', parent['step_key']) is not None)
    _require('source_split' not in payload and payload['analysis_key'] == parent['step_key'])
    _require(parent['input_hash'] == _input_hash(payload, plan['identity']))
    _require(isinstance(groups, list) and 2 <= len(groups) <= 4 and all(isinstance(g, list) and g for g in groups))
    flat = [ref for group in groups for ref in group]
    _require(flat == payload['sources'] and len({_fingerprint(r) for r in flat}) == len(flat))
    _require(plan['inherited_repair'] in (None, 'OUTPUT_TRUNCATED'))
    _require(type(parent['attempt_count']) is int and type(parent['retry_count']) is int
             and parent['attempt_count'] >= parent['retry_count'] >= 1)
    _require(re.fullmatch('[0-9a-f]{64}', parent['attempts_sha256']) is not None)


def create_plan(context, parent_row_dict, groups, *, artifacts):
    """Seal a first-level partition before any module assembly; no mutation."""
    from app.services.project_autopilot_engine import StageError
    try:
        identity = _identity(context); row = parent_row_dict
        _require(all(row.get(k) == identity[k] for k in ('run_id', 'workspace_id', 'project_id')))
        _require(not any(k.startswith(('module:', 'cross:', 'section:', 'candidate-part:')) or k in ('report', 'candidates') for k in artifacts))
        _require(row['step_key'] not in artifacts and row['attempts'] and
                 any(a.get('outcome') == 'OUTPUT_TRUNCATED' for a in row['attempts']))
        parent = {key: deepcopy(row[key]) for key in ('id', 'step_key', 'stage', 'status', 'module_key',
            'input_json', 'input_hash', 'requires', 'attempt_count', 'retry_count', 'uses_model')}
        parent['attempts_sha256'] = row_fingerprint(row['attempts'])
        plan = {'contract_version': CONTRACT_VERSION, 'identity': identity, 'parent': parent,
            'source_groups': deepcopy(groups), 'inherited_repair': 'OUTPUT_TRUNCATED',
            'original_source_fingerprints': _source_fingerprints(row['input_json']['sources'], artifacts, identity['target_sha'])}
        plan['fingerprint'] = _fingerprint(plan)
        _validate_shape(plan)
        validate_plan(plan, context, artifacts)
        return plan
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, StageError): raise
        _require(False)


def plan_specs(plan):
    _validate_shape(plan)
    parent = plan['parent']; fingerprint = plan['fingerprint']; specs = []
    for index, group in enumerate(plan['source_groups']):
        key = f'analyze:{CONTRACT_VERSION}:{fingerprint}:{index}'
        marker = {'contract_version': CONTRACT_VERSION, 'plan_fingerprint': fingerprint,
                  'parent_key': parent['step_key'], 'role': 'child', 'index': index}
        payload = {**deepcopy(parent['input_json']), 'sources': deepcopy(group), 'analysis_key': key,
            'source_split': marker, 'inherited_repair': plan['inherited_repair']}
        specs.append({'key': key, 'stage': 'analyze_module', 'requires': deepcopy(parent['requires']),
                      'payload': payload, 'uses_model': True, 'module_key': parent['module_key']})
    keys = [s['key'] for s in specs]
    specs.append({'key': f'merge:{CONTRACT_VERSION}:{fingerprint}', 'stage': 'merge_analysis_split',
        'requires': keys, 'payload': {'child_keys': keys, 'source_split': {
            'contract_version': CONTRACT_VERSION, 'plan_fingerprint': fingerprint,
            'parent_key': parent['step_key'], 'role': 'merge'}},
        'uses_model': False, 'module_key': parent['module_key']})
    return specs


def _audit(plan, spec, artifacts):
    refs = spec['payload'].get('sources', plan['parent']['input_json']['sources'])
    audit = {**deepcopy(spec['payload']['source_split']),
        'payload_fingerprint': _fingerprint(spec['payload']),
        'source_fingerprints': _source_fingerprints(refs, artifacts, plan['identity']['target_sha'])}
    if spec['stage'] == 'merge_analysis_split':
        _require(all(k in artifacts for k in spec['requires']))
        audit['child_output_fingerprints'] = {k: _fingerprint(artifacts[k]) for k in spec['requires']}
    return audit


def _merged(plan, spec, artifacts):
    parts = [artifacts[k] for k in spec['requires']]
    value = {field: [x for part in parts for x in part.get(field, [])] for field in LIST_FIELDS}
    value.update(module_key=plan['parent']['module_key'],
        dependency_scope_complete=all(p['dependency_scope_complete'] for p in parts),
        source_ids=sorted({source for p in parts for source in p['source_ids']}),
        validation='merged_validated_source_partitions', source_split=_audit(plan, spec, artifacts))
    _require(len({c['id'] for c in value['claims']}) == len(value['claims']))
    return value


def validate_plan(plan, context, artifacts):
    from app.services.project_autopilot_engine import StageError, _validate_refs
    try:
        _validate_shape(plan)
        _require(plan['identity'] == _identity(context))
        parent = plan['parent']; target = plan['identity']['target_sha']
        _require(parent['step_key'] not in artifacts and artifacts.get('resolve', {}).get('target_sha') == target)
        _require(all(key in artifacts for key in parent['requires']))
        _require(plan['original_source_fingerprints'] == _source_fingerprints(parent['input_json']['sources'], artifacts, target))
        specs = plan_specs(plan)
        for spec in specs:
            if spec['key'] not in artifacts: continue
            output = artifacts[spec['key']]
            _require(output.get('source_split') == _audit(plan, spec, artifacts))
            if spec['stage'] == 'merge_analysis_split':
                expected = _merged(plan, spec, artifacts)
                _require({k: v for k, v in output.items() if k != 'provenance'} == expected)
            else:
                refs = spec['payload']['sources']
                chunks = [c for ref in refs for c in artifacts['read:' + _fingerprint(ref['path'])[:24]]['chunks']
                          if all(c.get(k) == v for k, v in ref.items())]
                _require(all(isinstance(output.get(field), list) for field in LIST_FIELDS))
                _require(type(output.get('dependency_scope_complete')) is bool and bool(output['claims']))
                _require(output.get('source_ids') == sorted({r['source_id'] for r in refs}))
                _require(output['module_key'] == parent['module_key'])
                _require(all(c['id'].startswith(spec['key'] + ':') for c in output['claims']))
                _require(len({c['id'] for c in output['claims']}) == len(output['claims']))
                for item in output['claims'] + output['issues']:
                    _require(_validate_refs(item['citations'], chunks) == item['citations'])
        module = artifacts.get('module:' + parent['module_key'])
        if module is not None:
            merge_key = specs[-1]['key']
            _require(merge_key in artifacts and module.get('source_split_fingerprints', {}).get(merge_key) == _fingerprint(artifacts[merge_key]))
        return plan
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, StageError) and exc.category == 'CONFIG_CHANGED': raise
        _require(False)


def validate_splits(context, artifacts):
    plans = context.get('source_splits') or []
    _require(isinstance(plans, list))
    known = {}; parents = set()
    for plan in plans:
        validate_plan(plan, context, artifacts)
        _require(plan['parent']['step_key'] not in parents)
        parents.add(plan['parent']['step_key'])
        known.update({spec['key']: (plan, spec) for spec in plan_specs(plan)})
    for key, artifact in artifacts.items():
        if key.startswith(('analyze:' + CONTRACT_VERSION + ':', 'merge:' + CONTRACT_VERSION + ':')) or artifact.get('source_split'):
            _require(key in known)
    return known


def validate_parent_spec(plan, spec):
    parent = plan['parent']
    _require(spec['key'] == parent['step_key'] and spec['payload'] == parent['input_json']
             and spec['requires'] == parent['requires'] and spec['module_key'] == parent['module_key'])


def stage_plan(context, payload, stage, artifacts):
    known = validate_splits(context, artifacts)
    key = payload.get('analysis_key') if stage == 'analyze_module' else 'merge:' + CONTRACT_VERSION + ':' + str((payload.get('source_split') or {}).get('plan_fingerprint'))
    _require(key in known)
    plan, spec = known[key]
    _require(spec['stage'] == stage and spec['payload'] == payload)
    return plan, spec


def child_audit(plan, spec, artifacts):
    return _audit(plan, spec, artifacts)


def merge(plan, spec, artifacts):
    return _merged(plan, spec, artifacts)
