"""Project Analyzer — 文件模式 → 能力域 的确定性映射规则引擎。

不用大 prompt: 全部是可解释的路径/内容规则, 每条发现都带证据(文件路径或 README 引用)。
证据等级:
- VERIFIED        : 读到了代码/配置内容, 且内容确认该能力(如 retry 代码确实有退避重试)
- HIGH            : 结构证据(文件/目录名清晰指向该能力, 未读内容)
- UNVERIFIED_CLAIM: 仅 README 声明, 无对应文件
"""
import re

# 路径规则: (domain, 编译后的路径正则, 发现标题, 说明)
# domain 取值 = skills 表的 8 个 key: agent/tools/memory/rag/eval/backend/reliability/deploy
PATH_RULES: list[tuple[str, re.Pattern, str, str]] = [
    ("agent", re.compile(r"(^|/)(agents?|agent_core|planner|orchestrat\w*)/", re.I), "Agent 编排代码",
     "存在 agent/规划/编排目录, 说明具备多步任务编排结构。"),
    ("agent", re.compile(r"(^|/)[^/]*agent[^/]*\.(py|ts|js|tsx)$", re.I), "Agent 模块文件",
     "文件名含 agent, 候选的 agent 实现入口。"),
    ("tools", re.compile(r"(^|/)(tools?|mcp[^/]*|adapters?|connectors?)/", re.I), "工具/MCP 适配层",
     "存在 tools/MCP/适配器目录, 指向工具调用与协议接入。"),
    ("memory", re.compile(r"(^|/)[^/]*(memory|state(_|store|manager)?|session_store)[^/]*\.(py|ts|js)$", re.I), "状态与记忆模块",
     "存在 memory/state 命名的模块文件。"),
    ("rag", re.compile(r"(^|/)(rag|retriev\w*|embed\w*|vector_store|indexing)/", re.I), "RAG/检索链路",
     "存在检索/向量/嵌入相关目录。"),
    ("eval", re.compile(r"(^|/)(evals?|evaluation|benchmarks?|rubrics?|judge)/", re.I), "评测资产目录",
     "存在评测/基准目录, 是 Agent Evaluation 的直接证据。"),
    ("eval", re.compile(r"(^|/)[^/]*eval[^/]*\.py$", re.I), "评测脚本",
     "文件名含 eval 的 Python 脚本。"),
    ("backend", re.compile(r"(^|/)(server|api|backend|app)/", re.I), "后端服务目录",
     "存在服务端代码目录, 承担 API/业务逻辑。"),
    ("backend", re.compile(r"(^|/)(models?|schema[s]?|migrations?)/", re.I), "数据模型/迁移",
     "存在 ORM 模型或迁移目录, 说明有持久化设计。"),
    ("reliability", re.compile(r"(^|/)[^/]*(retry|backoff|guard|rate_?limit|circuit|fallback)[^/]*\.(py|ts|js)$", re.I), "可靠性组件",
     "存在重试/限流/降级命名模块。"),
    ("reliability", re.compile(r"(^|/)tests?/", re.I), "测试目录",
     "存在测试目录; 自动化测试是可验证性的基础证据。"),
    ("deploy", re.compile(r"(^|/)(deploy\w*|docker[^/]*|k8s|infra|\.github/workflows)/", re.I), "部署/CI 配置",
     "存在部署/容器/CI 配置目录。"),
    ("deploy", re.compile(r"(^|/)(Dockerfile|docker-compose\.ya?ml|Makefile)$", re.I), "构建/部署入口文件",
     "存在 Dockerfile/compose/Makefile 等部署入口。"),
]

# 内容确认规则: 路径命中后再读文件内容, 内容匹配则升为 VERIFIED
CONTENT_CONFIRM: dict[str, list[str]] = {
    "agent": [r"plan|step|react|tool_call|reasoning|loop|max_iter", r"agent"],
    "tools": [r"tool|schema|function_call|mcp"],
    "memory": [r"memory|history|checkpoint|session|persist"],
    "rag": [r"retriev|embed|chunk|vector|top_?k|rerank"],
    "eval": [r"assert|expect|score|rubric|pass_rate|case"],
    "backend": [r"route|router|endpoint|model|crud|session"],
    "reliability": [r"retry|backoff|timeout|except|fallback|limit"],
    "deploy": [r"build|image|service|deploy|workflow|on:|steps"],
}

# README 声明词 → 域: 只有 README 提到而无文件证据 → UNVERIFIED_CLAIM
README_CLAIMS: list[tuple[str, re.Pattern, str]] = [
    ("agent", re.compile(r"(multi[- ]?agent|自主规划|任务编排|agent 框架|planning)", re.I), "README 声明 Agent 能力"),
    ("tools", re.compile(r"(tool ?call|mcp|function ?call|工具调用)", re.I), "README 声明工具调用"),
    ("memory", re.compile(r"(记忆|memory|状态管理|上下文持久)", re.I), "README 声明状态/记忆管理"),
    ("rag", re.compile(r"(rag|检索增强|向量检索|knowledge base|知识库)", re.I), "README 声明 RAG"),
    ("eval", re.compile(r"(评测|评估|benchmark|eval(uation)? suite|准确率)", re.I), "README 声明评测能力"),
    ("reliability", re.compile(r"(容错|降级|重试|高可用|幂等)", re.I), "README 声明可靠性设计"),
]

_MAX_READ_FILES = 8      # 内容确认最多读的文件数(控制 API 用量)
_MAX_READ_BYTES = 12_000  # 每文件最多读的字符数
_IGNORE = re.compile(r"(^|/)(node_modules|\.git|dist|build|\.next|__pycache__|vendor|\.venv)/")


async def analyze_snapshot(snapshot: dict, file_reader=None) -> dict:
    """对 collect_repo_snapshot 的产物做确定性分析。

    file_reader: async (path) -> str | None, 用于内容确认(调用方注入 fetch_file_text 节流包装); None 则只做结构分析。
    返回 {findings: [...], matrix: [...], stats: {...}}。
    """
    tree = [it for it in snapshot.get("tree", []) if it.get("type") == "blob" and not _IGNORE.search(it["path"])]
    readme = snapshot.get("readme") or ""
    findings: list[dict] = []

    # ---- 1) 路径规则(结构证据 HIGH) ----
    to_confirm: list[tuple[str, str, dict]] = []  # (domain, path, finding)
    for it in tree:
        p = it["path"]
        for domain, rx, title, detail in PATH_RULES:
            if rx.search(p):
                f = {
                    "domain": domain, "level": "HIGH", "title": title,
                    "detail": detail, "file_path": p,
                }
                findings.append(f)
                to_confirm.append((domain, p, f))
                break  # 一个文件只产一条发现(取第一条匹配规则)

    # ---- 2) 内容确认(抽样读文件 → VERIFIED) ----
    if file_reader is not None:
        picked: list[tuple[str, str, dict]] = []
        seen_files: set[str] = set()
        # 每个域先挑一个代表文件, 再补齐到上限
        for domain, p, f in to_confirm:
            if f["level"] != "HIGH":
                continue
            if all(d != domain for d, _, _ in picked):
                picked.append((domain, p, f))
                seen_files.add(p)
        for domain, p, f in to_confirm:
            if len(picked) >= _MAX_READ_FILES:
                break
            if p not in seen_files:
                picked.append((domain, p, f))
                seen_files.add(p)
        for domain, p, f in picked:
            try:
                text = await file_reader(p)
            except Exception:  # noqa: BLE001
                text = None
            if not text:
                continue
            text = text[:_MAX_READ_BYTES]
            if any(re.search(rx, text, re.I) for rx in CONTENT_CONFIRM.get(domain, [])):
                f["level"] = "VERIFIED"
                f["detail"] = f"{f['detail']}（已读文件内容确认）"

    # ---- 3) README 声明(无文件证据 → UNVERIFIED_CLAIM) ----
    for domain, rx, title in README_CLAIMS:
        m = rx.search(readme or "")
        if not m:
            continue
        has_file = any(f["domain"] == domain and f["level"] in ("VERIFIED", "HIGH") for f in findings)
        if not has_file:
            start = max(0, m.start() - 30)
            quote = (readme or "")[start : m.end() + 30].replace("\n", " ").strip()
            findings.append({
                "domain": domain, "level": "UNVERIFIED_CLAIM", "title": title,
                "detail": f"README 声明了该能力但未发现对应代码结构。引用: “…{quote}…”",
                "file_path": "README.md",
            })

    # ---- 4) 语言分布发现 ----
    langs = snapshot.get("languages") or {}
    if langs:
        total = sum(langs.values()) or 1
        top = sorted(langs.items(), key=lambda kv: -kv[1])[:3]
        findings.insert(0, {
            "domain": "backend", "level": "VERIFIED",
            "title": f"主要语言: {' / '.join(k for k, _ in top)}",
            "detail": "语言分布(字节): " + ", ".join(f"{k} {v * 100 // total}%" for k, v in top) + "。",
            "file_path": "(languages API)",
        })

    # ---- 5) 能力矩阵(确定性计分) ----
    domains = ["agent", "tools", "memory", "rag", "eval", "backend", "reliability", "deploy"]
    matrix = []
    for d in domains:
        fs = [f for f in findings if f["domain"] == d]
        score = 0.0
        for f in fs:
            score += {"VERIFIED": 1.0, "HIGH": 0.6, "UNVERIFIED_CLAIM": 0.25}.get(f["level"], 0)
        level = round(min(5.0, max(score, 0.5 if fs else 0.0)), 1)
        matrix.append({"key": d, "level": level, "findings": len(fs)})

    stats = {
        "files": len(tree),
        "commits": snapshot.get("commit_count"),
        "languages": list(langs.keys())[:5],
        "truncated": any(it.get("path") == "__TRUNCATED__" for it in snapshot.get("tree", [])),
    }
    return {"findings": findings, "matrix": matrix, "stats": stats}
