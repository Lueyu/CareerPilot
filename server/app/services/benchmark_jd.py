"""JD(岗位描述)解析与对标版本生成 — 确定性规则引擎。

输入一段 JD 原文, 输出结构化解析(要求/关键词/能力域命中/硬性条件),
并可基于种子标准生成新的对标版本(JobStandard 非种子副本, 命中的能力域上调目标等级)。
不使用 LLM(确定性业务规则), 便于离线运行与结果可解释。
"""
from __future__ import annotations

import re
from typing import Any

# 能力域关键词表(小写匹配); 覆盖 8 个种子能力域的常见 JD 表述
SKILL_KEYWORDS: dict[str, list[str]] = {
    "agent": ["agent", "智能体", "多智能体", "planning", "规划", "工作流", "workflow", "autogen", "langgraph"],
    "tools": ["tool", "工具调用", "mcp", "function calling", "函数调用", "插件", "tool use", "api 集成"],
    "memory": ["memory", "记忆", "上下文管理", "会话状态", "长期记忆", "向量记忆"],
    "rag": ["rag", "检索增强", "召回", "embedding", "向量检索", "知识库", "重排", "rerank", "分块", "chunk"],
    "eval": ["评估", "评测", "eval", "benchmark", "数据集", "标注", "a/b", "回归测试", "评测集", "裁判"],
    "backend": ["后端", "数据库", "系统设计", "高并发", "缓存", "消息队列", "api 设计", "fastapi", "postgres", "mysql"],
    "reliability": ["可靠性", "幂等", "安全", "注入", "降级", "限流", "容错", "重试", "数据安全", "合规", "权限"],
    "deploy": ["部署", "可观测", "监控", "docker", "k8s", "kubernetes", "ci/cd", "运维", "prometheus", "灰度", "observability"],
}

# 目标等级随命中强度的提升幅度: ≥6 次命中=核心域 +0.5, ≥3 次 +0.25, ≥1 次 不变(仅记录)
HIT_STRONG = 6
HIT_MID = 3

# 职级 → 默认基准目标等级(用于全新能力域无种子目标时)
LEVEL_BASE = {"P4": 2.5, "P5": 3.0, "P6": 3.5, "P7": 4.0, "P8": 4.5}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。;；\n]+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 6]


def parse_jd(text: str, skills: list[dict[str, Any]]) -> dict[str, Any]:
    """解析 JD 原文。skills: [{key, name}], 返回 parsed 结构(与 JdSource.parsed 对齐)。"""
    low = text.lower()
    sents = _sentences(text)

    # 1) 能力域命中计数
    skill_hits: list[dict[str, Any]] = []
    matched_terms: list[str] = []
    for s in skills:
        terms = SKILL_KEYWORDS.get(s["key"], [s["key"], (s.get("name") or "").lower()])
        hits = 0
        for t in terms:
            t = t.lower().strip()
            if t and t in low:
                hits += low.count(t)
                if t not in matched_terms:
                    matched_terms.append(t)
        if hits > 0:
            skill_hits.append({"skill_key": s["key"], "skill_name": s.get("name") or s["key"], "hits": hits})
    skill_hits.sort(key=lambda x: -x["hits"])

    # 2) 要求条目(动词引导的职责句) 与 硬性条件(学历/年限/精通)
    req_pat = re.compile(r"(负责|参与|主导|设计|实现|搭建|优化|维护|构建|熟悉|掌握|了解|具备)")
    hard_pat = re.compile(r"(本科|硕士|博士|学历|年.{0,4}经验|精通|熟练|必须|优先|英文)")
    requirements = [p for p in sents if req_pat.search(p)][:15]
    hard_requirements = [p for p in sents if hard_pat.search(p)][:10]

    return {
        "requirements": requirements,
        "keywords": matched_terms[:20],
        "skill_hits": skill_hits,
        "hard_requirements": hard_requirements,
    }


def build_targets(base_targets: list[dict[str, Any]], parsed: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """基于种子目标与解析结果生成对标版本目标。

    返回 (new_targets, changes); new_targets: [{skill_id, weight_pct, target_level}]。
    规则: 命中 ≥HIT_STRONG → +0.5; ≥HIT_MID → +0.25; 上限 5.0; 不下调(种子假设保守)。
    """
    hit_map = {h["skill_key"]: h["hits"] for h in parsed.get("skill_hits", [])}
    changes: list[dict[str, Any]] = []
    new_targets: list[dict[str, Any]] = []
    for t in base_targets:
        key = t["skill_key"]
        hits = hit_map.get(key, 0)
        level = float(t["target_level"])
        if hits >= HIT_STRONG:
            level = min(5.0, level + 0.5)
        elif hits >= HIT_MID:
            level = min(5.0, level + 0.25)
        if abs(level - float(t["target_level"])) > 1e-9:
            changes.append({"skill_key": key, "skill_name": t.get("skill_name") or key,
                            "from": t["target_level"], "to": level, "hits": hits})
        new_targets.append({"skill_id": t["skill_id"], "weight_pct": t["weight_pct"], "target_level": level})
    return new_targets, changes
