"""Published teaching supplements. The question and scoring snapshot remain independently versioned."""
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models import Question
from app.models.question import SOURCE_PUBLIC
from app.services.experienced_bank import Text, is_owned_question, original_identities, load_catalog


class Comparison(BaseModel):
    headers: list[Text] = Field(min_length=2)
    rows: list[list[Text]] = Field(min_length=2)

    @model_validator(mode="after")
    def check_width(self):
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("All comparison rows must match header width")
        return self


class ProjectExample(BaseModel):
    name: Literal["AIDR", "Squabble", "CareerPilot"]
    anchor: Text
    application: Text


class SelfCheck(BaseModel):
    question: Text
    answer: Text


class AnswerGuide(BaseModel):
    plain: Text
    analogy: Text
    steps: list[Text] = Field(min_length=3)
    flow: list[Text] = Field(min_length=3)
    table: Comparison
    example: Text
    project: ProjectExample
    pitch: Text
    check: SelfCheck


@lru_cache(maxsize=1)
def published_guides() -> dict[str, AnswerGuide]:
    result = {}
    directory = Path(__file__).resolve().parents[1] / "data" / "answer_guides"
    for path in sorted(directory.glob("*.json")):
        for code, data in json.loads(path.read_text(encoding="utf-8-sig")).items():
            if code in result:
                raise ValueError(f"Duplicate answer guide for {code}")
            result[code] = AnswerGuide.model_validate(data)
    return result


@lru_cache(maxsize=1)
def _authored_titles() -> dict[str, tuple[str, str]]:
    return {q.code: (d.key, q.title) for d in load_catalog() for q in d.questions}


def for_question(question: Question) -> AnswerGuide | None:
    if (question.workspace_id is not None or question.is_demo or question.canonical_question_id is not None
            or question.source_type != SOURCE_PUBLIC or question.qtype != "open"):
        return None
    guide = published_guides().get(question.short_code)
    if guide is None:
        return None
    if is_owned_question(question):
        identity = (question.generation_meta["domain_key"], question.title)
        return guide if _authored_titles().get(question.short_code) == identity else None
    if not question.generation_meta and original_identities().get(question.short_code) == (question.title, question.topic):
        return guide
    return None
