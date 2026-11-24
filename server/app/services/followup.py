"""确定性追问建议(诊断报告/面试官视角)。

规则引擎: 根据作答结果把「下一层考察点」归入 7 类 Gap,
每题最多 2 条, 全部可解释(附 why)。LLM 不参与——追问建议必须稳定可复现。

7 类 Gap: Concept(概念) / Mechanism(机制) / Application(应用) /
Boundary(边界) / Trade-off(权衡) / Expression(表达) / Verification(验证)。
"""
from __future__ import annotations

from app.generation_i18n import generation_message
from app.i18n import Locale, request_language

# Keep rule IDs stable in saved results.
_RULES = (
    ("Expression", "blank"),
    ("Mechanism", "miss"),
    ("Boundary", "partial"),
    ("Trade-off", "correct"),
    ("Verification", "always"),
)


def suggest_followups(
    title: str, verdict: str, missed: list[str] | None = None, *, language: Locale | None = None,
) -> list[dict[str, str]]:
    """按作答结果生成 ≤2 条追问建议。missed 为缺失要点标题列表(可为空)。"""
    missed = [m for m in (missed or []) if str(m).strip()]
    blank = verdict == "WRONG" and not missed  # WRONG 但说不出缺什么 → 视为没答上来

    language = language or request_language()
    verdict_rule = {"PARTIAL": "partial", "CORRECT": "correct"}.get(verdict)
    applicable = {"always", verdict_rule}
    if blank:
        applicable.add("blank")
    if missed:
        applicable.add("miss")

    picked = []
    for kind, when in _RULES:
        if when not in applicable:
            continue
        prefix = f"followup.{kind}"
        question = generation_message(f"{prefix}.question", language)
        if when == "miss":
            question = question.format(miss=missed[0][:30])
        picked.append({
            "kind": kind,
            "label": generation_message(f"{prefix}.label", language),
            "question": question,
            "why": generation_message(f"{prefix}.why", language),
        })
        if len(picked) == 2:
            break
    return picked
