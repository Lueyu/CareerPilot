"""Shared text-only parsing for interview imports."""

import json
import re
from typing import Any


def read_json(content: str) -> Any:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|```$", "", text, flags=re.MULTILINE).strip()
    data: Any
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 容错: 截取第一个 [ 或 { 到最后一个 ] 或 }
        start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=-1)
        end = max(text.rfind("]"), text.rfind("}"))
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data


QA_HEAD = re.compile(r"^\s*(?:#{1,6}\s+)?(?:问|问题|[Qq])\s*[:：]\s*(.+)$")
NUMBERED_HEAD = re.compile(r"^\s*(?:#{1,6}\s+)?[0-9]+[.、)]\s*(\S.+)$")
ANSWER_HEAD = re.compile(r"^\s*(?:答案|答|[Aa])\s*[:：]\s*")
MARKDOWN_QUESTION = re.compile(r"^\s*#{1,6}\s+(.+[？?])\s*$")


def extract_structured_questions(text: str, *, include_numbered_answers: bool = False) -> list[dict]:
    """按原文顺序提取问答和编号题。"""
    items: list[dict] = []
    title = ""
    answer: list[str] = []
    explicit = False
    has_qa = False

    def append():
        if len(title) < (2 if include_numbered_answers else 6 if explicit else 1):
            return
        items.append(
            {
                "title": title[:200],
                "answer": "\n".join(answer).strip(),
                "confidence": 0.7 if explicit else (0.55 if title.endswith(("?", "？")) else 0.4),
            }
        )

    for line in text.splitlines():
        qa = QA_HEAD.match(line)
        numbered = NUMBERED_HEAD.match(line)
        heading = MARKDOWN_QUESTION.match(line) if include_numbered_answers else None
        next_title = numbered.group(1).strip() if numbered and 6 <= len(line.strip()) <= 120 else ""
        numbered_question = next_title and (
            not title
            or (not explicit and not include_numbered_answers)
            or next_title.endswith(("?", "？"))
            or (next_title.startswith(("什么", "如何", "怎么", "为什么")) and not next_title.endswith(("。", ".")))
        )
        if qa or heading or numbered_question:
            append()
            explicit = bool(qa or heading)
            title = (qa or heading).group(1).strip() if explicit else next_title
            has_qa = has_qa or (explicit and len(title) >= 6)
            answer = []
        elif title and (explicit or include_numbered_answers):
            answer.append(ANSWER_HEAD.sub("", line, count=1))

    append()
    return items if has_qa or include_numbered_answers else []
