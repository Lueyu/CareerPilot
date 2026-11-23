"""DB-independent, checkpointable project analysis stages.

The caller persists every returned artifact and owns retry, lease and cancellation
semantics. This module never publishes memory, mutates capability evidence, executes
repository code, discovers global credentials, or substitutes static model results.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services import project_autopilot_sources as sources
from app.services import project_claim_registry as claim_registry
from app.services import project_stage_splits as stage_splits
from app.services import study_routing
from app.services.llm import AgentAdapter
from app.services.project_prompts import PROMPT_BUNDLE_VERSION, system_prompt
from app.services.project_sources import redact_value

PIPELINE_VERSION = 'project-autopilot-v1'
SAFETY_POLICY_VERSION = 'project-source-policy-v3'
SCHEMA_VERSION = 2
MODEL_STAGES = frozenset(('plan_modules', 'analyze_module', 'cross_module', 'explain_section', 'extract_candidates', 'analyze_change_impact'))


class StageError(ValueError):
    safe_for_user = True

    def __init__(self, category, message, retry_after=None, request_id=None, usage=None, request_meta=None):
        super().__init__(message)
        self.category, self.retry_after, self.request_id = category, retry_after, request_id
        self.usage, self.request_meta = usage, request_meta


def _attach_request(error, usage, meta):
    if error.usage is None:
        error.usage = usage
    if error.request_meta is None:
        error.request_meta = meta
    if error.request_id is None:
        error.request_id = (error.request_meta or {}).get('request_id')
    return error


@dataclass
class RuntimeContext:
    workspace_id: int
    project_id: int
    llm_config: dict = field(repr=False)
    credentials: dict = field(repr=False)
    request_timeout: float = 180
    input_chars: int = 40000
    max_source_bytes: int | None = None
    context_tokens: int | None = None
    output_reserve_tokens: int = 4000
    repair_diagnostic: str | None = None
    transport_mode: str = 'json'
    stage_timeout: float | None = None


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


class Ref(Strict):
    source_id: str = Field(min_length=1, max_length=120)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


FactType = Literal['CODE_OBSERVED', 'INFERRED', 'DOC_CLAIM', 'UNASSESSED']


class TechnologyUse(Strict):
    name: str = Field(min_length=1, max_length=120)
    limitations: str = Field(min_length=1, max_length=600)


class Claim(Strict):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,39}$')
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=1800)
    kind: FactType
    citations: list[Ref] = Field(max_length=12)
    technology: TechnologyUse | None = None


class Issue(Strict):
    title: str = Field(min_length=1, max_length=200)
    skill_key: Literal['agent', 'tools', 'memory', 'rag', 'eval', 'backend', 'reliability', 'deploy']
    severity: Literal['low', 'medium', 'high']
    detail: str = Field(min_length=1, max_length=1800)
    impact: str = Field(min_length=1, max_length=1000)
    recommendation: str = Field(min_length=1, max_length=1000)
    status: Literal['observed', 'hypothesis']
    citations: list[Ref] = Field(min_length=1, max_length=12)
    interview_questions: list[str] = Field(min_length=1, max_length=5)


class PathReason(Strict):
    path: str = Field(min_length=1, max_length=512)
    reason: str = Field(min_length=1, max_length=1000)


class ModuleResponse(Strict):
    claims: list[Claim] = Field(min_length=1, max_length=30)
    issues: list[Issue] = Field(max_length=12)
    dependencies: list[PathReason] = Field(max_length=100)
    dependency_scope_complete: bool
    unknowns: list[str] = Field(max_length=30)
    read_requests: list[PathReason] = Field(max_length=30)


class ModulePlan(Strict):
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)
    paths: list[str] = Field(min_length=1, max_length=100)


class PlanResponse(Strict):
    modules: list[ModulePlan] = Field(min_length=1, max_length=100)


class Explanation(Strict):
    text: str = Field(min_length=1, max_length=3000)
    claim_ids: list[str] = Field(min_length=1, max_length=30)


class CrossResponse(Strict):
    observations: list[Explanation] = Field(max_length=30)
    conflicts: list[Explanation] = Field(max_length=30)
    unknowns: list[str] = Field(max_length=30)


class SectionResponse(Strict):
    paragraphs: list[Explanation] = Field(min_length=1, max_length=10)
    unknowns: list[str] = Field(max_length=30)


class Candidate(Strict):
    fact_key: str = Field(min_length=1, max_length=160)
    kind: Literal['purpose', 'architecture', 'module', 'tradeoff', 'risk', 'hypothesis']
    content: str = Field(min_length=1, max_length=2000)
    claim_ids: list[str] = Field(min_length=1, max_length=20)
    fact_type: FactType
    issue_id: str | None = Field(default=None, max_length=40)


class CandidateResponse(Strict):
    candidates: list[Candidate] = Field(max_length=40)


class ImpactResponse(Strict):
    affected_paths: list[str] = Field(max_length=500)
    unknown_dependencies: bool
    reasons: list[PathReason] = Field(max_length=100)


SECTIONS = [('overview', '项目用途'), ('features', '功能、模块与技术栈'), ('flow', '主要业务和数据流'),
            ('implementation', '实现、声明与未接通环节'), ('architecture', '架构取舍、风险与改进'),
            ('tests_deploy', '测试与部署：源码观察，不是运行结果'), ('interview', '岗位关联与面试准备'),
            ('changes', '相对上一版本的变化'), ('limitations', '分析范围与局限')]


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _provenance(context, runtime=None):
    return {'pipeline_version': PIPELINE_VERSION, 'prompt_bundle_version': PROMPT_BUNDLE_VERSION,
            'schema_version': SCHEMA_VERSION, 'safety_policy_version': SAFETY_POLICY_VERSION,
            'model_config_fingerprint': context.get('model_config_fingerprint'),
            'workspace_id': runtime.workspace_id if runtime else context.get('workspace_id'),
            'project_id': runtime.project_id if runtime else context.get('project_id') or context.get('project', {}).get('id')}


def _read_key(path):
    return 'read:' + fingerprint(path)[:24]


def _groups(items, budget, *, max_items=None):
    groups, current, size = [], [], 2
    for item in items:
        amount = len(_json(item)) + 1
        if amount > budget:
            raise StageError('INPUT_TOO_LARGE', '一个阶段资料超过输入预算，需要拆分或调整预算，未截断冒充完整')
        if current and (size + amount > budget or (max_items and len(current) >= max_items)):
            groups.append(current)
            current, size = [], 2
        current.append(item)
        size += amount
    if current:
        groups.append(current)
    return groups


def _manifest_partitions(manifest, budget):
    entries = [e for e in manifest['entries'] if e.get('included')]
    return _groups(entries, max(1000, budget // 3), max_items=80)


def _chunk_sources(read_artifacts):
    return [chunk for value in read_artifacts for chunk in value.get('chunks', [])]


def _claims(artifacts, module_keys):
    return [claim for key in module_keys for claim in artifacts[key]['claims']]


def _module_specs(artifacts):
    return [(key, m) for key in sorted(artifacts) if key.startswith('plan:') for m in artifacts[key]['modules']]


def _claim_contract(context, artifacts):
    version = context.get('claim_input_contract') or 'legacy'
    def reject():
        raise StageError('CONFIG_CHANGED', '结论输入契约或来源检查点不完整、不一致；原检查点保留，不能发布或重标')
    if version not in ('legacy', claim_registry.CONTRACT_VERSION):
        reject()
    for key, artifact in artifacts.items():
        if key.startswith(('cross:', 'section:', 'candidate-part:')):
            encoded = key.split(':')[1] == claim_registry.CONTRACT_VERSION
            if (encoded != (version == claim_registry.CONTRACT_VERSION) or
                    artifact.get('input_contract_version') != (version if encoded else None)):
                reject()
            if encoded:
                # Recovery must re-bind a saved result to the original full
                # claims, not merely trust a version-shaped key or hash string.
                try:
                    ids = artifact['claim_input_claim_ids']
                    available = (artifacts['report']['report']['claims'] if key.startswith('candidate-part:') else
                                 [c for k, a in artifacts.items() if k.startswith('module:') for c in a['claims']])
                    by_id = {c['id']: c for c in available}
                    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
                        reject()
                    claims = [by_id[i] for i in ids]
                    if key.startswith(('cross:', 'section:')) and artifact['claim_ids'] != ids:
                        reject()
                    if artifact.get('claim_input_fingerprint') != fingerprint(claim_registry.encode_claims(claims)):
                        reject()
                except (KeyError, TypeError, ValueError, AttributeError):
                    reject()
    for key in ('report', 'candidates'):
        if key not in artifacts:
            continue
        artifact = artifacts[key]
        if version == 'legacy':
            if artifact.get('input_contract_version') is not None or artifact.get('source_stage_fingerprints') is not None:
                reject()
            continue
        expected = _aggregate_claim_contract(artifacts, key)
        outputs = [artifact]
        if key == 'report':
            outputs += [artifact.get('report', {}), artifact.get('snapshot', {})]
        if any(not isinstance(output, dict) or any(output.get(k) != v for k, v in expected.items()) for output in outputs):
            reject()
    return version


def _aggregate_claim_contract(artifacts, stage):
    prefixes = ('module:', 'cross:', 'section:') if stage == 'report' else ('candidate-part:',)
    fixed = ('resolve', 'manifest') if stage == 'report' else ()
    return {'input_contract_version': claim_registry.CONTRACT_VERSION,
            'source_stage_fingerprints': {k: fingerprint(a) for k, a in artifacts.items()
                                          if k in fixed or k.startswith(prefixes)}}


def _claim_material(payload, claims):
    version = payload.get('input_contract_version')
    if version is None:
        return {'claims': claims}
    if version != claim_registry.CONTRACT_VERSION:
        raise StageError('CONFIG_CHANGED', '未知的结论输入契约')
    try:
        material = claim_registry.encode_claims(claims)
    except ValueError:
        raise StageError('INVALID_CITATION', '来源注册表不能无损还原本批结论') from None
    if payload.get('claim_input_fingerprint') != fingerprint(material):
        raise StageError('CONFIG_CHANGED', '本批结论或来源与已冻结输入指纹不一致')
    return material


def plan_steps(context: dict, artifacts: dict[str, dict]) -> list[dict]:
    """Deterministic discovered graph. All returned prerequisite keys are stable."""
    specs = []
    claim_contract = _claim_contract(context, artifacts)
    stage_splits.validate_splits(context, artifacts)
    split_by_parent = {plan['parent']['step_key']: plan for plan in context.get('source_splits') or []}
    def add(key, stage, requires=(), payload=None, module_key=None):
        item = {'key': key, 'stage': stage, 'requires': list(requires), 'payload': payload or {}, 'uses_model': stage in MODEL_STAGES}
        if stage == 'plan_modules' and _reusable_plan(context, _effective_manifest(artifacts), (payload or {}).get('paths', [])):
            item['uses_model'] = False
        if stage == 'analyze_change_impact' and _impact_input(context, artifacts['manifest']) is None:
            item['uses_model'] = False
        if module_key:
            item['module_key'] = module_key
        specs.append(item)
        return key
    add('resolve', 'resolve')
    if 'resolve' not in artifacts:
        return specs
    page_keys, cursor, index = [], None, 0
    while True:
        key = f'manifest-page:{index:06d}'
        add(key, 'manifest_page', ['resolve', *page_keys[-1:]], {'cursor': cursor})
        page_keys.append(key)
        if key not in artifacts:
            return specs
        page = artifacts[key]
        if page.get('complete'):
            break
        cursor = page.get('next_cursor')
        if cursor is None:
            raise StageError('MANIFEST_INCOMPLETE', '清单未完成且没有恢复游标')
        index += 1
    add('manifest', 'manifest', ['resolve', *page_keys], {'page_keys': page_keys})
    if 'manifest' not in artifacts:
        return specs
    budget = int((context.get('budget') or {}).get('input_chars') or 40000)
    manifest = artifacts['manifest']
    semantic_requires = ['manifest']
    if context.get('mode') == 'incremental' and manifest['diff'].get('changes') and not manifest['diff'].get('requires_full'):
        add('impact', 'analyze_change_impact', ['manifest'])
        if 'impact' not in artifacts:
            return specs
        semantic_requires.append('impact')
    manifest = _effective_manifest(artifacts)
    partitions = _manifest_partitions(manifest, budget)
    plan_keys = []
    for part in partitions:
        key = 'plan:' + fingerprint([e['path'] for e in part])[:24]
        plan_keys.append(add(key, 'plan_modules', semantic_requires, {'paths': [e['path'] for e in part]}))
    if not all(key in artifacts for key in plan_keys):
        return specs
    module_keys = []
    for plan_key, module in _module_specs({key: artifacts[key] for key in plan_keys}):
        module_key, paths = module['key'], module['paths']
        result_key = 'module:' + module_key
        module_keys.append(result_key)
        reusable = _reusable_module(context, manifest, module)
        if reusable:
            add(result_key, 'reuse_module', ['manifest', plan_key], {'module': module}, module_key)
            continue
        read_keys = [_read_key(path) for path in paths]
        for path, read_key in zip(paths, read_keys):
            if not any(s['key'] == read_key for s in specs):
                add(read_key, 'read_source', ['manifest'], {'path': path})
        if not all(k in artifacts for k in read_keys):
            continue
        chunks = _chunk_sources([artifacts[k] for k in read_keys])
        batches = _groups(chunks, max(1000, budget // 2))
        analysis_keys, read_paths = [], set(paths)
        all_batches_done = True
        # Each followup round must add a previously unread path for this module.
        # The finite manifest bounds discovery; a fixed default can strand a run
        # even while its independently enforced call/source budgets have room.
        available_paths = {e['path'] for e in manifest['entries'] if e.get('included')}
        max_rounds = int((context.get('budget') or {}).get('module_read_rounds') or
                         (1 + len(available_paths - read_paths)))
        for round_index in range(max_rounds):
            for chunk_index, batch in enumerate(batches):
                key = f'analyze:{module_key}:{round_index}:{chunk_index}'
                requires = [plan_key, 'manifest', *sorted({_read_key(c['path']) for c in batch})]
                payload = {'module': module, 'sources': [{k: c[k] for k in ('source_id', 'start_line', 'end_line', 'path')} for c in batch],
                           'analysis_key': key}
                if key in split_by_parent:
                    plan = split_by_parent[key]
                    stage_splits.validate_parent_spec(plan, {'key': key, 'payload': payload, 'requires': requires, 'module_key': module_key})
                    children = stage_splits.plan_specs(plan)
                    specs.extend(children)
                    analysis_keys.append(children[-1]['key'])
                else:
                    analysis_keys.append(add(key, 'analyze_module', requires, payload, module_key))
            if not all(k in artifacts for k in analysis_keys):
                all_batches_done = False
                break
            requested = sorted({r['path'] for k in analysis_keys for r in artifacts[k].get('read_requests', [])} - read_paths)
            if not requested:
                break
            if round_index == max_rounds - 1:
                raise StageError('BUDGET_EXHAUSTED', '模块仍需补读来源，本轮阅读预算已用完；成功模块和进度已保存')
            for path in requested:
                key = _read_key(path)
                if not any(s['key'] == key for s in specs):
                    add(key, 'read_source', ['manifest'], {'path': path})
            if not all(_read_key(p) in artifacts for p in requested):
                all_batches_done = False
                break
            batches = _groups(_chunk_sources([artifacts[_read_key(p)] for p in requested]), max(1000, budget // 2))
            read_paths.update(requested)
        if all_batches_done:
            add(result_key, 'module', [plan_key, 'manifest', *analysis_keys],
                {'module': module, 'analysis_keys': analysis_keys, 'read_paths': sorted(read_paths)}, module_key)
    if not module_keys or not all(key in artifacts for key in module_keys):
        if not module_keys:
            add('report', 'report', ['manifest'], {'module_keys': [], 'section_keys': [], 'cross_keys': []})
        return specs
    claims = _claims(artifacts, module_keys)
    if claim_contract == 'legacy':
        claim_batches = _groups(claims, max(1000, budget // 2))
    else:
        try:
            claim_batches = claim_registry.group_claims(claims, max(1000, budget // 2))
        except ValueError:
            raise StageError('INPUT_TOO_LARGE', '无损结论及来源注册表超过阶段材料预算；未截断') from None
    def claim_payload(batch):
        result = {'claim_ids': [c['id'] for c in batch]}
        if claim_contract != 'legacy':
            result.update(input_contract_version=claim_contract,
                          claim_input_fingerprint=fingerprint(claim_registry.encode_claims(batch)))
        return result
    version_key = '' if claim_contract == 'legacy' else claim_contract + ':'
    cross_keys = []
    for i, batch in enumerate(claim_batches):
        cross_keys.append(add(f'cross:{version_key}{i}', 'cross_module', module_keys,
            {**claim_payload(batch), 'module_keys': module_keys}))
    if not all(key in artifacts for key in cross_keys):
        return specs
    section_keys = []
    for section, title in SECTIONS:
        for i, batch in enumerate(claim_batches):
            key = f'section:{version_key}{section}:{i}'
            section_keys.append(add(key, 'explain_section', [*module_keys, *cross_keys],
                {'section': section, 'title': title, **claim_payload(batch), 'module_keys': module_keys, 'cross_keys': cross_keys}))
    add('report', 'report', ['manifest', *module_keys, *cross_keys, *section_keys],
        {'module_keys': module_keys, 'section_keys': section_keys, 'cross_keys': cross_keys})
    if artifacts.get('report', {}).get('coverage_status') == 'COMPLETE':
        candidate_keys = []
        for i, batch in enumerate(claim_batches):
            candidate_keys.append(add(f'candidate-part:{version_key}{i}', 'extract_candidates', ['report'], claim_payload(batch)))
        add('candidates', 'candidates', candidate_keys, {'candidate_keys': candidate_keys})
    return specs


def _validated(model, value):
    try:
        return model.model_validate(value).model_dump()
    except ValidationError as exc:
        # Never include source/model values or attacker-controlled field names.
        fields = set().union(*(set(m.model_fields) for m in (Ref, Claim, Issue, PathReason, ModuleResponse, ModulePlan, PlanResponse,
                                                            Explanation, CrossResponse, SectionResponse, Candidate, CandidateResponse, ImpactResponse)))
        locations = ', '.join('.'.join(str(p) if isinstance(p, int) or p in fields else 'unknown_field' for p in e['loc']) for e in exc.errors()[:5])
        raise StageError('INVALID_SCHEMA', f'模型结构校验失败：{locations}；未保存为有效阶段') from None


async def _ask(task, payload, model, runtime):
    if runtime.transport_mode not in ('json', 'sse-v1'):
        raise StageError('CONFIG_CHANGED', '未知的模型传输协议；未发起请求，原检查点保留')
    stage_timeout = runtime.request_timeout
    if runtime.stage_timeout is not None:
        total, io = runtime.stage_timeout, runtime.request_timeout
        if (runtime.transport_mode != 'sse-v1' or type(total) not in (int, float)
                or not 0 < total <= 600 or not math.isfinite(total)
                or type(io) not in (int, float) or not 0 < io <= total or not math.isfinite(io)):
            raise StageError('CONFIG_CHANGED', '阶段总时限配置无效；需显式流式模式且总时限不小于网络闲置时限，未发起请求')
        stage_timeout = total
    cfg = runtime.llm_config
    route_meta = {}
    if 'auto_profiles' in cfg:
        action = 'project' if task in ('analyze_module', 'verify_cross_module', 'analyze_change_impact', 'explain_project_section') else 'organize'
        tier, reason, _ = study_routing.decide('auto', action, '', [], payload.get('sources', []))
        profiles = cfg['auto_profiles']
        selected = tier if profiles.get(tier) else 'flash'
        if tier != selected:
            reason += '；深入档未配置，使用快速档'
        cfg = profiles.get(selected) or {}
        route_meta = {'routing_mode': 'auto', 'requested_tier': tier, 'tier': selected, 'routing_reason': reason}
    if not all(cfg.get(k) for k in ('provider', 'api_key', 'base_url', 'model')):
        raise StageError('NOT_CONFIGURED', '尚未配置可用的模型服务')
    value = {'task': task, **payload, 'output_schema': model.model_json_schema(), 'prompt_version': PROMPT_BUNDLE_VERSION}
    if runtime.repair_diagnostic:
        value['repair_request'] = {'previous_validation_error': runtime.repair_diagnostic[:600],
            'output_strategy': 'smaller_response_complete_schema',
            'instructions': '这是一次有预算的修复：严格修正指出的字段或实际引用，保持所有必填字段。若先前截断，只输出最重要的 1-2 条观察、最多 1 个问题，章节 1 段约 100 字，候选最多 3 条；规划仍需覆盖全部给定路径。不要重复长解释，不修改模型参数，不编造补全。'}
    user, system = _json(value), system_prompt(task)
    contract = payload.get('input_contract_version')
    if contract is not None:
        if contract != claim_registry.CONTRACT_VERSION:
            raise StageError('CONFIG_CHANGED', '未知的结论输入契约')
        value['task_prompt_version'] = contract
        user = _json(value)
        system += '\n' + claim_registry.INSTRUCTIONS
    estimate = len((system + user).encode('utf-8'))
    meta = {**route_meta, 'task': task, 'input_chars': len(system) + len(user), 'input_bytes': estimate,
            'input_tokens_estimate': estimate, 'token_estimate_method': 'conservative_utf8_bytes_not_actual_tokens',
            'prompt_version': PROMPT_BUNDLE_VERSION, 'model': cfg['model'], 'provider': cfg['provider'],
            'repair_requested': bool(runtime.repair_diagnostic), 'request_started': False,
            'transport_mode': runtime.transport_mode, 'io_timeout_seconds': runtime.request_timeout,
            'stage_timeout_seconds': stage_timeout}
    if contract is not None:
        meta.update(input_contract_version=contract, task_prompt_version=contract)
    if len(system) + len(user) > runtime.input_chars or (runtime.context_tokens and estimate + runtime.output_reserve_tokens > runtime.context_tokens):
        raise StageError('INPUT_TOO_LARGE', '阶段请求超过输入预算，需要拆分；未重复发送或静默截断', usage={'known': False}, request_meta=meta)
    adapter = AgentAdapter()
    meta['request_started'] = True
    try:
        # No model-name heuristics or effort override. Keep the user's model.
        transport_options = {'stream': True} if runtime.transport_mode == 'sse-v1' else {}
        if runtime.stage_timeout is not None:
            transport_options['total_timeout'] = stage_timeout
        result = await asyncio.wait_for(adapter.chat_with(cfg, system, user, json_mode=True,
            timeout=runtime.request_timeout, **transport_options), timeout=stage_timeout)
    except TimeoutError:
        meta.update(getattr(adapter, 'last_response_meta', {}) or {})
        raise StageError('REQUEST_TIMEOUT', '单次模型请求超时，已完成阶段仍保留',
            request_id=meta.get('request_id'), usage=getattr(adapter, 'last_usage', None) or {'known': False},
            request_meta=meta) from None
    response_meta = getattr(adapter, 'last_response_meta', {}) or {}
    meta.update(response_meta)
    reported_usage = (result or {}).get('usage') or getattr(adapter, 'last_usage', None) or {}
    usage = {k: reported_usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
             if isinstance(reported_usage, dict) and type(reported_usage.get(k)) is int and reported_usage[k] >= 0} or {'known': False}
    raw_text = (result or {}).get('content')
    if isinstance(raw_text, str):
        meta.update(output_chars=len(raw_text), output_bytes=len(raw_text.encode()), output_sha256=hashlib.sha256(raw_text.encode()).hexdigest())
    if response_meta.get('finish_reason') == 'length':
        raise StageError('OUTPUT_TRUNCATED', '模型输出被服务端截断，未用不完整结果继续分析', request_id=response_meta.get('request_id'), usage=usage, request_meta=meta)
    if not result:
        error = getattr(adapter, 'last_error', {}) or {}
        status, kind = error.get('status'), error.get('kind')
        category = ('NOT_CONFIGURED' if kind == 'configuration' else 'REQUEST_TIMEOUT' if kind == 'timeout' else
                    'AUTH_BLOCKED' if status in (401, 403) else 'RATE_LIMITED' if status == 429 else
                    'INPUT_TOO_LARGE' if status == 413 else 'PROVIDER_5XX' if isinstance(status, int) and status >= 500 else
                    'PROVIDER_REJECTED' if isinstance(status, int) and status >= 400 else 'NETWORK_TRANSIENT' if kind == 'network' else 'INVALID_SCHEMA')
        raise StageError(category, f'模型阶段暂未成功（{category}），已完成进度保留', error.get('retry_after'), error.get('request_id'), usage, meta)
    try:
        text = result['content'].strip()
        fence = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.S)
        parsed = json.loads(fence.group(1) if fence else text)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise StageError('INVALID_SCHEMA', '模型未返回完整 JSON 对象', usage=usage, request_meta=meta, request_id=meta.get('request_id')) from None
    try:
        value = _validated(model, redact_value(parsed))
    except StageError as exc:
        raise _attach_request(exc, usage, meta) from None
    return value, usage, meta


def _validate_refs(refs, chunks):
    result = []
    for ref in refs:
        if ref['start_line'] > ref['end_line']:
            raise StageError('INVALID_CITATION', '引用行号范围无效')
        candidates = [c for c in chunks if c['source_id'] == ref['source_id']]
        identities = {(c['path'], c['source_version']) for c in candidates}
        # Chunk boundaries are an input-size detail, not an unread gap. Accept
        # a contiguous union only within one unambiguous source/version; never
        # bridge missing lines or bind a ref to a different source identity.
        cursor = ref['start_line']
        if len(identities) == 1:
            for chunk in sorted(candidates, key=lambda c: (c['start_line'], c['end_line'])):
                if chunk['end_line'] < cursor:
                    continue
                if chunk['start_line'] > cursor:
                    break
                cursor = chunk['end_line'] + 1
                if cursor > ref['end_line']:
                    break
        if len(identities) != 1 or cursor <= ref['end_line']:
            raise StageError('INVALID_CITATION', '引用不属于本阶段实际读取的版本或行号范围')
        source = candidates[0]
        result.append({**ref, 'path': source['path'], 'source_version': source['source_version']})
    return result


def _normalize_fact_boundaries(artifact, source_records):
    """Derive labels from frozen source categories without editing checkpoints."""
    derived = deepcopy(artifact)
    categories = {}
    for source in source_records:
        identity = (source.get('source_id'), source.get('source_revision'), source.get('path'))
        if all(isinstance(value, str) and value for value in identity):
            categories.setdefault(identity, set()).add(source.get('category'))
    adjustments = derived.setdefault('boundary_adjustments', [])
    for claim in derived.get('claims', []):
        refs = claim.get('citations') or []
        if claim.get('kind') != 'CODE_OBSERVED' or not refs or not all(isinstance(ref, dict) for ref in refs):
            continue
        identities = [(ref.get('source_id'), ref.get('source_version'), ref.get('path')) for ref in refs]
        if not all(categories.get(identity) == {'documentation'} for identity in identities):
            continue
        claim['kind'] = 'DOC_CLAIM'
        claim.pop('technology', None)
        adjustment = {'claim_id': claim['id'], 'from': 'CODE_OBSERVED', 'to': 'DOC_CLAIM',
            'reason': 'all_citations_are_documentation',
            'sources': [{'source_id': source_id, 'source_version': version, 'path': path}
                        for source_id, version, path in sorted(set(identities))]}
        if adjustment not in adjustments:
            adjustments.append(adjustment)
    return derived


def _apply_claim_reviews(artifact, context, target_sha):
    """Derive usable claims without overwriting frozen model observations.

    Reviews are explicit, source-backed run audit records, not model output or
    repository instructions. Quarantined claims remain available as history,
    but cannot enter synthesis or memory extraction as accepted evidence.
    """
    claim_map = {c['id']: c for c in artifact.get('claims', [])}
    reviews = [r for r in context.get('claim_reviews', []) if r.get('claim_id') in claim_map]
    if not reviews:
        return artifact
    sources_by_id = {s['source_id']: s for s in artifact.get('sources', [])}
    chunks = [{'source_id': s['source_id'], 'path': s['path'], 'source_version': s['source_revision'],
               'start_line': 1, 'end_line': s['line_count']} for s in sources_by_id.values()]
    seen = set()
    for review in reviews:
        if (review.get('action') != 'QUARANTINE' or not review.get('review_id') or not review.get('reason')
                or review.get('workspace_id') != context['workspace_id']
                or review.get('project_id') != context['project_id'] or review.get('target_sha') != target_sha
                or not review.get('citations') or review['claim_id'] in seen):
            raise StageError('INVALID_CITATION', '结论复核记录缺少依据、重复或不属于当前项目版本')
        checked = _validate_refs([{k: r[k] for k in ('source_id', 'start_line', 'end_line')}
                                  for r in review['citations']], chunks)
        if checked != review['citations']:
            raise StageError('INVALID_CITATION', '结论复核引用与实际读取来源不一致')
        seen.add(review['claim_id'])
    result = deepcopy(artifact)
    result['quarantined_claims'] = [*result.get('quarantined_claims', []),
        *[{'original_claim': deepcopy(claim_map[r['claim_id']]),
           'claim_snapshot_scope': 'derived_before_quarantine; raw_model_stage_preserved',
           'review': deepcopy(r)} for r in reviews]]
    result['claims'] = [c for c in result['claims'] if c['id'] not in seen]
    result['issues'] = [i for i in result.get('issues', []) if i.get('claim_id') not in seen]
    result['unknowns'] = [*result.get('unknowns', []), *[
        f"独立源码复核 {r['review_id']}：一条原始模型结论存在争议，已从本报告事实与记忆候选输入中隔离；"
        f"原因：{r['reason']} 原始结论和对应来源仍保留供核查，不表示已由模型完成纠正。" for r in reviews]]
    result['claim_review_fingerprint'] = fingerprint(reviews)
    return result


def _validate_claim_links(entries, claims):
    available = {c['id'] for c in claims}
    if any(not set(e['claim_ids']) <= available or len(e['claim_ids']) != len(set(e['claim_ids'])) for e in entries):
        raise StageError('INVALID_CITATION', '解释引用了本阶段未提供或重复的结论编号')


def _path_context(manifest, module, limit=4000):
    all_paths = [e['path'] for e in manifest['entries'] if e.get('included')]
    parents = {p.rsplit('/', 1)[0] if '/' in p else '' for p in module['paths']}
    candidates = sorted(all_paths, key=lambda p: (p not in module['paths'], (p.rsplit('/', 1)[0] if '/' in p else '') not in parents, p))
    selected, size = [], 2
    for path in candidates:
        amount = len(_json(path)) + 1
        if size + amount > limit:
            break
        selected.append(path)
        size += amount
    partial = len(selected) < len(all_paths)
    note = ('此处仅提供部分可分析路径，未列出的文件可能存在。' if partial
            else '此处列出固定版本中全部可分析路径，不含因策略排除的文件。')
    return {'available_paths': selected, 'path_catalog_partial': partial, 'path_catalog_note': note}


def _check_module_paths(value, manifest):
    eligible = {entry['path'] for entry in manifest['entries'] if entry.get('included')}
    for key, label in (('dependencies', '依赖'), ('read_requests', '补读请求')):
        accepted = []
        for item in value[key]:
            path = item['path']
            if sources.classify_path(path)[1] == 'unsafe_path' or any(ord(c) < 32 or ord(c) == 127 for c in path):
                raise StageError('INVALID_CITATION', '模块依赖或补读请求包含无效的仓库相对路径')
            if path in eligible:
                accepted.append(item)
        omitted = len(value[key]) - len(accepted)
        if omitted:
            value[key] = accepted
            value['dependency_scope_complete'] = False
            value['unknowns'].append(f'{omitted} 条{label}无法匹配固定版本的可分析文件，相关依赖范围仍待确认。')


def _change_context(diff, claims):
    changes = diff.get('changes') or []
    paths = {c['path'] for claim in claims for c in claim['citations']}
    related = [c for c in changes if c['path'] in paths or c.get('previous_path') in paths]
    selected, size = [], 0
    for change in related:
        if size + len(_json(change)) > 4000:
            break
        selected.append(change)
        size += len(_json(change))
    counts = {s: sum(c['status'] == s for c in changes) for s in ('added', 'modified', 'deleted', 'renamed')}
    return {'complete_manifest_diff': diff.get('complete', False), 'counts': counts,
            'related_changes': selected, 'related_changes_truncated': len(selected) < len(related),
            'note': '完整逐文件差异另行保存在报告；本请求只有数量与当前结论相关差异，不推断未展示代码变化。'}


def _split_source(source, limit=8000):
    chunks, current, size, start = [], [], 0, 1
    lines = source['content'].splitlines()
    for n, line in enumerate(lines, 1):
        if len(line) + 20 > limit:
            raise StageError('INPUT_TOO_LARGE', '源码包含超过阶段预算的单行，请调整读取预算；未截断行号证据')
        amount = len(line) + len(str(n)) + 3
        if current and size + amount > limit:
            chunks.append({'start_line': start, 'end_line': n - 1, 'content': '\n'.join(current)})
            current, size, start = [], 0, n
        current.append(f'{n}: {line}')
        size += amount
    if current:
        chunks.append({'start_line': start, 'end_line': len(lines), 'content': '\n'.join(current)})
    return [{**chunk, 'source_id': source['source_id'], 'source_version': source['source_revision'], 'path': source['path'],
             'encoding': source.get('encoding', 'utf-8'),
             'normalizations': [n for n in source.get('normalizations', []) if chunk['start_line'] <= n['line'] <= chunk['end_line']],
             'normalization_note': '若 normalizations 非空，表中原始控制字符已作可逆可见转义；原始行号不变。显示的转义串不代表原代码本来写了反斜杠，须结合坐标表理解。'} for chunk in chunks]


def _baseline_artifacts(context):
    baseline = context.get('baseline') or {}
    return baseline.get('artifacts') or baseline.get('artifact_mapping') or {}


def _downloaded_bytes(artifacts):
    # Only this run's actual read checkpoints consume its download budget.
    # Reused semantic artifacts retain old source records but perform no download.
    downloads = {}
    for key, value in artifacts.items():
        if key.startswith('read:') and isinstance(value, dict) and value.get('source'):
            source = value['source']
            count = source.get('download_bytes')
            if type(count) is not int or count < 0:
                raise StageError('BUDGET_EXHAUSTED', '旧源码检查点缺少原始读取字节计量，不能按脱敏后长度推算剩余额度')
            downloads.setdefault(source['source_id'], count)
    return sum(downloads.values())


def _impact_input(context, manifest):
    baseline = context.get('baseline') or {}
    modules = (baseline.get('snapshot') or {}).get('modules') or []
    if not modules or any(not m.get('dependency_scope_complete') for m in modules):
        return None
    value = {'changes': manifest['diff']['changes'], 'known_modules': [
        {'module_key': m['key'], 'paths': m['paths'], 'dependency_paths': m['dependencies']} for m in modules]}
    budget = int((context.get('budget') or {}).get('input_chars') or 40000)
    return value if len(_json(value)) < budget // 2 else None


def _effective_manifest(artifacts):
    manifest = artifacts['manifest']
    impact = artifacts.get('impact')
    if not impact:
        return manifest
    diff = dict(manifest['diff'])
    if impact.get('unknown_dependencies'):
        diff.update(requires_full=True, reason='增量影响范围无法有界确认，转为完整语义分析')
    return {**manifest, 'diff': diff, 'impact': impact}


def _reusable_module(context, manifest, module):
    if not _reuse_allowed(context, manifest):
        return None
    baseline = context.get('baseline') or {}
    modules = (baseline.get('snapshot') or {}).get('modules') or [v for k, v in _baseline_artifacts(context).items() if k.startswith('module:')]
    original = next((m for m in modules if m.get('key') == module.get('key') and m.get('paths') == module.get('paths')), None)
    if not original or original.get('provenance') != _provenance(context) or original.get('coverage_status') != 'COMPLETE':
        return None
    # Unknown dependencies anywhere in the prior map cannot establish a safe
    # reverse-impact boundary, so conservatively reanalyse all semantics.
    if any(not m.get('dependency_scope_complete') for m in modules):
        return None
    old_manifest = (baseline.get('snapshot') or {}).get('manifest') or _baseline_artifacts(context).get('manifest') or {}
    before = {e['path']: e for e in old_manifest.get('entries', [])}
    after = {e['path']: e for e in manifest['entries']}
    changed = manifest['diff']['changes']
    impacted = set(manifest.get('impact', {}).get('affected_paths', []))
    shared = re.compile(r'(^|/)(?:[^/]*(?:schema|interface|config)|(?:package|requirements|pyproject|cargo|go\.mod|pnpm|yarn|uv\.lock|poetry\.lock)[^/]*|main\.[^/]+|types\.[^/]+)$', re.I)
    if any(shared.search(c['path']) or shared.search(c.get('previous_path', '')) for c in changed):
        return None
    # New/deleted or relocated files can introduce/remove relationships absent
    # from the old map. A full semantic pass is safer than inventing dependencies.
    if any(c['status'] in ('added', 'deleted', 'renamed') for c in changed):
        return None
    owners = {p: m for m in modules for p in m['paths']}
    todo, checked = [original], set()
    while todo:
        current = todo.pop()
        if current['key'] in checked:
            continue
        checked.add(current['key'])
        for path in set(current['paths']) | set(current.get('dependencies', [])):
            if path in impacted:
                return None
            if path not in before or path not in after or not before[path].get('blob_oid'):
                return None
            if any(before[path].get(k) != after[path].get(k) for k in ('blob_oid', 'type', 'mode')):
                return None
            if path in owners and owners[path]['key'] != current['key']:
                todo.append(owners[path])
    if original.get('fingerprint') != _module_fingerprint(context, manifest, module, original.get('dependencies', [])):
        return None
    return original


def _reuse_allowed(context, manifest):
    return context.get('mode') == 'incremental' and manifest.get('complete') and manifest.get('diff', {}).get('complete') and not manifest['diff'].get('requires_full')


def _reusable_plan(context, manifest, paths):
    if not _reuse_allowed(context, manifest):
        return None
    return next((v for k, v in _baseline_artifacts(context).items() if k.startswith('plan:') and v.get('paths') == paths
                 and v.get('provenance') == _provenance(context)), None)


async def execute_stage(stage: str, inputs: dict, runtime: RuntimeContext) -> dict:
    context, payload, artifacts = inputs['context'], inputs.get('payload') or {}, inputs.get('artifacts') or {}
    # Durable identities always refer to the saved stage output. Incremental
    # semantic analysis below uses a derived manifest with impact information.
    checkpoint_artifacts = artifacts
    stage_splits.validate_splits(context, checkpoint_artifacts)
    split_plan = split_spec = None
    if payload.get('source_split') or stage == 'merge_analysis_split':
        split_plan, split_spec = stage_splits.stage_plan(context, payload, stage, checkpoint_artifacts)
    if inputs.get('repair_diagnostic'):
        runtime = replace(runtime, repair_diagnostic=str(inputs['repair_diagnostic'])[:600])
    if split_plan is not None and stage == 'analyze_module' and split_plan['inherited_repair'] == 'OUTPUT_TRUNCATED':
        compact = '上次模型输出被截断；请保持完整结构并使用更精简的输出。'
        repair = runtime.repair_diagnostic or ''
        runtime = replace(runtime, repair_diagnostic=repair if compact in repair else compact + ' ' + repair[:500])
    if context.get('workspace_id', runtime.workspace_id) != runtime.workspace_id or context.get('project_id', runtime.project_id) != runtime.project_id:
        raise StageError('AUTH_BLOCKED', '阶段所属工作区或项目不匹配')
    for value in artifacts.values():
        if isinstance(value, dict) and value.get('provenance'):
            p = value['provenance']
            if p.get('workspace_id') != runtime.workspace_id or p.get('project_id') != runtime.project_id:
                raise StageError('AUTH_BLOCKED', '不能复用其他工作区或项目的阶段产物')
    project = context['project']
    if 'manifest' in artifacts:
        artifacts = {**artifacts, 'manifest': _effective_manifest(artifacts)}
    provenance = _provenance(context, runtime)
    usage, request_meta = {'known': False, 'model_calls': 0}, {}
    try:
        if stage == 'resolve':
            artifact = await sources.probe_repository({**project, **({'target_sha': context['target_sha']} if context.get('target_sha') else {})}, runtime.credentials)
            base = context.get('base_sha')
            artifact['base_is_ancestor'] = await sources.check_ancestry(project, base, artifact, runtime.credentials) if base else None
        elif stage == 'manifest_page':
            artifact = await sources.fetch_manifest_page(project, artifacts['resolve'], payload.get('cursor'), runtime.credentials)
        elif stage == 'manifest':
            entries = {}
            for key in payload['page_keys']:
                for entry in artifacts[key]['entries']:
                    if entry['path'] in entries and entries[entry['path']] != entry:
                        raise StageError('MANIFEST_INCOMPLETE', '目录页对同一路径返回不同版本，不能合并为完整清单')
                    entries[entry['path']] = entry
            artifact = {'entries': [entries[p] for p in sorted(entries)], 'complete': bool(artifacts[payload['page_keys'][-1]]['complete']),
                        'target_sha': artifacts['resolve']['target_sha'], 'branch': artifacts['resolve']['branch'],
                        'repository_id': artifacts['resolve']['repository_id']}
            baseline = context.get('baseline') or {}
            old = (baseline.get('snapshot') or {}).get('manifest') or _baseline_artifacts(context).get('manifest')
            diff = sources.diff_manifests(old, artifact) if old else {'complete': False, 'changes': [], 'requires_full': True, 'reason': '首次分析或无兼容清单'}
            if context.get('mode') == 'incremental' and (artifacts['resolve'].get('base_is_ancestor') is not True or (old and old.get('branch') != artifact['branch'])):
                diff = {**diff, 'requires_full': True, 'reason': '历史不连续、分支改变或提供方无法确认祖先关系'}
            artifact['diff'] = diff
        elif stage == 'plan_modules':
            paths = payload['paths']
            selected = [e for e in artifacts['manifest']['entries'] if e['path'] in paths]
            cached = _reusable_plan(context, artifacts['manifest'], paths)
            if cached:
                value = {'modules': [{k: m[k] for k in ('name', 'purpose', 'paths')} for m in cached['modules']]}
                request_meta = {'cache_reused': True, 'cache_kind': 'module_plan'}
            else:
                # Keep version/security metadata in the immutable manifest. The
                # reading plan only needs each included path, its kind and size.
                planning_entries = [{key: entry.get(key) for key in ('path', 'category', 'size')} for entry in selected]
                value, usage, request_meta = await _ask('plan_modules', {'entries': planning_entries}, PlanResponse, runtime)
            planned = [p for m in value['modules'] for p in m['paths']]
            if sorted(planned) != sorted(paths) or len(planned) != len(set(planned)):
                raise StageError('INVALID_SCHEMA', '模块计划没有恰好覆盖本分区的全部合法路径')
            artifact = {'modules': [{**m, 'key': 'm' + fingerprint(sorted(m['paths']))[:20], 'paths': sorted(m['paths'])} for m in value['modules']],
                        'paths': paths, 'plan_fingerprint': fingerprint(paths)}
        elif stage == 'analyze_change_impact':
            material = _impact_input(context, artifacts['manifest'])
            if material is None:
                artifact = {'affected_paths': [], 'unknown_dependencies': True, 'reasons': [],
                            'fallback_reason': '依赖范围未知或影响资料超出一次有界核对；安全退回完整语义分析'}
            else:
                artifact, usage, request_meta = await _ask('analyze_change_impact', material, ImpactResponse, runtime)
                available = {e['path'] for e in artifacts['manifest']['entries']}
                if any(p not in available for p in artifact['affected_paths']) or any(r['path'] not in available for r in artifact['reasons']):
                    raise StageError('INVALID_CITATION', '影响分析引用了目标清单之外的路径')
        elif stage == 'read_source':
            entry = next((e for e in artifacts['manifest']['entries'] if e['path'] == payload['path'] and e.get('included')), None)
            if not entry:
                raise StageError('SOURCE_UNAVAILABLE', '读取路径没有列在本次授权清单中')
            remaining = None if runtime.max_source_bytes is None else runtime.max_source_bytes - _downloaded_bytes(artifacts)
            declared = entry.get('size')
            if remaining is not None and (remaining <= 0 or (type(declared) is int and declared > remaining)):
                raise StageError('BUDGET_EXHAUSTED', '本次分析的累计源码读取预算不足，已保存成功读取；提高预算后可继续')
            read_limit = min(remaining, sources.MAX_RESPONSE_BYTES) if remaining is not None else sources.MAX_RESPONSE_BYTES
            if type(declared) is int and declared > read_limit:
                raise StageError('SOURCE_UNAVAILABLE', '单个文件超过安全读取范围，无法完整读取')
            source = await sources.read_source(project, artifacts['resolve'], entry, runtime.credentials, max_bytes=read_limit)
            if type(source.get('download_bytes')) is not int or not 0 <= source['download_bytes'] <= read_limit:
                raise StageError('BUDGET_EXHAUSTED', '源码读取字节超出剩余预算或缺少实际计量，未继续发送模型')
            if source['source_revision'] != artifacts['resolve']['target_sha'] or source['path'] != entry['path'] or source.get('blob_oid') != entry.get('blob_oid'):
                raise StageError('INVALID_CITATION', '读取到的源码身份与固定提交或清单不一致')
            source['source_id'] = 's' + fingerprint([runtime.workspace_id, runtime.project_id, artifacts['resolve']['repository_id'],
                source['source_revision'], source['path'], source.get('blob_oid'), source['content_sha256'], SAFETY_POLICY_VERSION])[:48]
            source['workspace_id'], source['project_id'] = runtime.workspace_id, runtime.project_id
            source['safety_policy_version'] = SAFETY_POLICY_VERSION
            artifact = {'source': source, 'chunks': _split_source(source, min(8000, max(500, runtime.input_chars // 5)))}
        elif stage == 'analyze_module':
            selected = []
            for ref in payload['sources']:
                item = next((c for c in artifacts[_read_key(ref['path'])]['chunks'] if all(c[k] == ref[k] for k in ref)), None)
                if item is None:
                    raise StageError('INVALID_CITATION', '源码块版本或读取范围与阶段输入不一致')
                selected.append(item)
            value, usage, request_meta = await _ask('analyze_module', {'module': {k: payload['module'][k] for k in ('key', 'name', 'purpose')}, 'sources': selected,
                **_path_context(artifacts['manifest'], payload['module'], min(4000, runtime.input_chars // 10))}, ModuleResponse, runtime)
            _check_module_paths(value, artifacts['manifest'])
            ids = [c['id'] for c in value['claims']]
            if len(ids) != len(set(ids)):
                raise StageError('INVALID_SCHEMA', '模块结论编号重复')
            prefix = payload['analysis_key']
            for claim in value['claims']:
                if claim['kind'] != 'UNASSESSED' and not claim['citations']:
                    raise StageError('INVALID_CITATION', '有内容的项目观察必须引用实际读取来源')
                if claim.get('technology') is None or claim['kind'] != 'CODE_OBSERVED':
                    claim.pop('technology', None)
                claim['citations'] = _validate_refs(claim['citations'], selected)
                claim['id'] = prefix + ':' + claim['id']
            for index, issue in enumerate(value['issues']):
                issue['citations'] = _validate_refs(issue['citations'], selected)
                issue['claim_id'] = prefix + f':issue{index}'
                value['claims'].append({'id': issue['claim_id'], 'title': issue['title'], 'text': issue['detail'],
                    'kind': 'CODE_OBSERVED' if issue['status'] == 'observed' else 'INFERRED', 'citations': issue['citations']})
            artifact = {**value, 'module_key': payload['module']['key'], 'source_ids': sorted({s['source_id'] for s in selected}), 'validation': 'schema_and_actual_source_ranges'}
            artifact = _normalize_fact_boundaries(artifact,
                [artifacts[_read_key(ref['path'])]['source'] for ref in payload['sources']])
            if split_plan is not None:
                artifact['source_split'] = stage_splits.child_audit(split_plan, split_spec, checkpoint_artifacts)
        elif stage == 'merge_analysis_split':
            artifact = stage_splits.merge(split_plan, split_spec, checkpoint_artifacts)
        elif stage == 'module':
            parts = [artifacts[k] for k in payload['analysis_keys']]
            module = payload['module']
            artifact = {**module, 'module_key': module['key'], 'analyzed_sha': artifacts['resolve']['target_sha'],
                'claims': [c for p in parts for c in p['claims']], 'issues': [c for p in parts for c in p['issues']],
                'unknowns': [c for p in parts for c in p['unknowns']],
                'dependencies': sorted({d['path'] for p in parts for d in p['dependencies']} |
                                       (set(payload['read_paths']) - set(module['paths']))),
                'dependency_scope_complete': all(p['dependency_scope_complete'] for p in parts),
                'sources': [artifacts[_read_key(p)]['source'] for p in payload['read_paths']], 'coverage_status': 'COMPLETE'}
            artifact['boundary_adjustments'] = [a for part in parts for a in part.get('boundary_adjustments', [])]
            split_fingerprints = {key: fingerprint(artifacts[key]) for key in payload['analysis_keys'] if artifacts[key].get('source_split')}
            if split_fingerprints:
                artifact['source_split_fingerprints'] = split_fingerprints
            artifact = _normalize_fact_boundaries(artifact, artifact['sources'])
            artifact = _apply_claim_reviews(artifact, context, artifacts['resolve']['target_sha'])
            artifact['fingerprint'] = _module_fingerprint(context, artifacts['manifest'], module, artifact['dependencies'])
        elif stage == 'reuse_module':
            original = _reusable_module(context, artifacts['manifest'], payload['module'])
            if not original:
                raise StageError('SOURCE_UNAVAILABLE', '缓存兼容条件已改变，需要重新分析该模块')
            artifact = deepcopy(original)
            artifact = _normalize_fact_boundaries(artifact, artifact.get('sources', []))
            artifact = _apply_claim_reviews(artifact, context, artifacts['resolve']['target_sha'])
            artifact['compatibility'] = {'target_sha': artifacts['resolve']['target_sha'], 'manifest_hash': fingerprint(artifacts['manifest']['entries']),
                'dependency_fingerprint': _module_fingerprint(context, artifacts['manifest'], payload['module'], original['dependencies']),
                'reused_from_sha': original['analyzed_sha'], 'reason': '模块及显式依赖的 blob、策略和模型版本均未改变'}
        elif stage in ('cross_module', 'explain_section'):
            contract = _claim_contract(context, checkpoint_artifacts)
            if payload.get('input_contract_version', 'legacy') != contract:
                raise StageError('CONFIG_CHANGED', '阶段输入契约未被本次任务明确选择')
            claims = [c for c in _claims(artifacts, payload['module_keys']) if c['id'] in payload['claim_ids']]
            material = _claim_material(payload, claims)
            if stage == 'cross_module':
                value, usage, request_meta = await _ask('verify_cross_module', material, CrossResponse, runtime)
                _validate_claim_links(value['observations'] + value['conflicts'], claims)
            else:
                value, usage, request_meta = await _ask('explain_project_section', {'section': payload['title'], **material,
                    'changes': _change_context(artifacts['manifest']['diff'], claims) if payload['section'] == 'changes' else None,
                    'personal_contribution': '未由用户确认；不能推断'}, SectionResponse, runtime)
                _validate_claim_links(value['paragraphs'], claims)
            artifact = {**value, 'claim_ids': [c['id'] for c in claims], **({'section': payload['section'], 'title': payload['title']} if stage == 'explain_section' else {})}
        elif stage == 'report':
            contract = _claim_contract(context, checkpoint_artifacts)
            artifact = _assemble_report(context, artifacts, payload)
            if contract != 'legacy':
                audit = _aggregate_claim_contract(checkpoint_artifacts, 'report')
                artifact.update(deepcopy(audit))
                artifact['report'].update(deepcopy(audit))
                artifact['snapshot'].update(deepcopy(audit))
        elif stage == 'extract_candidates':
            contract = _claim_contract(context, checkpoint_artifacts)
            if payload.get('input_contract_version', 'legacy') != contract:
                raise StageError('CONFIG_CHANGED', '阶段输入契约未被本次任务明确选择')
            report = artifacts['report']['report']
            if artifacts['report']['coverage_status'] != 'COMPLETE':
                raise StageError('SOURCE_UNAVAILABLE', '部分报告不能生成长期记忆候选')
            claims = [c for c in report['claims'] if c['id'] in payload['claim_ids']]
            material = _claim_material(payload, claims)
            value, usage, request_meta = await _ask('extract_memory_candidates', {**material,
                'issues': [{'id': i['id'], 'claim_id': i['claim_id']} for i in report['issues'] if i['claim_id'] in payload['claim_ids']],
                'confirmed_memories': context.get('confirmed_memories') or [], 'existing_candidates': context.get('existing_candidates') or []}, CandidateResponse, runtime)
            _validate_claim_links(value['candidates'], claims)
            claim_map = {c['id']: c for c in claims}
            issues = {i['id']: i for i in report['issues']}
            for candidate in value['candidates']:
                kinds = {claim_map[k]['kind'] for k in candidate['claim_ids']}
                if candidate['fact_type'] == 'CODE_OBSERVED' and kinds != {'CODE_OBSERVED'}:
                    raise StageError('INVALID_CITATION', '记忆候选把推断或文档声明提升成了源码事实')
                related = [i['id'] for i in report['issues'] if i['claim_id'] in candidate['claim_ids']]
                if not candidate.get('issue_id') and len(related) == 1:
                    candidate['issue_id'] = related[0]
                if candidate.get('issue_id') and (candidate['issue_id'] not in issues or issues[candidate['issue_id']]['claim_id'] not in candidate['claim_ids']):
                    raise StageError('INVALID_CITATION', '记忆候选的问题与来源结论不一致')
                candidate['mtype'] = 'ProjectIssue' if candidate.get('issue_id') else 'ProjectUnderstanding'
                candidate['citations'] = [ref for k in candidate['claim_ids'] for ref in claim_map[k]['citations']]
            artifact = value
        elif stage == 'candidates':
            contract = _claim_contract(context, checkpoint_artifacts)
            candidates = [c for k in payload['candidate_keys'] for c in artifacts[k]['candidates']]
            unique = {}
            for c in candidates:
                key = fingerprint([c['content'], sorted(c['claim_ids']), c['fact_type']])
                unique.setdefault(key, c)
            artifact = {'candidates': list(unique.values())}
            if contract != 'legacy':
                artifact.update(_aggregate_claim_contract(checkpoint_artifacts, 'candidates'))
        else:
            raise StageError('INVALID_SCHEMA', '未知分析阶段')
    except sources.SourceError as exc:
        raise StageError(exc.category, str(exc), exc.retry_after, exc.request_id) from None
    except StageError as exc:
        raise _attach_request(exc, usage, request_meta) from None
    if payload.get('input_contract_version'):
        artifact['input_contract_version'] = payload['input_contract_version']
        artifact['claim_input_fingerprint'] = payload['claim_input_fingerprint']
        artifact['claim_input_claim_ids'] = [c['id'] for c in claims]
    artifact['provenance'] = provenance
    return {'artifact': artifact, 'usage': usage, 'request_meta': request_meta}


def _module_fingerprint(context, manifest, module, dependencies):
    paths = set(module['paths']) | set(dependencies)
    by_path = {e['path']: e for e in manifest['entries']}
    return fingerprint({'scope': _provenance(context), 'paths': [(p, by_path.get(p, {}).get('blob_oid'), by_path.get(p, {}).get('mode')) for p in sorted(paths)]})


def _assemble_report(context, artifacts, payload):
    modules = [artifacts[k] for k in payload['module_keys']]
    claims = [c for m in modules for c in m['claims']]
    issues = [{**i, 'id': f'I{n}'} for n, i in enumerate([i for m in modules for i in m['issues']], 1)]
    source_map = {s['source_id']: s for m in modules for s in m['sources']}
    for claim in claims:
        chunks = [{'source_id': s['source_id'], 'source_version': s['source_revision'], 'path': s['path'], 'start_line': 1, 'end_line': s['line_count']} for s in source_map.values()]
        checked = _validate_refs([{k: r[k] for k in ('source_id', 'start_line', 'end_line')} for r in claim['citations']], chunks)
        if checked != claim['citations']:
            raise StageError('INVALID_CITATION', '报告来源版本与原始引用不一致')
    sections = []
    for name, title in SECTIONS:
        parts = [artifacts[k] for k in payload['section_keys'] if artifacts[k]['section'] == name]
        sections.append({'id': name, 'title': title, 'paragraphs': [p for part in parts for p in part['paragraphs']],
                         'unknowns': [u for part in parts for u in part['unknowns']]})
    manifest = artifacts['manifest']
    complete = bool(modules and claims and manifest['complete'] and all(s['paragraphs'] for s in sections)
                    and all(m['coverage_status'] == 'COMPLETE' for m in modules))
    source_paths = {s['path'] for s in source_map.values()}
    eligible = {e['path'] for e in manifest['entries'] if e.get('included')}
    complete = complete and eligible <= source_paths
    coverage = {'files_read': len(source_paths), 'eligible_files': len(eligible), 'tree_truncated': not manifest['complete'],
                'unread_files': sorted(eligible - source_paths), 'truncated_files': [], 'failed_files': [],
                'scope': 'planned_modules_read_without_repository_execution', 'modules_read': len(modules),
                'source_download_bytes': _downloaded_bytes(artifacts)}
    unknowns = list(dict.fromkeys([u for m in modules for u in m['unknowns']] +
        [u for k in payload['cross_keys'] for u in artifacts[k]['unknowns']] + ['仅进行只读源码分析，未安装依赖或运行测试；个人贡献尚未确认。']))
    normalized_sources = [{k: s[k] for k in ('source_id', 'path', 'source_revision', 'encoding', 'normalizations')}
                          for s in source_map.values() if s.get('normalizations')]
    quarantined = [q for m in modules for q in m.get('quarantined_claims', [])]
    if quarantined:
        review_notes = list(dict.fromkeys(u for m in modules for u in m['unknowns']
                                         if u.startswith('独立源码复核 ')))
        next(s for s in sections if s['id'] == 'limitations')['paragraphs'].extend(
            {'text': note, 'claim_ids': []} for note in review_notes)
    if normalized_sources:
        note = (f"本次 {len(normalized_sources)} 个严格 UTF-8 文本文件中的稀疏 NUL 已作可逆可见转义；"
                '原始行号保持不变，来源保存字符坐标、转义表及脱敏后原文哈希。显示转义不等于原代码原本写了转义串，也不证明代码运行正确。')
        unknowns.append(note)
        next(s for s in sections if s['id'] == 'limitations')['paragraphs'].append({'text': note, 'claim_ids': []})
    architecture = [{'title': c['title'], 'detail': c['text'], 'citations': c['citations'], 'claim_id': c['id'], 'fact_type': c['kind']}
                    for c in claims if c['citations'] and ':issue' not in c['id']]
    report = {'schema_version': SCHEMA_VERSION, 'summary': '\n'.join(p['text'] for p in sections[0]['paragraphs']) if sections else '',
              'architecture': architecture, 'issues': issues, 'unknowns': unknowns, 'sections': sections, 'claims': claims,
              'technologies_assessed': True,
              'modules': [{k: v for k, v in m.items() if k != 'sources'} for m in modules], 'changes': manifest['diff'],
              'cross_module': [artifacts[k] for k in payload['cross_keys']], 'source_normalizations': normalized_sources,
              'quarantined_claims': quarantined}
    return {'report': report, 'source_revision': artifacts['resolve']['target_sha'], 'coverage_status': 'COMPLETE' if complete else 'PARTIAL',
            'snapshot': {'source_registry': source_map, 'files': list(source_map.values()), 'manifest': manifest, 'coverage': coverage,
                         'modules': modules, 'provenance': _provenance(context)}}
