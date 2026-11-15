"""评分引擎(规则引擎版, Rubric-based; LLM 评分通过 AgentAdapter 接入)。

MCQ: 选项精确匹配 -> CORRECT / WRONG。
Open: rubric 要点关键词覆盖度 -> PARTIAL 比例得分。
每题输出: verdict / points / feedback(逐要点) / 建议下一步。
"""
from __future__ import annotations

import re

from app.models import Question


def score_mcq(question: Question, choice_index: int | None) -> dict:
    correct = (question.rubric or {}).get("answer_index")
    if choice_index is None:
        return {"verdict": "WRONG", "points": 0.0, "hits": [], "misses": (question.rubric or {}).get("points", [])}
    ok = choice_index == correct
    return {
        "verdict": "CORRECT" if ok else "WRONG",
        "points": (question.rubric or {}).get("max_points", 5.0) if ok else 0.0,
        "hits": (question.rubric or {}).get("points", []) if ok else [],
        "misses": [] if ok else (question.rubric or {}).get("points", []),
    }


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z_]{2,}|\d+", text.lower()))


def score_open(question: Question, text: str | None) -> dict:
    rubric = question.rubric or {}
    points: list[dict] = rubric.get("points", [])
    max_points = rubric.get("max_points", 5.0)
    if not text or not text.strip():
        return {"verdict": "WRONG", "points": 0.0, "hits": [], "misses": points}

    answer_tokens = _tokenize(text)
    hits: list[dict] = []
    misses: list[dict] = []
    for p in points:
        keywords = p.get("keywords", [])
        if not keywords:
            hits.append(p)
            continue
        if any(k.lower() in text.lower() or k.lower() in answer_tokens for k in keywords):
            hits.append(p)
        else:
            misses.append(p)

    ratio = len(hits) / max(1, len(points))
    if ratio >= 0.999:
        verdict = "CORRECT"
    elif ratio >= 0.4:
        verdict = "PARTIAL"
    else:
        verdict = "WRONG"
    pts = round(max_points * ratio, 1)
    return {"verdict": verdict, "points": pts, "hits": hits, "misses": misses}


def score_question(question: Question, *, choice_index: int | None, text: str | None) -> dict:
    """统一入口: 按题型评分, 返回 {verdict, points, hits, misses}。"""
    if question.qtype == "mcq":
        return score_mcq(question, choice_index)
    return score_open(question, text)


def build_feedback(question: Question, result: dict) -> dict:
    """构造逐要点点评与建议(报告用)。"""
    rubric = question.rubric or {}
    advice = rubric.get("advice", "")
    hit_titles = [h.get("title", "") for h in result["hits"]]
    miss_titles = [m.get("title", "") for m in result["misses"]]
    next_hint = miss_titles[0] if miss_titles else (hit_titles[-1] if hit_titles else "")
    return {
        "hit_points": hit_titles,
        "missed_points": miss_titles,
        "advice": advice,
        "next": next_hint,
    }
