"""GitHub REST 只读分析(真实 API, 不用 Mock)。

只做只读调用: repo 元信息 / README / 文件树 / 语言分布 / commit 数 / 单文件。
token 来自设置页或 .env(get_github_token); 无 token 时匿名访问公共仓库(限速 60/h, 仅小仓库可用)。
"""
import base64
import time

import httpx

from app.services.github_publish import get_github_token

_API = "https://api.github.com"


class GitHubRepoError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"GitHub {status_code}: {detail}")


def _headers(token: str | None, raw: bool = False) -> dict:
    h = {
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "CareerPilot-Local",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _get(url: str, token: str | None, *, raw: bool = False, params: dict | None = None) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        r = await client.get(url, headers=_headers(token, raw=raw), params=params)
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text[:200])
        except Exception:  # noqa: BLE001
            msg = r.text[:200]
        raise GitHubRepoError(r.status_code, msg)
    return r


async def fetch_repo_meta(owner: str, repo: str, token: str | None = None) -> dict:
    r = await _get(f"{_API}/repos/{owner}/{repo}", token)
    d = r.json()
    return {
        "full_name": d.get("full_name"),
        "description": d.get("description"),
        "default_branch": d.get("default_branch") or "main",
        "language": d.get("language"),
        "languages_meta": {"stars": d.get("stargazers_count", 0), "size_kb": d.get("size", 0)},
        "pushed_at": d.get("pushed_at"),
        "html_url": d.get("html_url"),
        "private": bool(d.get("private")),
    }


async def fetch_readme(owner: str, repo: str, token: str | None = None, branch: str | None = None) -> str:
    params = {"ref": branch} if branch else None
    r = await _get(f"{_API}/repos/{owner}/{repo}/readme", token, raw=True, params=params)
    return r.text


async def fetch_tree(owner: str, repo: str, token: str | None = None, branch: str | None = None) -> list[dict]:
    """返回 [{path, type(blob|tree), size}]。超过 100k 条目被 GitHub 截断(truncated)。"""
    if not branch:
        meta = await fetch_repo_meta(owner, repo, token)
        branch = meta["default_branch"]
    r = await _get(f"{_API}/repos/{owner}/{repo}/git/trees/{branch}", token, params={"recursive": "1"})
    d = r.json()
    items = []
    for it in d.get("tree", []):
        if it.get("type") in ("blob", "tree"):
            items.append({"path": it["path"], "type": it["type"], "size": it.get("size") or 0})
    if d.get("truncated"):
        items.append({"path": "__TRUNCATED__", "type": "meta", "size": 0})
    return items


async def fetch_languages(owner: str, repo: str, token: str | None = None) -> dict[str, int]:
    r = await _get(f"{_API}/repos/{owner}/{repo}/languages", token)
    return r.json()  # {"Python": 123456, "TypeScript": ...}


async def count_commits(owner: str, repo: str, token: str | None = None, branch: str | None = None) -> int | None:
    """通过 per_page=1 的 Link 头拿总页数; 无分页头时为 1。失败返回 None(不阻塞扫描)。"""
    try:
        params = {"per_page": "1"}
        if branch:
            params["sha"] = branch
        r = await _get(f"{_API}/repos/{owner}/{repo}/commits", token, params=params)
        link = r.headers.get("Link") or ""
        last = None
        import re as _re
        for part in link.split(","):
            if 'rel="last"' in part:
                m = _re.search(r"[?&]page=(\d+)", part)
                if m:
                    last = m.group(1)
        return int(last) if last else 1
    except GitHubRepoError:
        return None


async def fetch_file_text(owner: str, repo: str, path: str, token: str | None = None, branch: str | None = None) -> str | None:
    """取单文件文本(≤512KB); 不存在/二进制返回 None。"""
    params: dict = {}
    if branch:
        params["ref"] = branch
    try:
        r = await _get(f"{_API}/repos/{owner}/{repo}/contents/{path}", token, params=params or None)
    except GitHubRepoError as e:
        if e.status_code == 404:
            return None
        raise
    if r.headers.get("content-type", "").startswith("application/json"):
        d = r.json()
        if d.get("encoding") == "base64" and d.get("content"):
            try:
                return base64.b64decode(d["content"]).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return None
        return None
    return r.text


async def collect_repo_snapshot(owner: str, repo: str, branch: str | None = None) -> dict:
    """一次扫描需要的全部只读数据(meta+readme+tree+languages+commit 数)。"""
    token = await get_github_token()
    meta = await fetch_repo_meta(owner, repo, token)
    br = branch or meta["default_branch"]
    readme, tree, langs, commits = "", [], {}, None
    try:
        readme = await fetch_readme(owner, repo, token, br)
    except GitHubRepoError as e:
        if e.status_code != 404:
            raise
    tree = await fetch_tree(owner, repo, token, br)
    langs = await fetch_languages(owner, repo, token)
    commits = await count_commits(owner, repo, token, br)
    return {**meta, "branch_used": br, "readme": readme[:64_000], "tree": tree, "languages": langs, "commit_count": commits, "fetched_at": time.time()}
