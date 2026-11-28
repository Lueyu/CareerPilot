"""Grounded project analysis tests: all network and model calls are fixtures."""
import asyncio
import importlib
import importlib.util
import json
import re

import httpx
import pytest

from app.i18n import message, using_language


def module(name):
    assert importlib.util.find_spec(name), f"{name} must implement grounded repository analysis"
    return importlib.import_module(name)


REVISION = "a" * 40


def report():
    return {
        "schema_version": 1,
        "summary": "A small API delegates retrieval to a separate service.",
        "architecture": [{"title": "Request flow", "detail": "The route calls search.",
            "citations": [{"path": "app/api.py", "start_line": 1, "end_line": 2}]}],
        "issues": [{"id": "I1", "title": "No explicit timeout", "skill_key": "reliability",
            "severity": "medium", "detail": "The inspected call passes no timeout.",
            "impact": "A slow dependency may hold a request open.",
            "recommendation": "Check client defaults and add a bounded timeout.",
            "status": "hypothesis",
            "citations": [{"path": "app/search.py", "start_line": 1, "end_line": 2}],
            "interview_questions": ["How would you choose a timeout budget?"]}],
        "unknowns": ["The dependency's default timeout has not been inspected."],
    }


class RepositoryFixture:
    revision = REVISION
    branch = "main"
    kind = "github"
    tree_truncated = False
    files = {"app/api.py": "from .search import search\ndef route(): return search()\n",
             "app/search.py": "def search():\n    return client.get('/search')\n",
             "README.md": "# Search API\n"}
    manifest = [{"path": path, "size": len(value)} for path, value in files.items()]

    def __init__(self):
        self.reads = []

    async def read_file(self, path):
        self.reads.append(path)
        return self.files[path]


async def install_engine_fixture(monkeypatch, responses):
    engine = module("app.services.project_deep_analysis")
    repo = RepositoryFixture()
    prompts = []

    async def open_repo(info):
        return repo

    async def config():
        return {"provider": "fixture", "api_key": "fixture-key", "base_url": "https://invalid.example", "model": "fixture"}

    async def chat(self, cfg, system, user, **kwargs):
        prompts.append((system, json.loads(user)))
        value = responses[len(prompts) - 1]
        return None if value is None else {"content": json.dumps(value), "usage": {"total_tokens": 10}}

    monkeypatch.setattr(engine, "open_repository", open_repo)
    monkeypatch.setattr(engine, "get_llm_config", config)
    monkeypatch.setattr(engine.AgentAdapter, "chat_with", chat)
    return engine, repo, prompts


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['zh-CN', 'en'])
async def test_analysis_reads_followup_sources_and_preserves_grounded_version(monkeypatch, language):
    engine, repo, prompts = await install_engine_fixture(monkeypatch, [
        {"paths": ["app/api.py"], "reason": "Trace the entry point"},
        {"paths": ["app/search.py"], "reason": "Inspect the called service"}, report(),
    ])
    original = '我负责维护搜索服务。'
    with using_language(language):
        result = await engine.analyze_project({"id": 1, "kind": "github", "name": "Fixture"}, "Review reliability",
                                              {"role": "developer", "statement": original})
    assert repo.reads == ["app/api.py", "app/search.py"]
    assert result["source_revision"] == REVISION
    assert result["snapshot"]["coverage"]["files_read"] == 2
    assert "README.md" in result["snapshot"]["coverage"]["unread_files"]
    assert result["snapshot"]["files"][0]["content"].startswith("from .search")
    assert result["report"]["issues"][0]["status"] == "hypothesis"
    assert result["result_meta"]["calls"] == 3
    assert result["result_meta"]["usage"]["total_tokens"] == 30
    assert ("个人能力" if language == 'zh-CN' else "personal ability") in prompts[-1][0]
    for system, payload in prompts:
        assert message('language', language) in system
        assert payload['personal_context']['statement'] == original
        if language == 'en':
            assert not re.search(r'[\u3400-\u9fff]', system)
    assert "app/search.py" in json.dumps(prompts[-1], ensure_ascii=False)
    assert "fixture-key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_bad_citations_and_personal_scores_are_rejected(monkeypatch):
    bad = report()
    bad["issues"][0]["citations"][0]["end_line"] = 999
    engine, _, _ = await install_engine_fixture(monkeypatch, [
        {"paths": ["app/api.py"], "reason": "entry"},
        {"paths": ["app/search.py"], "reason": "dependency"}, bad,
    ])
    with pytest.raises(ValueError, match="引用|citation"):
        await engine.analyze_project({"kind": "github"})
    bad = report()
    bad["personal_score"] = 0
    with pytest.raises(ValueError):
        engine.validate_report(bad, [{"path": k, "content": v} for k, v in RepositoryFixture.files.items()])


@pytest.mark.asyncio
async def test_model_failure_never_becomes_static_success(monkeypatch):
    engine, _, _ = await install_engine_fixture(monkeypatch, [None])
    with pytest.raises(ValueError, match="模型|LLM"):
        await engine.analyze_project({"kind": "github"})


@pytest.mark.asyncio
async def test_model_cannot_request_unlisted_or_sensitive_files(monkeypatch):
    engine, repo, _ = await install_engine_fixture(monkeypatch, [{"paths": [".env"], "reason": "steal config"}])
    with pytest.raises(ValueError, match="文件|path"):
        await engine.analyze_project({"kind": "github"})
    assert repo.reads == []


def test_safe_paths_and_line_preserving_redaction():
    sources = module("app.services.project_sources")
    for path in (".env", ".env.production", "config/secrets.json", "id_rsa", "keys/prod.pem", "../api.py", "node_modules/lib/a.js", "data/answers.json", "snapshot.dump"):
        assert not sources.is_safe_source_path(path), path
    assert sources.is_safe_source_path("server/app/config.py")
    assert sources.is_safe_source_path("README.md")
    original = 'api_key = "sk-fixtureSecretValue1234567890"\npassword: "private-value"\nurl = "postgresql://alice:password123@db/main"\nprint("ok")\n'
    redacted = sources.redact_source(original)
    assert len(redacted.splitlines()) == len(original.splitlines())
    for value in ("sk-fixtureSecretValue1234567890", "private-value", "password123"):
        assert value not in redacted
    assert 'print("ok")' in redacted


@pytest.mark.asyncio
async def test_repository_resolves_commit_before_tree_and_reads_at_same_revision(monkeypatch):
    sources = module("app.services.project_sources")
    calls = []

    async def token(): return "never-in-snapshot"
    async def get(url, token, **kwargs):
        calls.append((url, kwargs))
        class Response:
            def json(self): return {"sha": REVISION}
        return Response()
    async def tree(owner, repo, token, branch):
        assert branch == REVISION
        return [{"path": "app/api.py", "type": "blob", "size": 40}, {"path": ".env", "type": "blob", "size": 20}]
    async def read(self, path):
        assert self.revision == REVISION
        return "print('ok')"
    monkeypatch.setattr(sources.github_repo, "get_github_token", token)
    monkeypatch.setattr(sources.github_repo, "_get", get)
    monkeypatch.setattr(sources.github_repo, "fetch_tree", tree)
    monkeypatch.setattr(sources.SourceRepository, "_read_text", read)
    repo = await sources.open_repository({"kind": "github", "owner": "example", "repo": "fixture", "branch": "main"})
    assert repo.revision == REVISION
    assert [f["path"] for f in repo.manifest] == ["app/api.py"]
    assert await repo.read_file("app/api.py") == "print('ok')"
    assert calls[0][0].endswith("/commits/main")


@pytest.mark.asyncio
async def test_file_budget_records_unread_and_truncated_content(monkeypatch):
    engine, repo, _ = await install_engine_fixture(monkeypatch, [
        {"paths": ["app/api.py"], "reason": "entry"},
        {"paths": ["app/search.py"], "reason": "dependency"}, report(),
    ])
    monkeypatch.setattr(engine, "MAX_FILE_CHARS", 60)
    repo.files = {**repo.files, "app/api.py": "from .search import search\ndef route(): return search()\n" + "# later\n" * 20}
    result = await engine.analyze_project({"kind": "github"})
    assert "app/api.py" in result["snapshot"]["coverage"]["truncated_files"]
    assert len(result["snapshot"]["files"][0]["content"]) <= 60
    assert result["snapshot"]["files"][0]["truncated"] is True


@pytest.mark.asyncio
async def test_context_and_model_redaction_preserves_json_and_cannot_leak_keys(monkeypatch):
    value = report()
    value["issues"][0]["detail"] = 'Source includes token="hidden-fixture-value".'
    engine, _, prompts = await install_engine_fixture(monkeypatch, [
        {"paths": ["app/api.py"], "reason": "entry"},
        {"paths": ["app/search.py"], "reason": "dependency"}, value,
    ])
    result = await engine.analyze_project({"kind": "github"}, personal_context={"role": "developer", "api_key": "private-context-value"})
    assert "private-context-value" not in json.dumps(prompts)
    assert "hidden-fixture-value" not in json.dumps(result)
    assert result["snapshot"]["personal_context"]["role"] == "developer"


def test_citations_must_reference_read_files_and_issue_ids_are_unique():
    engine = module("app.services.project_deep_analysis")
    files = [{"path": k, "content": v} for k, v in RepositoryFixture.files.items()]
    value = report()
    value["architecture"][0]["citations"][0]["path"] = "unread.py"
    with pytest.raises(ValueError, match="引用"):
        engine.validate_report(value, files)
    value = report()
    value["issues"].append(dict(value["issues"][0]))
    with pytest.raises(ValueError, match="编号重复"):
        engine.validate_report(value, files)


@pytest.mark.asyncio
async def test_download_is_bounded_and_uses_pinned_ref(monkeypatch):
    sources = module("app.services.project_sources")
    captured = []
    original_client = httpx.AsyncClient
    async def handler(request):
        captured.append(request)
        return httpx.Response(200, content=b"x" * 100)
    monkeypatch.setattr(sources.httpx, "AsyncClient", lambda **kwargs: original_client(**{**kwargs, "transport": httpx.MockTransport(handler)}))
    monkeypatch.setattr(sources, "MAX_DOWNLOAD_BYTES", 20)
    repo = sources.SourceRepository("github", "example", "fixture", "main", REVISION,
        [{"path": "app/api.py", "size": 100}], False, "fixture-token")
    assert await repo.read_file("app/api.py") is None
    assert captured[0].url.params["ref"] == REVISION
    assert "fixture-token" not in repr(repo)


@pytest.mark.asyncio
async def test_gitcode_page_failure_is_explicit_partial_coverage(monkeypatch):
    sources = module("app.services.project_sources")
    async def page(owner, repo, revision, token, number):
        assert revision == REVISION
        if number == 1:
            return [{"path": "app/api.py", "type": "blob"}] + [{"path": f"dir/{i}", "type": "tree"} for i in range(99)]
        if number == 2:
            raise sources.gitcode_repo.GitCodeRepoError(429, "rate limited")
        return []
    monkeypatch.setattr(sources.gitcode_repo, "_tree_page", page)
    files, truncated = await sources._gitcode_tree("example", "fixture", REVISION, "")
    assert truncated is True
    assert [f["path"] for f in files] == ["app/api.py"]


@pytest.mark.asyncio
async def test_no_config_fails_before_repository_access(monkeypatch):
    engine = module("app.services.project_deep_analysis")
    async def empty_config(): return {}
    async def forbidden(info): pytest.fail("No repository reads when model configuration is unavailable")
    monkeypatch.setattr(engine, "get_llm_config", empty_config)
    monkeypatch.setattr(engine, "open_repository", forbidden)
    with pytest.raises(ValueError, match="LLM"):
        await engine.analyze_project({"kind": "github"})


@pytest.mark.asyncio
async def test_raw_json_source_is_not_mistaken_for_a_contents_api_envelope(monkeypatch):
    sources = module("app.services.project_sources")
    original_client = httpx.AsyncClient
    source = '{"encoding":"base64","content":"aGVsbG8="}'
    async def handler(request): return httpx.Response(200, text=source, headers={"content-type": "application/json"})
    monkeypatch.setattr(sources.httpx, "AsyncClient", lambda **kwargs: original_client(**{**kwargs, "transport": httpx.MockTransport(handler)}))
    repo = sources.SourceRepository("github", "example", "fixture", "main", REVISION,
        [{"path": "app/schema.json", "size": len(source)}], False, "")
    assert await repo.read_file("app/schema.json") == source


@pytest.mark.asyncio
async def test_progress_callback_tracks_each_real_stage(monkeypatch):
    engine, _, _ = await install_engine_fixture(monkeypatch, [
        {"paths": ["app/api.py"], "reason": "entry"},
        {"paths": ["app/search.py"], "reason": "dependency"}, report(),
    ])
    phases = []
    async def progress(stage): phases.append(stage)
    await engine.analyze_project({"kind": "github"}, progress=progress)
    assert phases == ["fetch_repo", "select_files", "read_initial", "followup_files",
                      "read_followup", "final_report", "validate_report"]


@pytest.mark.asyncio
async def test_validation_failure_is_safe_and_identifies_last_phase(monkeypatch):
    bad = report()
    bad["private-model-response-secret"] = "secret-input"
    engine, _, _ = await install_engine_fixture(monkeypatch, [
        {"paths": ["app/api.py"], "reason": "entry"},
        {"paths": ["app/search.py"], "reason": "dependency"}, bad,
    ])
    phases = []
    async def progress(stage): phases.append(stage)
    with pytest.raises(ValueError) as exc:
        await engine.analyze_project({"kind": "github"}, progress=progress)
    assert type(exc.value).__name__ == "ProjectAnalysisError"
    assert "secret" not in str(exc.value)
    assert phases[-1] == "validate_report"


@pytest.mark.asyncio
async def test_source_errors_are_safely_wrapped(monkeypatch):
    engine, _, _ = await install_engine_fixture(monkeypatch, [])
    async def failing_source(info): raise ValueError("private-server-response-secret")
    monkeypatch.setattr(engine, "open_repository", failing_source)
    with pytest.raises(ValueError) as exc:
        await engine.analyze_project({"kind": "github"})
    assert type(exc.value).__name__ == "ProjectAnalysisError"
    assert "private-server-response-secret" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,status,expected', [('timeout', None, '超时'), ('http', 429, 'HTTP 429'),
    ('network', None, '网络'), ('invalid_response', None, '响应格式')])
async def test_adapter_failure_diagnostics_are_safe_and_reset(monkeypatch, kind, status, expected):
    from app.services.llm import AgentAdapter
    original_client = httpx.AsyncClient
    calls = []
    async def handler(request):
        calls.append(request)
        if len(calls) > 1:
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
        if kind == 'timeout': raise httpx.ReadTimeout('private-request-secret', request=request)
        if kind == 'network': raise httpx.ConnectError('private-network-secret', request=request)
        if kind == 'http': return httpx.Response(status, text='private-provider-body-secret')
        return httpx.Response(200, json={'choices': [], 'private-secret': 'do-not-persist'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(**{**kwargs, "transport": httpx.MockTransport(handler)}))
    adapter = AgentAdapter()
    cfg = {'provider': 'fixture', 'api_key': 'private-config-secret', 'base_url': 'https://invalid.example', 'model': 'fixture'}
    assert await adapter.chat_with(cfg, 'system', 'user') is None
    assert adapter.last_error == {'kind': kind, **({'status': status} if status else {})}
    assert 'private' not in json.dumps(adapter.last_error)
    assert await adapter.chat_with(cfg, 'system', 'user') == {'content': 'ok'}
    assert adapter.last_error is None


@pytest.mark.asyncio
async def test_final_report_has_separate_timeout(monkeypatch):
    engine, _, _ = await install_engine_fixture(monkeypatch, [
        {'paths': ['app/api.py'], 'reason': 'entry'},
        {'paths': ['app/search.py'], 'reason': 'dependency'}, report(),
    ])
    previous = engine.AgentAdapter.chat_with
    timeouts = []
    async def chat(self, *args, **kwargs):
        timeouts.append(kwargs['timeout'])
        return await previous(self, *args, **kwargs)
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', chat)
    await engine.analyze_project({'kind': 'github'})
    assert timeouts == [180.0, 180.0, 300.0]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,status,expected', [('timeout', None, '超时'), ('http', 429, 'HTTP 429'),
    ('network', None, '网络'), ('invalid_response', None, '响应格式')])
async def test_engine_none_failure_includes_safe_reason(monkeypatch, kind, status, expected):
    engine, _, _ = await install_engine_fixture(monkeypatch, [None])
    async def fail(self, *args, **kwargs):
        self.last_error = {'kind': kind, **({'status': status} if status else {})}
        return None
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', fail)
    with pytest.raises(engine.ProjectAnalysisError, match=expected):
        await engine.analyze_project({'kind': 'github'})


def large_files():
    return [{'path': path, 'content': '# inspected\n' + 'x = 1\n' * 2200, 'truncated': False}
            for path in ['app/api.py', 'app/search.py', 'app/models.py', 'app/queue.py', 'tests/api.py', 'tests/search.py']]


def report_for_paths(paths):
    result = report()
    result['architecture'][0]['citations'][0]['path'] = paths[0]
    result['issues'][0]['citations'][0]['path'] = paths[-1]
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['zh-CN', 'en'])
async def test_large_source_uses_bounded_parallel_reviews_and_small_synthesis(monkeypatch, language):
    engine = module('app.services.project_deep_analysis')
    review = getattr(engine, 'review_inspected_sources', None)
    assert callable(review), 'Large source must be reviewed in bounded groups before synthesis'
    prompts, adapters, active, peak = [], [], 0, 0
    async def chat(self, cfg, system, user, **kwargs):
        nonlocal active, peak
        payload = json.loads(user)
        assert message('language', language) in system
        if language == 'en':
            assert not re.search(r'[\u3400-\u9fff]', system)
        prompts.append(payload)
        adapters.append(self)
        if 'module_index' in payload:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.005)
            active -= 1
            result = report_for_paths([f['path'] for f in payload['inspected_files']])
        else:
            result = report()
        return {'content': json.dumps(result), 'usage': {'total_tokens': 10}}
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', chat)
    phases = []
    async def progress(stage): phases.append(stage)
    files = large_files()
    context = {'project_name': 'fixture', 'source_revision': REVISION, 'focus': '',
               'manifest': [{'path': f'never-copy-full-manifest/{i}'} for i in range(4000)]}
    with using_language(language):
        result = await review(files, context, {'files_read': len(files), 'eligible_files': 4000},
                              cfg={'provider': 'fixture', 'model': 'fixture'}, progress=progress)
    modules = [p for p in prompts if 'module_index' in p]
    assert len(modules) == 3
    assert sorted(f['path'] for p in modules for f in p['inspected_files']) == sorted(f['path'] for f in files)
    assert peak == 2 and len({id(a) for a in adapters}) == len(adapters)
    final = prompts[-1]
    assert 'module_reviews' in final and 'citation_excerpts' in final
    assert 'inspected_files' not in final and 'manifest' not in final
    assert 'never-copy-full-manifest' not in json.dumps(final)
    assert len(json.dumps(final, ensure_ascii=False)) <= 60000
    assert all(len(c['excerpt']) <= 1500 and c['excerpt_end_line'] - c['start_line'] < 15 for c in final['citation_excerpts'])
    assert len(result['module_reviews']) == 3 and result['report']['issues'][0]['id'] == 'I1'
    assert result['result_meta']['calls'] == 4
    assert phases == ['review_modules', 'final_report', 'validate_report']


@pytest.mark.asyncio
async def test_small_source_with_large_manifest_uses_bounded_synthesis(monkeypatch):
    engine = module('app.services.project_deep_analysis')
    files = [{'path': path, 'content': content, 'truncated': False}
             for path, content in RepositoryFixture.files.items() if path.endswith('.py')]
    context = {'project_name': 'fixture', 'source_revision': REVISION,
               'manifest': [{'path': f'never-copy-full-manifest/{i}'} for i in range(4000)]}
    coverage = {'files_read': len(files), 'eligible_files': 4000}
    candidate = {**context, 'inspected_files': engine._numbered(files), 'coverage': coverage,
                 'json_schema': engine.Report.model_json_schema()}
    assert sum(len(f['content']) for f in files) < engine.CHUNK_REVIEW_THRESHOLD
    assert len(json.dumps(candidate, ensure_ascii=False)) > engine.MAX_SYNTHESIS_CHARS
    assert len(json.dumps({k: v for k, v in candidate.items() if k != 'manifest'}, ensure_ascii=False)) < engine.MAX_SYNTHESIS_CHARS
    prompts = []
    async def chat(self, cfg, system, user, **kwargs):
        payload = json.loads(user)
        prompts.append(payload)
        paths = [f['path'] for f in payload['inspected_files']] if 'module_index' in payload else [f['path'] for f in files]
        return {'content': json.dumps(report_for_paths(paths))}
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', chat)
    phases = []
    async def progress(stage): phases.append(stage)
    result = await engine.review_inspected_sources(files, context, coverage,
        cfg={'provider': 'fixture', 'model': 'fixture'}, progress=progress)
    modules = [p for p in prompts if 'module_index' in p]
    assert len(modules) == 1
    assert [f['path'] for f in modules[0]['inspected_files']] == [f['path'] for f in files]
    final = prompts[-1]
    assert 'module_reviews' in final and 'citation_excerpts' in final
    assert 'manifest' not in final and 'inspected_files' not in final
    assert 'never-copy-full-manifest' not in json.dumps(final)
    assert len(json.dumps(final, ensure_ascii=False)) <= engine.MAX_SYNTHESIS_CHARS
    assert len(result['module_reviews']) == 1 and result['result_meta']['calls'] == 2
    assert phases == ['review_modules', 'final_report', 'validate_report']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['model', 'citation'])
async def test_module_review_failure_aborts_instead_of_synthesizing_success(monkeypatch, failure):
    engine = module('app.services.project_deep_analysis')
    review = getattr(engine, 'review_inspected_sources', None)
    assert callable(review)
    final_called = False
    async def chat(self, cfg, system, user, **kwargs):
        nonlocal final_called
        payload = json.loads(user)
        if 'module_index' not in payload:
            final_called = True
            return {'content': json.dumps(report())}
        if failure == 'model':
            self.last_error = {'kind': 'timeout'}
            return None
        result = report_for_paths([f['path'] for f in payload['inspected_files']])
        result['issues'][0]['citations'][0]['end_line'] = 99999
        return {'content': json.dumps(result)}
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', chat)
    with pytest.raises(engine.ProjectAnalysisError):
        await review(large_files(), {'source_revision': REVISION}, {'files_read': 6}, cfg={'provider': 'fixture', 'model': 'fixture'})
    assert not final_called


@pytest.mark.asyncio
async def test_each_model_call_has_an_outer_total_deadline(monkeypatch):
    engine, _, _ = await install_engine_fixture(monkeypatch, [None])
    deadlines = []
    async def expired(awaitable, timeout):
        deadlines.append(timeout)
        awaitable.close()
        raise TimeoutError()
    monkeypatch.setattr(engine.asyncio, 'wait_for', expired, raising=False)
    with pytest.raises(engine.ProjectAnalysisError, match='超时'):
        await engine.analyze_project({'kind': 'github'})
    assert deadlines == [180.0]


@pytest.mark.asyncio
async def test_valid_verbose_modules_are_preserved_and_synthesis_stays_bounded(monkeypatch):
    from copy import deepcopy
    engine = module('app.services.project_deep_analysis')
    final_payload = None
    async def chat(self, cfg, system, user, **kwargs):
        nonlocal final_payload
        payload = json.loads(user)
        if 'module_index' in payload:
            paths = [f['path'] for f in payload['inspected_files']]
            value = report_for_paths(paths)
            value['architecture'] = [deepcopy(value['architecture'][0]) for _ in range(3)]
            value['issues'] = [deepcopy(value['issues'][0]) for _ in range(3)]
            for index, entry in enumerate(value['architecture'] + value['issues']):
                entry['detail'] = 'Valid detailed observation. ' * 50
                entry['citations'] = [{'path': paths[0], 'start_line': n, 'end_line': n + 1} for n in (1, 3, 5)]
                if 'id' in entry:
                    entry['id'] = f'I{index + 1}'
            assert len(json.dumps(value)) > 6000
        else:
            final_payload = payload
            value = report()
            value['architecture'] = [deepcopy(value['architecture'][0]) for _ in range(7)]
            value['issues'] = [deepcopy(value['issues'][0]) for _ in range(7)]
            for index, entry in enumerate(value['issues'], 1): entry['id'] = f'I{index}'
        return {'content': json.dumps(value)}
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', chat)
    result = await engine.review_inspected_sources(large_files(), {'source_revision': REVISION},
        {'files_read': 6}, cfg={'provider': 'fixture', 'model': 'fixture'})
    assert len(result['module_reviews']) == 3
    for review in result['module_reviews']:
        assert len(review['report']['architecture']) == 3
        assert len(review['report']['issues']) == 3
        assert len(review['report']['issues'][0]['citations']) == 3
        assert review['report']['issues'][0]['detail'] == ('Valid detailed observation. ' * 50).strip()
    assert len(result['report']['issues']) == 7
    assert len(json.dumps(final_payload, ensure_ascii=False)) <= 60000
    for compact, full in zip(final_payload['module_reviews'], result['module_reviews']):
        assert len(compact['report']['issues']) == len(full['report']['issues'])
        assert compact['report']['issues'][0]['citations'] == full['report']['issues'][0]['citations']


def test_extreme_synthesis_budget_fails_explicitly_without_discarding_citations():
    from copy import deepcopy
    engine = module('app.services.project_deep_analysis')
    files = large_files()
    value = report()
    value['architecture'] = [deepcopy(value['architecture'][0]) for _ in range(12)]
    value['issues'] = [deepcopy(value['issues'][0]) for _ in range(12)]
    for index, entry in enumerate(value['architecture'] + value['issues']):
        entry['citations'] = [{'path': files[0]['path'], 'start_line': n, 'end_line': n + 1} for n in range(1, 11)]
        if 'id' in entry: entry['id'] = f'I{index + 1}'
    engine.validate_report(value, files)
    modules = [{'module_index': i, 'paths': [f['path'] for f in files], 'report': deepcopy(value)} for i in range(1, 13)]
    before = deepcopy(modules)
    with pytest.raises(engine.ProjectAnalysisError, match='final_report'):
        engine._synthesis_payload(files, {'source_revision': REVISION}, {}, modules, engine.Report.model_json_schema())
    assert modules == before


@pytest.mark.asyncio
async def test_adapter_effort_is_explicit_and_does_not_change_default_payload(monkeypatch):
    from app.services.llm import AgentAdapter
    original_client = httpx.AsyncClient
    payloads = []
    async def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original_client(**{**kwargs, "transport": httpx.MockTransport(handler)}))
    adapter = AgentAdapter()
    cfg = {'provider': 'fixture', 'api_key': 'fixture-key', 'base_url': 'https://invalid.example', 'model': 'deepseek-v4-flash'}
    before = dict(cfg)
    await adapter.chat_with(cfg, 'system', 'user')
    assert payloads[0] == {'model': 'deepseek-v4-flash', 'messages': [{'role': 'system', 'content': 'system'},
        {'role': 'user', 'content': 'user'}], 'temperature': 0.2, 'max_tokens': 4096}
    await adapter.chat_with(cfg, 'system', 'user', reasoning_effort='low')
    assert payloads[1] == {**payloads[0], 'reasoning_effort': 'low'}
    assert cfg == before


@pytest.mark.asyncio
@pytest.mark.parametrize('model,expected', [('deepseek-v4-flash', 'low'), ('deepseek-flash', 'low'),
    ('deepseek-v4-pro', 'low'), ('fixture-model', None), ('gateway/deepseek-v4-flash', None)])
async def test_only_exact_supported_analysis_models_request_low_effort(monkeypatch, model, expected):
    engine, _, _ = await install_engine_fixture(monkeypatch, [
        {'paths': ['app/api.py'], 'reason': 'entry'},
        {'paths': ['app/search.py'], 'reason': 'dependency'}, report(),
    ])
    cfg = {'provider': 'fixture', 'api_key': 'fixture-key', 'base_url': 'https://invalid.example', 'model': model}
    async def config(): return cfg
    monkeypatch.setattr(engine, 'get_llm_config', config)
    previous = engine.AgentAdapter.chat_with
    options = []
    async def chat(self, *args, **kwargs):
        options.append(kwargs)
        return await previous(self, *args, **kwargs)
    monkeypatch.setattr(engine.AgentAdapter, 'chat_with', chat)
    result = await engine.analyze_project({'kind': 'github'})
    if expected:
        assert all(item.get('reasoning_effort') == expected for item in options)
        assert result['result_meta']['requested_reasoning_effort'] == expected
    else:
        assert all('reasoning_effort' not in item for item in options)
        assert 'requested_reasoning_effort' not in result['result_meta']
    assert 'reasoning_effort' not in cfg

pytestmark = pytest.mark.usefixtures("mock_private_billing")
