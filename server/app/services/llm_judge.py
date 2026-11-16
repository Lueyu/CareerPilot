"""开放题 LLM Judge。

评分纪律(与项目原则一致):
- LLM 只对「开放题」给出带理由的评分; MCQ 仍走确定性规则(scorer.score_mcq), 不用 LLM。
- 调用失败 / JSON 解析失败 / 字段非法 → 返回 None, 由调用方把该题置为 SCORING_FAILED
  (points 保持 NULL), 绝不用规则假分或 0 分顶替。
- verdict 由 points/max 的确定性比例重新计算, 不信任 LLM 自报的 verdict。
"""
from __future__ import annotations

import html as _html
import json
import re as _re
from typing import Any

JUDGE_SYSTEM = """你是严格、可解释的 AI 工程面试评分官。根据「题目、评分要点(rubric)、候选人作答」独立打分。

评分纪律:
- 只依据评分要点与参考答案评分；候选人没写的内容一律不得脑补加分。
- 覆盖多少要点给多少分：完整讲清机制→高分；只有名词堆砌、没有机制展开→低分；与要点矛盾、纯套话或空白→0~1 分。
- reasoning 必须引用作答原句（可截取），说明「哪些要点覆盖了、哪些没覆盖」；不写空话。
- hit 列出答对的要点名（对齐 rubric 要点标题），missing 列出缺失或答错的要点名。

输出严格 JSON，字段缺一不可：
{"points": <0~max 的数字, 可含 1 位小数>, "reasoning": "<2-4 句中文评分理由>", "hit": ["<答对的要点>", ...], "missing": ["<缺失或答错的要点>", ...]}
注意: 不需要输出 verdict 字段, 系统会按 points/满分比例确定性判定。"""


def _strip_html(text: str, limit: int = 600) -> str:
    """去 HTML 标签 + 压缩空白 + 截断, 用于参考答案摘要。"""
    if not text:
        return ""
    t = _re.sub(r"<[^>]+>", " ", text)
    t = _html.unescape(t)
    t = _re.sub(r"\s+", " ", t).strip()
    return t[:limit]


async def judge_open_answer(
    adapter,
    *,
    title: str,
    skill_name: str | None,
    rubric: dict[str, Any],
    text: str | None,
) -> dict[str, Any] | None:
    """评分一道开放题。

    返回 {points, verdict, reasoning, hit, missing, max_points}；任何失败返回 None。
    """
    max_points = float((rubric or {}).get("max_points", 5.0) or 5.0)
    points_meta = (rubric or {}).get("points") or []
    kp_lines = []
    for i, p in enumerate(points_meta, 1):
        if isinstance(p, dict):
            d = str(p.get("detail") or "").strip()
            kp_lines.append(f"{i}. {p.get('title', '')}" + (f" —— {d[:120]}" if d else ""))
        else:
            kp_lines.append(f"{i}. {str(p)}")
    kp_text = "\n".join(kp_lines) if kp_lines else "（rubric 未给出明确要点，按通用 AI 工程标准评分）"

    answer_text = (text or "").strip()[:2000]
    if not answer_text:
        return None  # 空作答不该浪费一次 LLM 调用, 交给规则判 WRONG

    user = (
        f"题目：{title}\n"
        f"能力域：{skill_name or '未标注'}\n"
        f"满分：{max_points}\n"
        f"评分要点（rubric）：\n{kp_text}\n"
        f"参考答案摘要：{_strip_html((rubric or {}).get('answer_html') or '', 500) or '（无）'}\n"
        f"候选人作答：\n{answer_text}"
    )

    raw = await adapter.chat(JUDGE_SYSTEM, user, json_mode=True, timeout=90.0)
    if not isinstance(raw, dict):
        return None
    # chat() 返回 {"content": "<json 字符串>"}——需要二次解析
    content = raw.get("content")
    if isinstance(content, str):
        try:
            raw = json.loads(content)
        except ValueError:
            return None
        if not isinstance(raw, dict):
            return None
    elif not isinstance(content, dict):
        return None
    else:
        raw = content

    # ---- 确定性校验与归一化 ----
    try:
        pts = float(raw.get("points"))
    except (TypeError, ValueError):
        return None
    pts = max(0.0, min(max_points, pts))
    pts = round(pts, 1)

    ratio = pts / max_points if max_points else 0.0
    verdict = "CORRECT" if ratio >= 0.8 else ("PARTIAL" if ratio >= 0.4 else "WRONG")

    def _clean_list(v) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip()[:120] for x in v if str(x).strip()][:8]

    reasoning = str(raw.get("reasoning") or "").strip()
    if not reasoning:
        return None  # 没有评分理由的打分不可解释, 视为失败

    return {
        "points": pts,
        "max_points": max_points,
        "verdict": verdict,
        "reasoning": reasoning[:600],
        "hit": _clean_list(raw.get("hit")),
        "missing": _clean_list(raw.get("missing")),
    }
