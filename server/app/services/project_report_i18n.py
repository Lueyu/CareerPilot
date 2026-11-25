"""Localized text authored by the project report pipeline."""
import json
from functools import lru_cache
from pathlib import Path

from app.i18n import Locale


@lru_cache(maxsize=2)
def _catalog(language: Locale) -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / 'locales' / f'{language}.project-report.json'
    return json.loads(path.read_text(encoding='utf-8'))


def report_message(key: str, language: Locale, **values: object) -> str:
    return _catalog(language)[key].format(**values)
