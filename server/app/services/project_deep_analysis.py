"""Bounded multi-pass LLM understanding of inspected project source.

The output describes code and possible engineering risks, never personal mastery.
No report is produced when the model fails or its references are invalid.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.generation_i18n import generation_message, with_output_language
from app.i18n import request_language
from app.services.llm import AgentAdapter, get_llm_config
from app.services.project_report_i18n import report_message
from app.services.project_sources import SourceRepository, open_repository, redact_source, redact_value

MAX_INITIAL_FILES = 12
MAX_FOLLOWUP_FILES = 8
MAX_FILE_CHARS = 16_000
MAX_TOTAL_CHARS = 180_000
CHUNK_REVIEW_THRESHOLD = 60_000
MAX_MODULE_CHARS = 30_000
MAX_SYNTHESIS_CHARS = 60_000
_LOW_EFFORT_MODELS = frozenset(('deepseek-v4-flash', 'deepseek-flash', 'deepseek-v4-pro'))
_SKILLS = Literal["agent", "tools", "memory", "rag", "eval", "backend", "reliability", "deploy"]


class ProjectAnalysisError(ValueError):
    """An internally authored, credential-free message safe for the job API.

    Never construct this with raw remote/model messages or source content.
    Unexpected programming errors remain other exception types for the runner.
    """
    safe_for_user = True


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Citation(StrictModel):
    path: str = Field(min_length=1, max_length=512)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class Architecture(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=6000)
    citations: list[Citation] = Field(min_length=1, max_length=10)


class Issue(StrictModel):
    id: str = Field(pattern=r"^I[1-9][0-9]{0,2}$")
    title: str = Field(min_length=1, max_length=200)
    skill_key: _SKILLS
    severity: Literal["low", "medium", "high"]
    detail: str = Field(min_length=1, max_length=4000)
    impact: str = Field(min_length=1, max_length=2000)
    recommendation: str = Field(min_length=1, max_length=3000)
    status: Literal["observed", "hypothesis"]
    citations: list[Citation] = Field(min_length=1, max_length=10)
    interview_questions: list[str] = Field(min_length=1, max_length=5)


class Report(StrictModel):
    schema_version: Literal[1]
    summary: str = Field(min_length=1, max_length=5000)
    architecture: list[Architecture] = Field(min_length=1, max_length=12)
    issues: list[Issue] = Field(max_length=12)
    unknowns: list[str] = Field(max_length=30)


def validate_report(value: dict, files: list[dict]) -> dict:
    try:
        report = Report.model_validate(value).model_dump()
    except ValidationError as exc:
        fields = set().union(*(set(model.model_fields) for model in (Report, Issue, Architecture, Citation)))
        locations = ", ".join(".".join(str(part) if isinstance(part, int) or part in fields else "未知字段"
                                      for part in error["loc"]) for error in exc.errors()[:4])
        raise ProjectAnalysisError(f"模型报告结构无效：{locations}；未保存为成功分析") from None
    by_path = {item["path"]: len(item["content"].splitlines()) for item in files}
    ids = [issue["id"] for issue in report["issues"]]
    if len(ids) != len(set(ids)):
        raise ProjectAnalysisError("模型报告的问题编号重复")
    for entry in report["architecture"] + report["issues"]:
        for citation in entry["citations"]:
            start, end = citation["start_line"], citation["end_line"]
            if citation["path"] not in by_path or not 1 <= start <= end <= by_path[citation["path"]]:
                raise ProjectAnalysisError("模型报告引用了未读取文件或无效行号；请重试分析")
    for value in report["unknowns"] + [q for issue in report["issues"] for q in issue["interview_questions"]]:
        if not value.strip() or len(value) > 2000:
            raise ProjectAnalysisError("模型报告的问题或未验证事项为空或过长")
    return report


def _json_object(text: str) -> dict:
    raw = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", raw, flags=re.S)
    if fence:
        raw = fence.group(1)
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise ProjectAnalysisError("模型未返回有效 JSON；未生成分析结果") from None
    if not isinstance(value, dict):
        raise ProjectAnalysisError("模型响应必须是 JSON 对象")
    return value


def _selected_paths(value: dict, available: set[str], limit: int, required: bool = False) -> list[str]:
    if set(value) != {"paths", "reason"} or not isinstance(value.get("reason"), str):
        raise ProjectAnalysisError("模型选文件响应必须包含 paths 和 reason")
    paths = value.get("paths")
    if not isinstance(paths, list) or len(paths) > limit or (required and not paths):
        raise ProjectAnalysisError("模型选择的文件数量无效")
    if any(not isinstance(path, str) or path not in available for path in paths):
        raise ProjectAnalysisError("模型选择了未列出或不可读取的文件路径")
    if len(set(paths)) != len(paths):
        raise ProjectAnalysisError("模型重复选择文件路径")
    return paths


def _numbered(files: list[dict]) -> list[dict]:
    return [{"path": item["path"], "truncated": item["truncated"],
             "source": "\n".join(f"{i}: {line}" for i, line in enumerate(item["content"].splitlines(), 1))}
            for item in files]


async def _ask_model(cfg: dict, stage: str, instruction: str, payload: dict, calls: list[dict]) -> dict:
    # Each concurrent request owns its diagnostic state. No shared adapter can
    # overwrite another request's last_error while modules are being reviewed.
    adapter = AgentAdapter()
    timeout = 300.0 if stage == 'final_report' else 180.0
    options = {'reasoning_effort': 'low'} if cfg.get('model') in _LOW_EFFORT_MODELS else {}
    system = with_output_language(generation_message('project.deep.system') + '\n' + instruction)
    try:
        result = await asyncio.wait_for(adapter.chat_with(cfg, system,
            json.dumps(payload, ensure_ascii=False), json_mode=True, timeout=timeout, **options), timeout=timeout)
    except TimeoutError:
        raise ProjectAnalysisError(f'LLM 模型响应超时（{stage}，总等待上限 {int(timeout)} 秒）；未生成分析结果') from None
    except (ValueError, httpx.HTTPError):
        raise ProjectAnalysisError(f'LLM 模型请求失败（{stage}）；请检查模型服务配置') from None
    if not result or not isinstance(result.get('content'), str):
        diagnostic = getattr(adapter, 'last_error', None) or {}
        reasons = {'timeout': f'模型响应超时（等待上限 {int(timeout)} 秒）',
                   'network': '模型服务网络连接失败', 'invalid_response': '模型服务响应格式无效',
                   'configuration': '模型服务配置不完整'}
        reason = reasons.get(diagnostic.get('kind'), '模型调用失败')
        if diagnostic.get('kind') == 'http':
            code = diagnostic.get('status')
            reason = f'模型服务返回 HTTP {code}' if type(code) is int and 100 <= code <= 599 else '模型服务返回 HTTP 错误'
        raise ProjectAnalysisError(f'LLM {reason}（{stage}）；未用静态扫描冒充 AI 分析')
    calls.append({'stage': stage, 'usage': result.get('usage') or {}})
    return redact_value(_json_object(result['content']))


def _module_groups(files):
    groups, current, size = [], [], 0
    for item in files:
        amount = len(item['content'])
        if amount > MAX_MODULE_CHARS:
            raise ProjectAnalysisError('单个源码快照超过分组上限，请按读取限制重新分析')
        if current and size + amount > MAX_MODULE_CHARS:
            groups.append(current)
            current, size = [], 0
        current.append(item)
        size += amount
    if current:
        groups.append(current)
    return groups


def _brief_context(context):
    personal = json.dumps(context.get('personal_context') or {}, ensure_ascii=False)
    return {'project_name': str(context.get('project_name') or '')[:255],
            'source_revision': context.get('source_revision'), 'focus': str(context.get('focus') or '')[:3000],
            'personal_context_excerpt': personal[:4000], 'personal_context_truncated': len(personal) > 4000}


def _brief_coverage(coverage):
    return {**{key: coverage.get(key) for key in ('files_read', 'eligible_files', 'tree_truncated', 'chars_read', 'scope')},
            'unread_count': len(coverage.get('unread_files') or []),
            'truncated_count': len(coverage.get('truncated_files') or []),
            'failed_count': len(coverage.get('failed_files') or [])}


def _compact_report(value, width):
    """Keep full module reports in the snapshot, send bounded extracts to synthesis."""
    if isinstance(value, str):
        return value if len(value) <= width else value[:width] + '…（摘要截断）'
    if isinstance(value, list):
        return [_compact_report(item, width) for item in value]
    if isinstance(value, dict):
        return {key: item if key in ('citations', 'id', 'skill_key', 'status', 'severity') else _compact_report(item, width)
                for key, item in value.items()}
    return value


def _citation_excerpts(files, module_reviews, budget):
    by_path = {item['path']: item['content'].splitlines() for item in files}
    refs = sorted({(c['path'], c['start_line'], c['end_line']) for review in module_reviews
                   for entry in review['report']['architecture'] + review['report']['issues'] for c in entry['citations']})
    per_excerpt = min(1500, max(30, budget // max(1, len(refs))))
    excerpts = []
    for path, start, end in refs:
        excerpt_end = min(end, start + 14)
        full = '\n'.join(f'{i}: {by_path[path][i - 1]}' for i in range(start, excerpt_end + 1))
        excerpt = full[:per_excerpt]
        shown_end = start + len(excerpt.splitlines()) - 1
        excerpts.append({'path': path, 'start_line': start, 'end_line': end,
                         'excerpt_end_line': shown_end, 'excerpt': excerpt,
                         'excerpt_truncated': excerpt_end < end or len(full) > per_excerpt})
    return excerpts


def _synthesis_payload(files, context, coverage, module_reviews, schema):
    # The final request never repeats the full directory or all raw source. Bound
    # both review summaries and citation snippets independently of model verbosity.
    for width, budget in ((250, 12000), (150, 8000), (80, 4000)):
        payload = {**_brief_context(context), 'coverage': _brief_coverage(coverage),
                   'module_reviews': [{'module_index': r['module_index'], 'paths': r['paths'],
                                       'report': _compact_report(r['report'], width)} for r in module_reviews],
                   'citation_excerpts': _citation_excerpts(files, module_reviews, budget),
                   'json_schema': schema,
                   'review_note': generation_message('project.deep.review_note')}
        if len(json.dumps(payload, ensure_ascii=False)) <= MAX_SYNTHESIS_CHARS:
            return payload
    raise ProjectAnalysisError('综合阶段（final_report）的已验证模块资料仍超过 60000 字符输入上限，请缩小分析范围；未省略模块或引用结构冒充完整报告')


def _result_meta(cfg, calls):
    usage = {key: sum(c['usage'].get(key, 0) or 0 for c in calls)
             for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    meta = {'provider': cfg.get('provider'), 'model': cfg.get('model'), 'calls': len(calls),
            'stages': calls, 'usage': usage, 'analysis_version': 'project-deep-v2'}
    if cfg.get('model') in _LOW_EFFORT_MODELS:
        # This records a requested option, not proof that a compatible gateway
        # forwarded or honored it, nor a confirmed explanation of prior timeouts.
        meta['requested_reasoning_effort'] = 'low'
    return meta


async def review_inspected_sources(files: list[dict], context: dict, coverage: dict, *, cfg: dict | None = None,
                                   progress: Callable[[str], Awaitable[None]] | None = None,
                                   calls: list[dict] | None = None) -> dict:
    """Review an already captured real source snapshot without refetching it.

    Used by analyze_project and by isolated acceptance replay. `files` contain raw
    redacted content (not line-numbered prompt text), paths and truncated flags.
    Replaying is still actual model work; it does not substitute canned findings.
    """
    cfg = cfg if cfg is not None else await get_llm_config()
    calls = calls if calls is not None else []
    if not files or len(files) > MAX_INITIAL_FILES + MAX_FOLLOWUP_FILES or sum(len(f['content']) for f in files) > MAX_TOTAL_CHARS:
        raise ProjectAnalysisError('源码快照为空或超出本次分析读取预算')
    async def phase(stage):
        if progress is not None:
            await progress(stage)
    schema = Report.model_json_schema()
    module_reviews = []
    candidate = {**context, 'inspected_files': _numbered(files), 'coverage': coverage, 'json_schema': schema}
    # Line numbers, the directory, coverage and schema also consume model input.
    # Route by the serialized payload as well as raw source size.
    if (sum(len(f['content']) for f in files) > CHUNK_REVIEW_THRESHOLD
            or len(json.dumps(candidate, ensure_ascii=False)) > MAX_SYNTHESIS_CHARS):
        await phase('review_modules')
        groups = _module_groups(files)
        semaphore = asyncio.Semaphore(2)
        async def review_group(index, group):
            async with semaphore:
                raw = await _ask_model(cfg, f'review_modules:{index}',
                    generation_message('project.deep.review_modules'),
                    {**_brief_context(context), 'module_index': index, 'module_count': len(groups),
                     'inspected_files': _numbered(group), 'coverage': _brief_coverage(coverage), 'json_schema': schema}, calls)
                validated = validate_report(raw, group)
                # Compact output is a model preference, not a second schema.
                # Preserve every valid observation and citation; the bounded
                # synthesis payload handles verbosity without discarding entries.
                return {'module_index': index, 'paths': [f['path'] for f in group], 'report': validated,
                        'chars_read': sum(len(f['content']) for f in group)}
        tasks = [asyncio.create_task(review_group(index, group)) for index, group in enumerate(groups, 1)]
        try:
            module_reviews = await asyncio.gather(*tasks)
        except BaseException:
            # Cancel remaining in-flight/queued paid requests when any module
            # fails. No partial set is promoted to a successful whole report.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        await phase('final_report')
        payload = _synthesis_payload(files, context, coverage, module_reviews, schema)
    else:
        await phase('final_report')
        payload = candidate
    raw_report = await _ask_model(cfg, 'final_report', generation_message('project.deep.final_report'), payload, calls)
    await phase('validate_report')
    final_report = validate_report(raw_report, files)
    language = request_language()
    scope_note = report_message('coverage.legacy_read', language, read=len(files),
                                eligible=coverage.get('eligible_files', len(files)))
    if coverage.get('tree_truncated'):
        scope_note += report_message('coverage.legacy_tree_truncated', language)
    if coverage.get('truncated_files'):
        scope_note += report_message('coverage.legacy_files_truncated', language)
    final_report['unknowns'] = final_report['unknowns'][:29] + [scope_note]
    return {'report': final_report, 'module_reviews': module_reviews, 'result_meta': _result_meta(cfg, calls)}


async def analyze_project(
    project_info: dict, focus: str = "", personal_context: dict | None = None,
    progress: Callable[[str], Awaitable[None]] | None = None,
    *, open_source: Callable[[dict], Awaitable[SourceRepository]] | None = None,
) -> dict:
    async def phase(stage: str):
        if progress is not None:
            await progress(stage)

    cfg = await get_llm_config()
    if not all(cfg.get(key) for key in ("provider", "api_key", "base_url", "model")):
        raise ProjectAnalysisError("未配置完整 LLM，无法执行 AI 深度分析；请先配置模型")
    await phase("fetch_repo")
    try:
        repository = await (open_source or open_repository)(project_info)
    except (ValueError, httpx.HTTPError):
        raise ProjectAnalysisError("读取仓库或提交版本失败；请检查仓库地址、分支及访问配置") from None
    if not repository.manifest:
        raise ProjectAnalysisError("当前版本未读取到可分析的源码文件；仓库可能过大、受限或仅包含被排除的文件")
    calls: list[dict] = []

    async def ask(stage: str, instruction: str, payload: dict) -> dict:
        return await _ask_model(cfg, stage, instruction, payload, calls)

    context = {
        "project_name": redact_source(str(project_info.get("name") or ""))[:255],
        "source_revision": repository.revision,
        "focus": redact_source(focus)[:3000],
        "personal_context": redact_value(personal_context or {}),
        "manifest": repository.manifest,
        "tree_truncated": repository.tree_truncated,
    }
    await phase("select_files")
    choice = await ask("select_files", generation_message('project.deep.select_files').format(limit=MAX_INITIAL_FILES), context)
    paths = _selected_paths(choice, {item["path"] for item in repository.manifest}, MAX_INITIAL_FILES, required=True)
    files: list[dict] = []
    failed: list[str] = []
    attempted: set[str] = set()
    total_chars = 0

    async def read_paths(selected: list[str]):
        nonlocal total_chars
        for path in selected:
            if total_chars >= MAX_TOTAL_CHARS:
                break
            attempted.add(path)
            try:
                content = await repository.read_file(path)
            except (ValueError, httpx.HTTPError):
                # A failed source read is visible in coverage. If no source can
                # be read, abort rather than ask a model to invent architecture.
                content = None
            if not content or "\x00" in content:
                failed.append(path)
                continue
            content = redact_source(content)
            limit = min(MAX_FILE_CHARS, MAX_TOTAL_CHARS - total_chars)
            truncated = len(content) > limit
            if truncated:
                content = content[:limit]
                # Keep complete source lines for unambiguous line citations.
                if "\n" in content:
                    content = content.rsplit("\n", 1)[0]
            total_chars += len(content)
            files.append({"path": path, "content": content, "line_count": len(content.splitlines()),
                          "truncated": truncated, "sha256": hashlib.sha256(content.encode()).hexdigest()})

    await phase("read_initial")
    await read_paths(paths)
    if not files:
        raise ProjectAnalysisError("所选源码文件均未读取成功；未生成 AI 分析")
    await phase("followup_files")
    followup = await ask("followup_files", generation_message('project.deep.followup_files').format(limit=MAX_FOLLOWUP_FILES),
                         {**context, "inspected_files": _numbered(files), "unavailable_files": failed,
                          "already_attempted": sorted(attempted)})
    followup_paths = _selected_paths(followup, {item["path"] for item in repository.manifest} - attempted, MAX_FOLLOWUP_FILES)
    await phase("read_followup")
    await read_paths(followup_paths)
    read_set = {item["path"] for item in files}
    coverage = {
        "files_read": len(files), "eligible_files": len(repository.manifest),
        "tree_truncated": repository.tree_truncated,
        "unread_files": [item["path"] for item in repository.manifest if item["path"] not in read_set],
        "truncated_files": [item["path"] for item in files if item["truncated"]],
        "failed_files": failed, "excluded_files": getattr(repository, "excluded_files", 0),
        "chars_read": total_chars, "max_files": MAX_INITIAL_FILES + MAX_FOLLOWUP_FILES,
        "max_chars": MAX_TOTAL_CHARS, "scope": "bounded_source_review",
    }
    reviewed = await review_inspected_sources(files, context, coverage, cfg=cfg, progress=progress, calls=calls)
    snapshot = {"schema_version": 1, "kind": repository.kind, "branch": repository.branch,
                "source_revision": repository.revision, "files": files, "coverage": coverage,
                "focus": context["focus"], "personal_context": context["personal_context"],
                "selection": {"initial": choice, "followup": followup}, 'module_reviews': reviewed['module_reviews']}
    return {"source_revision": repository.revision, "snapshot": snapshot, "report": reviewed['report'],
            "result_meta": reviewed['result_meta']}
