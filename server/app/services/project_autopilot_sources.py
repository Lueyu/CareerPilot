"""Pinned, checkpointable read-only repository access. No global credentials or execution."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from app.services.project_sources import is_safe_source_path, redact_source


class SourceError(ValueError):
    safe_for_user = True

    def __init__(self, category, message, retry_after=None, request_id=None):
        super().__init__(message)
        self.category, self.retry_after, self.request_id = category, retry_after, request_id


def safe_request_id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) and redact_source(value) == value else None


def retry_seconds(value):
    if not value:
        return None
    try:
        return max(0, min(86400, int(value)))
    except (ValueError, TypeError):
        try:
            return max(0, min(86400, int((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())))
        except (ValueError, TypeError, OverflowError):
            return None


def _identity(project):
    kind = project.get('kind') or 'github'
    owner, repo = str(project.get('owner') or ''), str(project.get('repo') or '')
    if kind not in ('github', 'gitcode') or not owner or not repo or any(c in owner + repo for c in '/\\?#\r\n'):
        raise SourceError('SOURCE_UNAVAILABLE', '仓库地址或提供方无效')
    return kind, quote(owner, safe=''), quote(repo, safe='')


def _headers(kind, credentials, raw=False):
    headers = {'User-Agent': 'CareerPilot-Local', 'Accept': 'application/vnd.github.raw+json' if raw else 'application/json'}
    token = (credentials or {}).get(f'{kind}_token')
    if token:
        headers['Authorization' if kind == 'github' else 'PRIVATE-TOKEN'] = f'Bearer {token}' if kind == 'github' else token
    if kind == 'github':
        headers['X-GitHub-Api-Version'] = '2022-11-28'
    return headers


async def _get(kind, path, credentials, *, params=None, raw=False, max_bytes=16_000_000):
    origin = 'https://api.github.com' if kind == 'github' else 'https://gitcode.com/api/v5'
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False, follow_redirects=False) as client:
            async with client.stream('GET', origin + path, headers=_headers(kind, credentials, raw), params=params) as response:
                if response.status_code != 200:
                    status = response.status_code
                    category = ('AUTH_BLOCKED' if status in (401, 403) else 'RATE_LIMITED' if status == 429 else
                                'PROVIDER_5XX' if status >= 500 else 'SOURCE_UNAVAILABLE')
                    if status == 403 and (response.headers.get('x-ratelimit-remaining') == '0' or response.headers.get('retry-after')):
                        category = 'RATE_LIMITED'
                    retry_after = retry_seconds(response.headers.get('retry-after'))
                    if category == 'RATE_LIMITED' and retry_after is None:
                        try:
                            retry_after = max(0, min(86400, int(response.headers['x-ratelimit-reset']) - int(time.time())))
                        except (KeyError, ValueError, TypeError):
                            pass
                    raise SourceError(category, f'仓库服务返回 HTTP {status}，进度已保存',
                        retry_after,
                        safe_request_id(response.headers.get('x-request-id') or response.headers.get('x-github-request-id')))
                chunks, size = [], 0
                async for chunk in response.aiter_bytes(chunk_size=min(65536, max_bytes + 1)):
                    size += len(chunk)
                    if size > max_bytes:
                        raise SourceError('BUDGET_EXHAUSTED', '单份仓库资料超过读取预算，请调整预算后恢复')
                    chunks.append(chunk)
                return b''.join(chunks), response.headers
    except httpx.TimeoutException:
        raise SourceError('REQUEST_TIMEOUT', '仓库读取超时，进度已保存') from None
    except httpx.HTTPError:
        raise SourceError('NETWORK_TRANSIENT', '仓库网络暂时不可用，进度已保存') from None


async def _json(kind, path, credentials, **kwargs):
    raw, headers = await _get(kind, path, credentials, **kwargs)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        return value, headers
    except (ValueError, UnicodeError):
        raise SourceError('SOURCE_UNAVAILABLE', '仓库服务未返回有效元数据') from None


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', value):
        raise SourceError('SOURCE_UNAVAILABLE', '仓库未提供可固定的真实提交版本')
    return value.lower()


async def probe_repository(project, credentials):
    kind, owner, repo = _identity(project)
    meta, _ = await _json(kind, f'/repos/{owner}/{repo}', credentials)
    branch = project.get('branch') or meta.get('default_branch')
    if not branch:
        raise SourceError('SOURCE_UNAVAILABLE', '无法确定仓库默认分支')
    ref = project.get('target_sha') or branch
    commit, _ = await _json(kind, f'/repos/{owner}/{repo}/commits/{quote(str(ref), safe="")}', credentials)
    revision = _sha(commit.get('sha'))
    if project.get('target_sha') and revision != _sha(project['target_sha']):
        raise SourceError('SOURCE_UNAVAILABLE', '仓库返回的提交与任务固定版本不一致')
    tree = (commit.get('commit') or {}).get('tree') or commit.get('tree') or {}
    return {'target_sha': revision, 'branch': str(branch), 'repository_id': str(meta.get('id') or f'{kind}:{owner}/{repo}'),
            'provider': kind, 'owner': project['owner'], 'repo': project['repo'],
            'tree_sha': tree.get('sha') or revision,
            'parents': [p['sha'] for p in commit.get('parents', []) if isinstance(p, dict) and isinstance(p.get('sha'), str)],
            'provider_verified': kind == 'github'}


_LOCKS = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'uv.lock', 'cargo.lock', 'composer.lock', 'go.sum'}


def classify_path(path, kind='blob', mode=None):
    if not isinstance(path, str) or not path or len(path) > 512 or path.startswith('/') or any(p in ('', '.', '..') for p in path.split('/')) or any(c in path for c in '\\:\r\n'):
        return False, 'unsafe_path', '路径不合法'
    name = path.rsplit('/', 1)[-1].lower()
    if kind != 'blob' or mode == '120000':
        return False, 'submodule' if kind == 'commit' else 'directory' if kind == 'tree' else 'symlink', '仅处理普通文本文件'
    if name in _LOCKS:
        # Reuse the established directory/privacy policy without blanket lock exclusion.
        fake = path.rsplit('/', 1)[0] + '/dependencies.json' if '/' in path else 'dependencies.json'
        return (True, 'dependency_lock', '依赖版本资料') if is_safe_source_path(fake) else (False, 'excluded', '敏感或生成目录')
    if name in ('requirements.txt', 'requirements-dev.txt', 'go.mod'):
        fake = path.rsplit('/', 1)[0] + '/dependencies.json' if '/' in path else 'dependencies.json'
        if is_safe_source_path(fake):
            return True, 'dependency_config', '依赖配置'
    if not is_safe_source_path(path):
        return False, 'excluded', '敏感、生成、二进制或非支持文本路径'
    category = ('documentation' if name.endswith(('.md', '.rst')) else 'test' if '/test' in '/' + path.lower() else
                'configuration' if name.endswith(('.json', '.toml', '.yaml', '.yml', '.ini', '.cfg')) else 'source')
    return True, category, '项目文本资料'


def _entry(item, prefix=''):
    path = item.get('path') or item.get('name')
    if not isinstance(path, str):
        raise SourceError('MANIFEST_INCOMPLETE', '仓库目录包含无效路径，不能认定清单完整')
    path = prefix + path
    kind, mode = item.get('type'), item.get('mode')
    included, category, reason = classify_path(path, kind, mode)
    oid = item.get('sha') or item.get('id')
    oid = oid if isinstance(oid, str) and re.fullmatch(r'[a-fA-F0-9]{40}|[a-fA-F0-9]{64}', oid) else None
    return {'path': path, 'blob_oid': oid, 'type': kind, 'mode': mode, 'size': item.get('size'),
            'included': included, 'category': category, 'reason': reason}


async def fetch_manifest_page(project, snapshot, cursor, credentials):
    kind, owner, repo = _identity(project)
    revision = _sha(snapshot['target_sha'])
    if kind == 'github':
        pending = list((cursor or {}).get('pending') or [{'prefix': '', 'sha': snapshot.get('tree_sha') or revision}])
        node = pending.pop(0)
        data, _ = await _json(kind, f'/repos/{owner}/{repo}/git/trees/{quote(node["sha"], safe="")}', credentials)
        if data.get('truncated') or not isinstance(data.get('tree'), list):
            raise SourceError('MANIFEST_INCOMPLETE', '单个目录仍被服务端截断，已保存的清单不能标记完整')
        entries = [_entry(item, node['prefix']) for item in data['tree']]
        for e in entries:
            if e['type'] == 'tree':
                if not e['blob_oid'] or e['category'] == 'unsafe_path':
                    raise SourceError('MANIFEST_INCOMPLETE', '子目录缺少有效版本，无法继续完整遍历')
                pending.append({'prefix': e['path'].rstrip('/') + '/', 'sha': e['blob_oid']})
        continuation = {'pending': pending} if pending else None
    else:
        page = (cursor or {}).get('page', 1)
        data, headers = await _json(kind, f'/repos/{owner}/{repo}/git/trees/{revision}', credentials,
            params={'recursive': '1', 'per_page': '100', 'page': str(page)})
        items = data.get('tree')
        if not isinstance(items, list):
            raise SourceError('MANIFEST_INCOMPLETE', '仓库目录分页响应不完整')
        entries = [_entry(item) for item in items]
        page_hash = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        if page_hash in (cursor or {}).get('seen', []):
            raise SourceError('MANIFEST_INCOMPLETE', '仓库重复返回相同目录页，已停止而未宣称完整')
        if data.get('truncated') and len(items) < 100:
            raise SourceError('MANIFEST_INCOMPLETE', '仓库目录被截断且未提供可继续页')
        continuation = {'page': page + 1, 'seen': [*(cursor or {}).get('seen', []), page_hash]} if len(items) >= 100 else None
    return {'entries': entries, 'next_cursor': continuation, 'complete': continuation is None, 'provider_verified': kind == 'github'}


def _visible_nuls(text):
    """Escape sparse actual NULs with a reversible coordinate map, after redaction."""
    rendered, normalizations = [], []
    for line_number, line in enumerate(text.splitlines(keepends=True), 1):
        parts, display_column = [], 1
        for source_column, char in enumerate(line, 1):
            replacement = '\\u0000' if char == '\x00' else char
            if char == '\x00':
                normalizations.append({'kind': 'visible_control_escape', 'codepoint': 'U+0000',
                    'line': line_number, 'source_column': source_column, 'display_column': display_column,
                    'replacement': replacement})
            parts.append(replacement)
            display_column += len(replacement)
        rendered.append(''.join(parts))
    return ''.join(rendered), normalizations


async def read_source(project, snapshot, entry, credentials, *, max_bytes=2_000_000):
    kind, owner, repo = _identity(project)
    path = entry.get('path')
    allowed, category, _ = classify_path(path, 'blob', entry.get('mode'))
    if not allowed or not entry.get('included'):
        raise SourceError('SOURCE_UNAVAILABLE', '不允许读取敏感或未纳入清单的路径')
    revision = _sha(snapshot['target_sha'])
    raw, headers = await _get(kind, f'/repos/{owner}/{repo}/contents/{quote(path, safe="/")}', credentials,
        params={'ref': revision}, raw=True, max_bytes=max_bytes)
    try:
        content = raw.decode('utf-8')
        if 'json' in headers.get('content-type', ''):
            try:
                envelope = json.loads(content)
            except ValueError:
                envelope = None
            if isinstance(envelope, dict) and envelope.get('type') == 'file' and envelope.get('encoding') == 'base64':
                if envelope.get('path') != path or envelope.get('sha') != entry.get('blob_oid') or not isinstance(envelope.get('content'), str):
                    raise SourceError('SOURCE_UNAVAILABLE', '文件响应的路径或版本与清单不一致')
                try:
                    content = base64.b64decode(''.join(envelope['content'].split()), validate=True).decode('utf-8')
                except (ValueError, UnicodeError):
                    raise SourceError('SOURCE_UNAVAILABLE', '文件编码响应无效，未把服务端封装当成源码') from None
    except UnicodeError:
        raise SourceError('SOURCE_UNAVAILABLE', '该文件不能严格解码为 UTF-8，未使用替换字符或空内容冒充源码') from None
    nul_count = content.count('\x00')
    other_controls = any((ord(c) < 32 and c not in '\t\r\n\x00') or ord(c) == 127 for c in content)
    if other_controls or nul_count > 8 or (nul_count and nul_count / max(1, len(content)) > 0.001):
        raise SourceError('SOURCE_UNAVAILABLE', '文件包含高密度 NUL 或其他二进制控制字符，保持未评估；未转换成伪源码')
    source_bytes = len(content.encode('utf-8'))
    content = redact_source(content)
    redacted_hash = hashlib.sha256(content.encode()).hexdigest()
    content, normalizations = _visible_nuls(content)
    return {'path': path, 'blob_oid': entry.get('blob_oid'), 'source_revision': revision,
            'content': content, 'line_count': len(content.splitlines()), 'category': category,
            'content_sha256': hashlib.sha256(content.encode()).hexdigest(), 'truncated': False,
            'download_bytes': len(raw), 'source_bytes': source_bytes, 'encoding': 'utf-8',
            'normalizations': normalizations, 'redacted_sha256_before_normalization': redacted_hash}


def diff_manifests(base, target):
    invalid = not base.get('complete') or not target.get('complete')
    maps = [{e['path']: e for e in m.get('entries', []) if e.get('type') in ('blob', 'commit')} for m in (base, target)]
    invalid = invalid or any(not e.get('blob_oid') for m in maps for e in m.values())
    if invalid:
        return {'complete': False, 'changes': [], 'requires_full': True, 'reason': '基线、目标清单或文件版本不完整'}
    before, after = maps
    deleted, added = set(before) - set(after), set(after) - set(before)
    changes = []
    for path in sorted(before.keys() & after.keys()):
        if any(before[path].get(k) != after[path].get(k) for k in ('blob_oid', 'mode', 'type')):
            changes.append({'status': 'modified', 'path': path, 'before_blob': before[path]['blob_oid'], 'after_blob': after[path]['blob_oid']})
    for path in sorted(list(deleted)):
        matches = [p for p in added if after[p]['blob_oid'] == before[path]['blob_oid'] and after[p].get('type') == before[path].get('type')]
        reverse = [p for p in deleted if before[p]['blob_oid'] == before[path]['blob_oid']]
        if len(matches) == len(reverse) == 1:
            new = matches[0]
            changes.append({'status': 'renamed', 'path': new, 'previous_path': path, 'before_blob': before[path]['blob_oid'], 'after_blob': after[new]['blob_oid']})
            deleted.remove(path)
            added.remove(new)
    changes.extend({'status': 'deleted', 'path': p, 'before_blob': before[p]['blob_oid']} for p in sorted(deleted))
    changes.extend({'status': 'added', 'path': p, 'after_blob': after[p]['blob_oid']} for p in sorted(added))
    return {'complete': True, 'changes': changes, 'requires_full': False, 'reason': '完整目录和 blob 逐项比较'}


async def check_ancestry(project, base_sha, snapshot, credentials):
    if base_sha == snapshot['target_sha'] or base_sha in snapshot.get('parents', []):
        return True
    kind, owner, repo = _identity(project)
    if kind != 'github':
        return None  # This provider's compare semantics are not yet independently verified.
    data, _ = await _json(kind, f'/repos/{owner}/{repo}/compare/{_sha(base_sha)}...{_sha(snapshot["target_sha"])}', credentials,
        params={'per_page': '1'})
    return True if data.get('status') in ('ahead', 'identical') else False if data.get('status') in ('behind', 'diverged') else None
