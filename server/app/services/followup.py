"""确定性追问建议(诊断报告/面试官视角)。

规则引擎: 根据作答结果把「下一层考察点」归入 7 类 Gap,
每题最多 2 条, 全部可解释(附 why)。LLM 不参与——追问建议必须稳定可复现。

7 类 Gap: Concept(概念) / Mechanism(机制) / Application(应用) /
Boundary(边界) / Trade-off(权衡) / Expression(表达) / Verification(验证)。
"""
from __future__ import annotations

from typing import Any

# 追问模板按优先级排列: 第一个命中的规则给第一条; 第二条用次优先规则(不重复 kind)。
_RULES: list[dict[str, Any]] = [
    {
        "kind": "Expression",
        "label": "表达",
        "when": "blank",
        "question": "面试里遇到完全没思路的题，你会怎么争取部分分？先说什么、再说什么？",
        "why": "这题几乎空白。空白作答通常不是不会，而是缺少答题框架——面试官想看到你拆解问题的路径。",
    },
    {
        "kind": "Mechanism",
        "label": "机制",
        "when": "miss",
        "question": "请具体展开「{miss}」的机制：它分哪几步？每一步的输入和输出是什么？",
        "why": "作答里缺少这个要点的机制展开——这是本题最直接的失分点，讲清它就补上了主要缺口。",
    },
    {
        "kind": "Boundary",
        "label": "边界",
        "when": "partial",
        "question": "你说的方案在什么规模/流量/数据量下会失效？失效时怎么降级？",
        "why": "主流程你已答对，面试官下一层一定问边界——答出失效条件才证明真正理解了适用范围。",
    },
    {
        "kind": "Trade-off",
        "label": "权衡",
        "when": "correct",
        "question": "如果要求把这套方案的成本或复杂度砍掉一半，你会砍什么、留什么？为什么？",
        "why": "满分说明主线清楚，下一步面试官考察权衡取舍——能否主动放弃某些东西体现工程判断力。",
    },
    {
        "kind": "Verification",
        "label": "验证",
        "when": "always",
        "question": "上线后你怎么验证这套设计真的有效？会看哪几个指标？",
        "why": "无论答得如何，「怎么验证」都是 AI 工程面试的高频收尾题——提前准备一层数据闭环。",
    },
]


def suggest_followups(title: str, verdict: str, missed: list[str] | None = None) -> list[dict[str, str]]:
    """按作答结果生成 ≤2 条追问建议。missed 为缺失要点标题列表(可为空)。"""
    missed = [m for m in (missed or []) if str(m).strip()]
    blank = verdict == "WRONG" and not missed  # WRONG 但说不出缺什么 → 视为没答上来

    picked: list[dict[str, str]] = []
    used_kinds: set[str] = set()

    def _pick(rule: dict[str, Any]) -> None:
        if len(picked) >= 2 or rule["kind"] in used_kinds:
            return
        q = rule["question"]
        if rule["when"] == "miss" and missed:
            q = q.format(miss=missed[0][:30])
        elif rule["when"] == "miss":
            return
        picked.append({"kind": rule["kind"], "label": rule["label"], "question": q, "why": rule["why"]})
        used_kinds.add(rule["kind"])

    for rule in _RULES:
        when = rule["when"]
        if when == "blank" and blank:
            _pick(rule)
        elif when == "miss" and missed:
            _pick(rule)
        elif when == "partial" and verdict == "PARTIAL":
            _pick(rule)
        elif when == "correct" and verdict == "CORRECT":
            _pick(rule)
        elif when == "always":
            _pick(rule)

    return picked
