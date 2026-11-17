"""Read-only source adapter contracts; HTTP is always a controlled fixture."""
import importlib
import importlib.util
import json
import time

import httpx
import pytest


SHA = 'a' * 40
TREE = 'b' * 40
PROJECT = {'kind': 'github', 'owner': 'fixture', 'repo': 'sample', 'branch': 'main'}


def sources():
    name = 'app.services.project_autopilot_sources'
    assert importlib.util.find_spec(name), 'Autopilot needs complete pinned source stages'
    return importlib.import_module(name)


def install_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.asyncio
async def test_probe_and_tree_walk_pin_version_and_account_for_all_paths(monkeypatch):
    api = sources()
    requests = []
    async def handler(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer explicit-fixture'
        path = request.url.path
        if path.endswith('/repos/fixture/sample'):
            return httpx.Response(200, json={'id': 9, 'default_branch': 'main'})
        if '/commits/' in path:
            return httpx.Response(200, json={'sha': SHA, 'commit': {'tree': {'sha': TREE}}, 'parents': []})
        if path.endswith(TREE):
            return httpx.Response(200, json={'tree': [{'path': 'src', 'type': 'tree', 'sha': 'c' * 40},
                {'path': '.env', 'type': 'blob', 'sha': 'd' * 40, 'size': 30},
                {'path': 'package-lock.json', 'type': 'blob', 'sha': 'e' * 40, 'size': 30}], 'truncated': False})
        if path.endswith('c' * 40):
            return httpx.Response(200, json={'tree': [{'path': f'f{i}.py', 'type': 'blob', 'sha': f'{i:040x}', 'size': 5}
                for i in range(350)], 'truncated': False})
        raise AssertionError(path)
    install_http(monkeypatch, handler)
    snapshot = await api.probe_repository(PROJECT, {'github_token': 'explicit-fixture'})
    assert snapshot['target_sha'] == SHA and snapshot['tree_sha'] == TREE
    first = await api.fetch_manifest_page(PROJECT, snapshot, None, {'github_token': 'explicit-fixture'})
    assert not first['complete'] and first['next_cursor']
    second = await api.fetch_manifest_page(PROJECT, snapshot, first['next_cursor'], {'github_token': 'explicit-fixture'})
    assert second['complete'] and second['next_cursor'] is None
    entries = first['entries'] + second['entries']
    assert len([e for e in entries if e['type'] == 'blob']) == 352
    assert next(e for e in entries if e['path'] == '.env')['included'] is False
    assert next(e for e in entries if e['path'] == 'package-lock.json')['category'] == 'dependency_lock'
    assert next(e for e in entries if e['path'] == 'src/f349.py')['blob_oid'] == f'{349:040x}'
    assert all('recursive' not in r.url.params for r in requests)


@pytest.mark.asyncio
async def test_source_reads_redact_before_storage_and_keep_original_lines(monkeypatch):
    api = sources()
    async def handler(request):
        assert request.url.params['ref'] == SHA
        return httpx.Response(200, text='api_key = "sk-' + 'x' * 24 + '"\nprint("ok")\n')
    install_http(monkeypatch, handler)
    value = await api.read_source(PROJECT, {'target_sha': SHA},
        {'path': 'app.py', 'blob_oid': 'f' * 40, 'included': True}, {})
    assert 'sk-' not in value['content'] and '[REDACTED]' in value['content']
    assert value['line_count'] == 2 and value['source_revision'] == SHA
    assert value['download_bytes'] > len(value['content'].encode())
    assert value['source_bytes'] == value['download_bytes']
    with pytest.raises(api.SourceError):
        await api.read_source(PROJECT, {'target_sha': SHA}, {'path': '.env', 'included': True}, {})


@pytest.mark.asyncio
@pytest.mark.parametrize('status,category', [(401, 'AUTH_BLOCKED'), (429, 'RATE_LIMITED'), (503, 'PROVIDER_5XX')])
async def test_source_failure_is_safe_and_retry_metadata_is_retained(monkeypatch, status, category):
    api = sources()
    async def handler(request):
        return httpx.Response(status, text='private key must not escape', headers={'Retry-After': '12', 'X-Request-Id': 'req-123'})
    install_http(monkeypatch, handler)
    with pytest.raises(api.SourceError) as exc:
        await api.probe_repository(PROJECT, {})
    assert exc.value.category == category
    assert exc.value.retry_after == 12 and exc.value.request_id == 'req-123'
    assert 'private' not in str(exc.value)


def test_complete_manifest_diff_includes_deletion_rename_and_unknown_hash():
    api = sources()
    base = {'complete': True, 'entries': [{'path': f'f{i}.py', 'blob_oid': f'{i:040x}', 'type': 'blob', 'mode': '100644'} for i in range(350)]}
    target = json.loads(json.dumps(base))
    target['entries'][349]['path'] = 'renamed.py'
    target['entries'].pop(348)
    diff = api.diff_manifests(base, target)
    assert diff['complete'] and not diff['requires_full']
    assert any(c['status'] == 'renamed' and c['previous_path'] == 'f349.py' for c in diff['changes'])
    assert any(c['status'] == 'deleted' and c['path'] == 'f348.py' for c in diff['changes'])
    target['entries'][0]['blob_oid'] = None
    assert api.diff_manifests(base, target)['requires_full']
    assert api.diff_manifests({**base, 'complete': False}, target)['requires_full']


@pytest.mark.asyncio
async def test_gitcode_pagination_continues_beyond_old_limit(monkeypatch):
    api = sources()
    async def handler(request):
        page = int(request.url.params['page'])
        count = 100 if page <= 31 else 1
        return httpx.Response(200, json={'tree': [{'path': f'f{page}-{i}.py', 'type': 'blob', 'sha': f'{page * 100 + i:040x}'} for i in range(count)]})
    install_http(monkeypatch, handler)
    project = {**PROJECT, 'kind': 'gitcode'}
    cursor, entries = None, []
    for _ in range(32):
        result = await api.fetch_manifest_page(project, {'target_sha': SHA}, cursor, {})
        entries.extend(result['entries'])
        cursor = result['next_cursor']
    assert result['complete'] and cursor is None and len(entries) == 3101
    assert result['provider_verified'] is False


@pytest.mark.asyncio
async def test_github_rate_reset_is_respected_without_retry_after(monkeypatch):
    api = sources()
    async def handler(request):
        return httpx.Response(403, headers={'x-ratelimit-remaining': '0', 'x-ratelimit-reset': str(int(time.time()) + 60)})
    install_http(monkeypatch, handler)
    with pytest.raises(api.SourceError) as exc:
        await api.probe_repository(PROJECT, {})
    assert exc.value.category == 'RATE_LIMITED' and 55 <= exc.value.retry_after <= 60


@pytest.mark.asyncio
async def test_contents_envelope_with_wrong_path_is_not_analyzed_as_source(monkeypatch):
    api = sources()
    async def handler(request):
        return httpx.Response(200, json={'type': 'file', 'path': 'other.py', 'sha': 'f' * 40, 'encoding': 'base64', 'content': 'eD0xCg=='})
    install_http(monkeypatch, handler)
    with pytest.raises(api.SourceError):
        await api.read_source(PROJECT, {'target_sha': SHA}, {'path': 'app.py', 'blob_oid': 'f' * 40, 'included': True}, {})


@pytest.mark.asyncio
async def test_sparse_utf8_nul_has_reversible_visible_escape_and_original_line_numbers(monkeypatch):
    api = sources()
    original = '// ordinary source context\n' * 100 + 'const existing = "\\u0000"; const actual = "\x00";\n'
    async def handler(request): return httpx.Response(200, content=original.encode(), headers={'content-type': 'text/plain'})
    install_http(monkeypatch, handler)
    result = await api.read_source(PROJECT, {'target_sha': SHA}, {'path': 'index.js', 'blob_oid': 'f' * 40, 'included': True}, {})
    assert result['encoding'] == 'utf-8' and '\x00' not in result['content']
    assert result['line_count'] == len(original.splitlines())
    assert result['source_bytes'] == result['download_bytes'] == len(original.encode())
    assert len(result['normalizations']) == 1
    correction = result['normalizations'][0]
    assert correction['line'] == 101 and correction['codepoint'] == 'U+0000'
    lines = result['content'].splitlines(keepends=True)
    column = correction['display_column'] - 1
    line = lines[correction['line'] - 1]
    assert line[column:column + len(correction['replacement'])] == correction['replacement']
    lines[correction['line'] - 1] = line[:column] + '\x00' + line[column + len(correction['replacement']):]
    assert ''.join(lines) == original
    assert result['redacted_sha256_before_normalization'] != result['content_sha256']


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [b'\xffinvalid', b'A\x00B\x00' * 500, b'// text\n' * 100 + b'\x01'])
async def test_non_utf8_or_binary_control_density_remains_blocked(monkeypatch, raw):
    api = sources()
    async def handler(request): return httpx.Response(200, content=raw)
    install_http(monkeypatch, handler)
    with pytest.raises(api.SourceError) as exc:
        await api.read_source(PROJECT, {'target_sha': SHA}, {'path': 'index.js', 'blob_oid': 'f' * 40, 'included': True}, {})
    assert exc.value.category == 'SOURCE_UNAVAILABLE'
