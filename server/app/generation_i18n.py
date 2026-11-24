"""Language helpers for native generation paths."""

import json
from functools import lru_cache
from pathlib import Path

from app.i18n import Locale, message, request_language


@lru_cache(maxsize=2)
def _catalog(language: Locale) -> dict[str, str]:
    path = Path(__file__).with_name("locales") / f"{language}.generation.json"
    return json.loads(path.read_text(encoding="utf-8"))


def generation_message(key: str, language: Locale | None = None) -> str:
    return _catalog(language or request_language())[key]


def saved_language(value: object) -> Locale:
    return "en" if value == "en" else "zh-CN"


def with_output_language(prompt: str, language: Locale | None = None) -> str:
    return f"{prompt}\n\n{message('language', language)}"
