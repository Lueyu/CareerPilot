"""GitHub 内容发布服务: 用 httpx 直接调 GitHub REST API(不依赖 PyGithub)。

提供原子操作(建分支/写文件/开 PR/查构建/合并/读文件), 由 API 层编排
成一次「发布运行」并落 Trace + 幂等 + 审计。支持 dry_run: 只读操作真实执行
(拿真实 base_sha), 写操作返回 planned 结构而不触网络。
"""
from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from sqlalchemy import select

from app.config import get_settings

# GitHub token 运行时解析缓存(AppSetting 'github' 优先于 env; 设置页保存后刷新)
_GH_CACHE: str | None = None
_GH_AT: float = 0.0
_GH_TTL = 10.0


async def get_github_token() -> str:
    """设置页可在线修改 GitHub token: DB AppSetting('github') 优先, env 兜底。"""
    global _GH_CACHE, _GH_AT
    now = time.monotonic()
    if _GH_CACHE is not None and (now - _GH_AT) < _GH_TTL:
        return _GH_CACHE
    token = get_settings().github_token or ""
    try:
        from app.db import SessionLocal
        from app.models import AppSetting

        async with SessionLocal() as session:
            row = (await session.execute(select(AppSetting).where(AppSetting.key == "github"))).scalar_one_or_none()
        if row and isinstance(row.value, dict) and row.value.get("token"):
            token = row.value["token"]
    except Exception:
        pass
    _GH_CACHE = token
    _GH_AT = now
    return token


def set_github_token_cache(value: str | None) -> None:
    """设置页保存/清除 token 后立即刷新缓存。"""
    global _GH_CACHE, _GH_AT
    _GH_CACHE = value
    _GH_AT = time.monotonic()


class GitHubError(Exception):
    """GitHub API 错误, 携带状态码与响应 detail, 供上层落库/告警。"""

    def __init__(self, message: str, status_code: int | None = None, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class GitHubPublisher:
    def __init__(self, token: str | None = None, api_url: str | None = None, dry_run: bool = False):
        s = get_settings()
        self.token = token or s.github_token
        self.api_url = (api_url or s.github_api_url).rstrip("/")
        self.dry_run = dry_run

    # ---- 基础设施 ----
    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CareerPilot-Publisher",
        }

    async def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        if not self.token:
            raise GitHubError("未配置 GITHUB_TOKEN, 无法访问 GitHub API", detail="missing_token")
        url = f"{self.api_url}{path}"
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            resp = await client.request(method, url, headers=self.headers, json=json)
        if resp.status_code >= 400:
            raise GitHubError(
                f"GitHub {method} {path} -> {resp.status_code}",
                status_code=resp.status_code,
                detail=(resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:500]),
            )
        return resp.json() if resp.content else {}

    def _plan(self, op: str, **kw: Any) -> dict:
        return {"op": op, "dry_run": True, **kw}

    # ---- 原子操作 ----
    async def get_base_sha(self, repo: str, branch: str) -> str:
        """读 base 分支最新 commit sha(只读, dry_run 也真实执行)。"""
        data = await self._request("GET", f"/repos/{repo}/branches/{branch}")
        return data["commit"]["sha"]

    async def create_branch(self, repo: str, branch: str, base_sha: str) -> dict:
        if self.dry_run:
            return self._plan("create_branch", repo=repo, branch=branch, base_sha=base_sha)
        return await self._request(
            "POST", f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )

    async def get_file(self, repo: str, path: str, ref: str) -> dict | None:
        """读某 ref 下文件(返回含 content(已解码)/sha 的 dict, 不存在返回 None)。"""
        try:
            data = await self._request("GET", f"/repos/{repo}/contents/{path}?ref={ref}")
        except GitHubError as e:
            if e.status_code == 404:
                return None
            raise
        if data.get("encoding") == "base64" and data.get("content"):
            data["content"] = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data

    async def write_file(self, repo: str, path: str, content: str, branch: str, message: str, sha: str | None = None) -> dict:
        """写入/更新文件到指定分支。"""
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode(),
            "branch": branch,
        }
        if sha is not None:
            payload["sha"] = sha
        if self.dry_run:
            return self._plan("write_file", repo=repo, path=path, branch=branch, message=message, has_sha=sha is not None)
        return await self._request("PUT", f"/repos/{repo}/contents/{path}", json=payload)

    async def create_pr(self, repo: str, head: str, base: str, title: str, body: str) -> dict:
        if self.dry_run:
            return self._plan("create_pr", repo=repo, head=head, base=base, title=title)
        return await self._request(
            "POST", f"/repos/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )

    async def merge_pr(self, repo: str, pr_number: int, method: str = "squash") -> dict:
        if self.dry_run:
            return self._plan("merge_pr", repo=repo, pr_number=pr_number, method=method)
        return await self._request(
            "PUT", f"/repos/{repo}/pulls/{pr_number}/merge",
            json={"merge_method": method},
        )

    async def get_workflow_runs(self, repo: str, head_sha: str) -> list[dict]:
        """查该 head_sha 的 Actions runs(可能为空 = 仓库无 workflow)。"""
        data = await self._request("GET", f"/repos/{repo}/actions/runs?head_sha={head_sha}")
        return data.get("workflow_runs", []) or []

    async def get_commit_status(self, repo: str, ref: str) -> dict:
        """查 commit 的 combined status(可能空)。"""
        return await self._request("GET", f"/repos/{repo}/commits/{ref}/status")

    async def get_branch_sha(self, repo: str, branch: str) -> str | None:
        """查分支是否存在, 返回其 sha; 不存在返回 None。"""
        try:
            data = await self._request("GET", f"/repos/{repo}/branches/{branch}")
            return data["commit"]["sha"]
        except GitHubError as e:
            if e.status_code == 404:
                return None
            raise


def make_publisher(dry_run: bool = False) -> GitHubPublisher:
    return GitHubPublisher(dry_run=dry_run)


async def make_publisher_rt(dry_run: bool = False) -> GitHubPublisher:
    """运行时 publisher: token 从 AppSetting/env 动态解析(设置页在线修改即时生效)。"""
    token = await get_github_token()
    return GitHubPublisher(token=token or None, dry_run=dry_run)
