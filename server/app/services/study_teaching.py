"""Select one teaching strategy for a learner-facing generation."""
from app.i18n import Locale, message

STRATEGY_VERSION = "careerpilot-teaching-v2"


def teaching_prompt(action: str, language: Locale | None = None) -> str:
    if action in {"interview", "interview_question", "notes"}:
        strategy = message(f"teaching.{action}", language)
    else:
        strategy = message("teaching.base", language)
        if action != "explain":
            strategy += " " + message(f"teaching.{action}", language)
    return strategy + "\n" + message("language", language)


def practice_prompt(kind: str, question: str, *, from_card: bool) -> str:
    if kind != "explain" and from_card:
        return question
    key = "practice.explain" if kind == "explain" else "practice.example"
    return message(key).format(question=question)
