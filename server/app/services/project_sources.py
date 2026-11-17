"""Read-only, revision-pinned source access for project understanding.

No repository code is executed. Only explicitly permitted text paths can be read;
credentials are excluded by path and redacted by pattern before use or storage.
The limits and exclusions are coverage limitations, not an assertion of full audit.
"""
from __future__ import annotations

import base64
import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from app.services import github_repo, gitcode_repo


MAX_DOWNLOAD_BYTES = 768_000
MAX_TREE_FILES = 4000
_TEXT_EXTENSIONS = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".java",
    ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".rb", ".php",
    ".swift", ".kt", ".kts", ".scala", ".sh", ".ps1", ".sql", ".proto",
    ".graphql", ".vue", ".svelte", ".html", ".css", ".scss", ".md", ".rst",
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".xml", ".gradle",
}
_SKIP_PARTS = {
    ".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", ".next",
    "__pycache__", ".idea", ".vscode", ".ssh", ".aws", ".azure", "credentials",
    "secrets", "personal", "attachments", "exports", "backups", "data", "datasets",
    "logs", ".tmp", ".codex", ".agents",
}
_SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
               "id_rsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc"}
_SECRET_NAME = re.compile(r"(^|[._-])(secrets?|credentials?|tokens?|passwords?)([._-]|$)", re.I)


def is_safe_source_path(path: str) -> bool:
    if not isinstance(path, str) or not path or len(path) > 512 or path.endswith("/"):
        return False
    if "\\" in path or any(ord(c) < 32 for c in path) or ":" in path or path.startswith("/"):
        return False
    parts = path.split("/")
    if any(p in ("", ".", "..") or p.casefold() in _SKIP_PARTS for p in parts):
        return False
    name = parts[-1].casefold()
    if name.startswith(".env") or name in _SKIP_NAMES or _SECRET_NAME.search(name):
        return False
    return PurePosixPath(name).suffix in _TEXT_EXTENSIONS or name in {"readme", "dockerfile", "makefile", "cmakelists.txt", "containerfile"}


def _redacted_lines(match: re.Match) -> str:
    return "[REDACTED]" + "\n" * match.group(0).count("\n")


def redact_source(text: str) -> str:
    """Best-effort credential filtering that preserves source line numbering."""
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                  _redacted_lines, text, flags=re.S)
    text = re.sub(r"(?i)\b(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9]{16,}|github_pat_[a-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b", "[REDACTED]", text)
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/'\"]+:[^\s/@'\"]+@", r"\1[REDACTED]@", text)
    # Includes common config assignments and JSON/YAML header values. Preserve
    # the key and line, but not the value. Calls to getenv remain understandable
    # elsewhere in the source; no secret value is needed for architecture review.
    text = re.sub(
        r'''(?im)(["']?(?:[\w-]*(?:api[_-]?key|access[_-]?key|client[_-]?secret|secret[_-]?key|password|passwd|token|authorization)[\w-]*)["']?\s*(?::[^=\n]{0,30})?\s*[:=]\s*)([^\n]+)''',
        lambda m: m.group(1) + '"[REDACTED]"', text,
    )
    return text


def redact_value(value):
    """Sanitize string values without running source regexes over JSON syntax."""
    if isinstance(value, str):
        return redact_source(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: "[REDACTED]" if re.search(r"(?i)(api[_-]?key|access[_-]?key|client[_-]?secret|secret[_-]?key|password|passwd|token|authorization)", str(key))
                else redact_value(item) for key, item in value.items()}
    return value


@dataclass
class SourceRepository:
    kind: str
    owner: str
    repo: str
    branch: str
    revision: str
    manifest: list[dict]
    tree_truncated: bool
    token: str = field(repr=False)
    excluded_files: int = 0

    async def read_file(self, path: str) -> str | None:
        if not is_safe_source_path(path) or path not in {item["path"] for item in self.manifest}:
            raise ValueError("不允许读取未列出或敏感的文件路径")
        value = await self._read_text(path)
        if value is None or "\x00" in value:
            return None
        return redact_source(value)

    async def _read_text(self, path: str) -> str | None:
        owner, repo, encoded_path = quote(self.owner, safe=""), quote(self.repo, safe=""), quote(path, safe="/")
        if self.kind == "github":
            url = f"{github_repo._API}/repos/{owner}/{repo}/contents/{encoded_path}"
            headers = github_repo._headers(self.token, raw=True)
            params = {"ref": self.revision}
        else:
            url = f"{gitcode_repo._RAW}/{owner}/{repo}/raw/{self.revision}/{encoded_path}"
            headers, params = {"User-Agent": "CareerPilot-Local"}, None
        try:
            async with httpx.AsyncClient(timeout=30.0, trust_env=False, follow_redirects=False) as client:
                async with client.stream("GET", url, headers=headers, params=params) as response:
                    if response.status_code != 200:
                        return None
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_DOWNLOAD_BYTES:
                            return None
                    value = bytes(data).decode("utf-8", errors="strict")
                    # Some compatible providers still return Contents JSON despite
                    # raw Accept. Decode only a recognizable API envelope.
                    if "json" in response.headers.get("content-type", ""):
                        try:
                            envelope = json.loads(value)
                            if (isinstance(envelope, dict) and envelope.get("type") == "file"
                                    and envelope.get("path") == path and isinstance(envelope.get("sha"), str)
                                    and envelope.get("encoding") == "base64" and isinstance(envelope.get("content"), str)):
                                value = base64.b64decode(envelope["content"], validate=False).decode("utf-8", errors="strict")
                        except (ValueError, UnicodeError):
                            pass
                    return value
        except (httpx.HTTPError, UnicodeError):
            return None


async def _gitcode_tree(owner: str, repo: str, revision: str, token: str) -> tuple[list[dict], bool]:
    """Keep real file types and mark partial pagination accurately.

    Unlike the old static scanner this does not masquerade directories as files.
    A partial tree remains explicitly partial, including request failures.
    """
    entries: dict[str, dict] = {}
    async def page_result(page):
        try:
            return await gitcode_repo._tree_page(owner, repo, revision, token, page)
        except gitcode_repo.GitCodeRepoError:
            if page == 1:
                raise
            return None

    # Read first page alone for small repositories, then bounded parallel groups.
    for start in (1, 2, 7, 12, 17, 22, 27):
        pages = [1] if start == 1 else list(range(start, min(start + 5, 31)))
        results = await asyncio.gather(*(page_result(page) for page in pages))
        for batch in results:
            if batch is None:
                return list(entries.values()), True
            for item in batch:
                path = item.get("path") or item.get("name")
                if isinstance(path, str) and item.get("type") == "blob":
                    entries[path] = {"path": path, "type": "blob", "size": item.get("size") or 0}
            if len(batch) < gitcode_repo._TREE_PAGE_SIZE:
                return list(entries.values()), False
    return list(entries.values()), True


async def open_repository(project_info: dict) -> SourceRepository:
    kind = project_info.get("kind") or "github"
    if kind not in ("github", "gitcode"):
        raise ValueError("AI 深度分析需要 GitHub 或 GitCode 仓库，手工项目请维护项目资料")
    owner, repo = str(project_info.get("owner") or ""), str(project_info.get("repo") or "")
    if not owner or not repo or any(c in owner + repo for c in "/\\?#\r\n"):
        raise ValueError("仓库地址无效")
    provider = github_repo if kind == "github" else gitcode_repo
    try:
        token = await (github_repo.get_github_token() if kind == "github" else gitcode_repo.get_gitcode_token())
        branch = project_info.get("branch")
        if not branch:
            meta = await provider.fetch_repo_meta(owner, repo, token)
            branch = meta["default_branch"]
        response = await provider._get(
            f"{provider._API}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/commits/{quote(str(branch), safe='')}", token,
        )
        revision = response.json().get("sha")
        if not isinstance(revision, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", revision):
            raise ValueError("无法获取真实提交版本；未使用分支名或扫描指纹冒充版本")
        if kind == "github":
            tree = await github_repo.fetch_tree(owner, repo, token, revision)
            truncated = any(it.get("path") == "__TRUNCATED__" for it in tree)
        else:
            tree, truncated = await _gitcode_tree(owner, repo, revision, token)
    except (github_repo.GitHubRepoError, gitcode_repo.GitCodeRepoError, httpx.HTTPError) as exc:
        # Never forward arbitrary remote messages which might echo credentials.
        code = getattr(exc, "status_code", None)
        raise ValueError(f"仓库读取失败（{kind}，HTTP {code or '网络错误'}），请检查仓库、分支和访问配置") from None
    unique = {it["path"]: {"path": it["path"], "size": it.get("size") or 0}
              for it in tree if it.get("type") == "blob" and is_safe_source_path(it.get("path"))}
    # Deterministic order is for a bounded manifest only; the model selects source
    # files by architecture rather than scoring filename matches as competence.
    manifest = [unique[p] for p in sorted(unique)]
    truncated = truncated or len(manifest) > MAX_TREE_FILES
    excluded = sum(1 for it in tree if it.get("type") == "blob") - len(unique)
    return SourceRepository(kind, owner, repo, str(branch), revision.lower(), manifest[:MAX_TREE_FILES], truncated, token or "", excluded)
