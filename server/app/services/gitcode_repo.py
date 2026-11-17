"""GitCode 只读分析(真实 API v5 + raw 文件服务器)。

只做只读调用: 仓库元信息 / README / 文件树 / 语言分布 / commit 数 / 单文件。
GitCode(gitcode.com, CSDN 旗下) 的 REST API 基址 https://gitcode.com/api/v5;
原始文件另由 https://raw.gitcode.com/{owner}/{repo}/raw/{branch}/{path} 直接提供(不限速)。
鉴权用 ``PRIVATE-TOKEN`` 请求头(实测: meta 端点要求该头, 缺失返回 400; access_token 查询参数被忽略)。
匿名访问公共仓库有限速; 可设环境变量 GITCODE_TOKEN(GitCode 个人访问令牌) 提升额度。
"""
import asyncio
import time
from urllib.parse import quote

import httpx

from app.config import get_settings

_API = "https://gitcode.com/api/v5"
_RAW = "https://raw.gitcode.com"

_HEADERS = {"User-Agent": "CareerPilot-Local", "Accept": "application/json"}

# README 候选名(按常见度排序); 命中第一个非空 200 即用。
_README_CANDIDATES = (
    "README.md", "README", "readme.md", "Readme.md", "README.MD",
    "README.rst", "README_en.md", "README_zh.md",
)

# 文件树分页: GitCode 每页固定 100 条、约 16s/页, 逐页串行太慢 → 并发拉取。
# 实测 eBackup/open-eBackup 是万级条目的巨型仓库, 且树 API 先返回目录(tree)后返回文件(blob)。
# 上限 30 页(3000 条目), 超出打 __TRUNCATED__ 标记; 目录条目也作为路径证据(带尾斜杠)喂给 analyzer。
_MAX_TREE_PAGES = 30
_TREE_PAGE_SIZE = 100
_TREE_CONCURRENCY = 16


class GitCodeRepoError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"GitCode {status_code}: {detail}")


async def get_gitcode_token() -> str:
    """个人访问令牌: 环境变量 GITCODE_TOKEN(无则匿名, 公共仓库仍可用但有限速)。"""
    return (get_settings().gitcode_token or "").strip()


async def _get(url: str, token: str | None, *, params: dict | None = None) -> httpx.Response:
    headers = dict(_HEADERS)
    if token:
        headers["PRIVATE-TOKEN"] = token
    async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
        r = await client.get(url, headers=headers, params=params or None)
    if r.status_code >= 400:
        try:
            d = r.json()
            msg = d.get("error_message") or d.get("message") or r.text[:200]
        except Exception:  # noqa: BLE001
            msg = r.text[:200]
        raise GitCodeRepoError(r.status_code, msg)
    return r


async def _get_raw(url: str) -> str | None:
    """raw 服务器直读; 非 200 返回 None(不抛错, README/单文件读失败可降级)。"""
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            r = await client.get(url, headers={"User-Agent": "CareerPilot-Local"})
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:  # noqa: BLE001
        return None
    return None


async def fetch_repo_meta(owner: str, repo: str, token: str | None = None) -> dict:
    """仓库元信息; 匿名 429 由调用方降级为 URL 推导的默认值。"""
    r = await _get(f"{_API}/repos/{owner}/{repo}", token)
    d = r.json()
    return {
        "full_name": d.get("full_name") or f"{owner}/{repo}",
        "description": d.get("description"),
        "default_branch": d.get("default_branch") or "master",
        "language": d.get("language"),
        "languages_meta": {"stars": d.get("stargazers_count", 0), "size_kb": 0},
        "pushed_at": d.get("pushed_at"),
        "html_url": d.get("html_url") or f"https://gitcode.com/{owner}/{repo}",
        "private": bool(d.get("private")),
    }


async def fetch_readme(owner: str, repo: str, branch: str | None = None) -> str:
    """README 走 raw 服务器(不限速), 按候选名逐个尝试, 取第一个非空 200。"""
    br = branch or "master"
    for name in _README_CANDIDATES:
        text = await _get_raw(f"{_RAW}/{owner}/{repo}/raw/{quote(br)}/{name}")
        if text:
            return text
    return ""


async def _tree_page(owner: str, repo: str, branch: str, token: str | None, page: int) -> list[dict]:
    r = await _get(
        f"{_API}/repos/{owner}/{repo}/git/trees/{branch}", token,
        params={"recursive": "1", "per_page": str(_TREE_PAGE_SIZE), "page": str(page)},
    )
    return r.json().get("tree") or []


async def fetch_tree(owner: str, repo: str, branch: str, token: str | None = None) -> list[dict]:
    """返回 [{path, type(blob|tree), size}] 的扁平列表, 直接喂给 project_analyzer。

    关键适配: GitCode 递归树把目录(tree)排在文件(blob)之前, 巨型仓库的 blob 要几十页后才出现,
    而 analyzer 只按 ``type=="blob"`` 的 path 匹配目录前缀规则。这里把目录也归一化成
    ``type="blob"`` 且路径带尾斜杠(如 "src/server/"), 使目录前缀规则照常命中、文件级规则不误命中;
    这样在无法廉价枚举全部文件时, 目录结构本身仍能给出能力域信号(等级停在 HIGH, 不读内容)。
    """
    items: dict[str, dict] = {}

    def add(batch: list[dict]) -> None:
        for it in batch:
            t = it.get("type")
            path = it.get("path") or it.get("name")
            if not path:
                continue
            if t == "blob":
                key = path
            elif t == "tree":
                key = path.rstrip("/") + "/"
            else:
                continue
            if key not in items:
                items[key] = {"path": key, "type": "blob", "size": 0}

    # 根树兜底: 顶层 Dockerfile/Makefile/目录名一定在场(递归分页可能被 cap 截断)。
    try:
        r = await _get(f"{_API}/repos/{owner}/{repo}/git/trees/{branch}", token)
        add(r.json().get("tree") or [])
    except GitCodeRepoError:
        pass

    first = await _tree_page(owner, repo, branch, token, 1)
    add(first)
    if len(first) < _TREE_PAGE_SIZE:
        return list(items.values())  # 小仓库, 一页内结束

    sem = asyncio.Semaphore(_TREE_CONCURRENCY)

    async def fetch_page(n: int) -> tuple[int, list[dict]]:
        async with sem:
            try:
                return n, await _tree_page(owner, repo, branch, token, n)
            except GitCodeRepoError:
                return n, []

    results = await asyncio.gather(*[fetch_page(n) for n in range(2, _MAX_TREE_PAGES + 1)])
    truncated = True  # 默认假设超上限; 找到早于上限结束的页则解除
    for _n, batch in results:
        add(batch)
        if len(batch) < _TREE_PAGE_SIZE:
            truncated = False  # 出现 <100 条的页 → 已到末页(其后页为空)
            break

    result = list(items.values())
    if truncated:
        result.append({"path": "__TRUNCATED__", "type": "meta", "size": 0})
    return result


async def fetch_languages(owner: str, repo: str, token: str | None = None) -> dict[str, float]:
    r = await _get(f"{_API}/repos/{owner}/{repo}/languages", token)
    return r.json()  # GitCode 返回百分比(float, 和≈100), 非字节数


async def count_commits(owner: str, repo: str, branch: str | None = None, token: str | None = None) -> int | None:
    """commit 总数(尽力而为): 从分页头推断; 取不到返回 None(不阻塞扫描)。"""
    import re as _re
    try:
        params = {"per_page": "1"}
        if branch:
            params["sha"] = branch
        r = await _get(f"{_API}/repos/{owner}/{repo}/commits", token, params=params)
        for header in ("X-Total", "X-Total-Count"):
            if r.headers.get(header):
                return int(r.headers[header])
        link = r.headers.get("Link") or ""
        last = None
        for part in link.split(","):
            if 'rel="last"' in part:
                m = _re.search(r"[?&]page=(\d+)", part)
                if m:
                    last = m.group(1)
        return int(last) if last else None
    except (GitCodeRepoError, ValueError):
        return None


async def fetch_gitcode_file_text(
    owner: str, repo: str, path: str, token: str | None = None, branch: str | None = None,
) -> str | None:
    """取单文件文本(经 raw 服务器, ≤512KB); 不存在/二进制返回 None。

    ``token`` 仅为与 github_repo.fetch_file_text 保持签名一致而保留(raw 服务器无需鉴权)。
    """
    br = branch or "master"
    text = await _get_raw(f"{_RAW}/{owner}/{repo}/raw/{quote(br)}/{quote(path, safe='/')}")
    if text is None:
        return None
    return text[:512_000]


async def collect_gitcode_snapshot(owner: str, repo: str, branch: str | None = None) -> dict:
    """一次扫描需要的全部只读数据(meta+readme+tree+languages+commit 数)。

    产出结构与 github_repo.collect_repo_snapshot 同构, 可直接喂给 project_analyzer。
    """
    token = await get_gitcode_token()
    try:
        meta = await fetch_repo_meta(owner, repo, token)
    except GitCodeRepoError:
        # 匿名 meta 可能 429; 用 URL 推导的默认值兜底, 其余字段仍可从 tree/raw 拿到。
        meta = {
            "full_name": f"{owner}/{repo}",
            "description": None,
            "default_branch": branch or "master",
            "language": None,
            "languages_meta": {"stars": 0, "size_kb": 0},
            "pushed_at": None,
            "html_url": f"https://gitcode.com/{owner}/{repo}",
            "private": False,
        }
    br = branch or meta["default_branch"] or "master"

    # 分支可能不匹配(master/main); tree 404 时回退到另一个常见默认分支。
    try:
        tree = await fetch_tree(owner, repo, br, token)
    except GitCodeRepoError as e:
        if e.status_code == 404 and br != "master":
            br, tree = "master", await fetch_tree(owner, repo, "master", token)
        elif e.status_code == 404 and br != "main":
            br, tree = "main", await fetch_tree(owner, repo, "main", token)
        else:
            raise

    readme = await fetch_readme(owner, repo, br)
    try:
        langs = await fetch_languages(owner, repo, token)
    except GitCodeRepoError:
        langs = {}
    commits = await count_commits(owner, repo, br, token)
    return {**meta, "branch_used": br, "readme": readme[:64_000], "tree": tree,
            "languages": langs, "commit_count": commits, "fetched_at": time.time()}
