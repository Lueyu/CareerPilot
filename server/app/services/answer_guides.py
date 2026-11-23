"""Published teaching supplements. The question and scoring snapshot remain independently versioned."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import lazyload

from app.models import Question
from app.models.question import SOURCE_PUBLIC
from app.services.experienced_bank import Text, is_owned_question, original_identities, load_catalog, render_answer_html


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


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PREVIOUS_REFERENCE_DIGESTS = {
    "rag-001": "8f972d18ee3e7a8dc6ee33fc936d7f19cd089fc853475553fccbec2a75e6c0e6",
    "sys-001": "cc9c246257f4adafc5936d8169810866b0d8b8214046dc1b886be3deaff16b2e",
    "xp-context-02": "f934d04bc7bda4b63db31e5cf655a633c2a6ade31e6f81bbeba4b311c4cf3fbd",
    "xp-engineering-04": "3605e824270f6138e5f366e56f80ca9c2da8e11f0fd9eb9a74e7c14ef69c595a",
    "xp-production-03": "54cdc9a3e139bc22f89c20ebf2b070eea48a524e7748812b9b2b1fbe0477e77f",
    "xp-rag-03": "298bd926aee1143f44885e351f3346a940f04bdaf78dcb9a1e5ae6c957414629",
}


@lru_cache(maxsize=1)
def published_guides() -> dict[str, AnswerGuide]:
    result = {}
    directory = DATA_DIR / "answer_guides"
    for path in sorted(directory.glob("*.json")):
        for code, data in json.loads(path.read_text(encoding="utf-8-sig")).items():
            if code in result:
                raise ValueError(f"Duplicate answer guide for {code}")
            result[code] = AnswerGuide.model_validate(data)
    return result


@lru_cache(maxsize=1)
def _authored_titles() -> dict[str, tuple[str, str]]:
    return {q.code: (d.key, q.title) for d in load_catalog() for q in d.questions}


def _public_original(question: Question) -> bool:
    return (question.workspace_id is None and not question.is_demo and question.canonical_question_id is None
            and question.source_type == SOURCE_PUBLIC and question.qtype == "open" and not question.generation_meta
            and original_identities().get(question.short_code) == (question.title, question.topic))


def for_question(question: Question) -> AnswerGuide | None:
    guide = published_guides().get(question.short_code)
    if guide is None:
        return None
    if is_owned_question(question) and question.qtype == "open":
        identity = (question.generation_meta["domain_key"], question.title)
        return guide if _authored_titles().get(question.short_code) == identity else None
    return guide if _public_original(question) else None


@lru_cache(maxsize=1)
def beginner_examples() -> dict[str, str]:
    examples = {}
    for path in sorted((DATA_DIR / "beginner_examples").glob("*.json")):
        for code, example in json.loads(path.read_text(encoding="utf-8-sig")).items():
            if code in examples or not isinstance(example, str) or not example.strip():
                raise ValueError(f"Invalid beginner example for {code}")
            examples[code] = example
    return examples


def example_for(question: Question) -> str | None:
    return beginner_examples().get(question.short_code) if _public_original(question) else None


@lru_cache(maxsize=1)
def _corrected_references() -> dict[str, str]:
    from app.seed import BANK_PATH

    questions = json.loads(BANK_PATH.read_text(encoding="utf-8"))["questions"]
    html = {q["id"]: q["html"] for q in questions if q["id"] in PREVIOUS_REFERENCE_DIGESTS}
    html.update({q.code: render_answer_html(q) for domain in load_catalog() for q in domain.questions
                 if q.code in PREVIOUS_REFERENCE_DIGESTS})
    return html


def current_reference(question: Question) -> str | None:
    previous = PREVIOUS_REFERENCE_DIGESTS.get(question.short_code)
    html = question.answer_html
    if previous and html and hashlib.sha256(html.encode()).hexdigest() == previous and for_question(question):
        return _corrected_references().get(question.short_code, html)
    return html


async def update_published_references(session):
    rows = await session.scalars(select(Question).options(lazyload("*")).where(Question.workspace_id.is_(None),
        Question.short_code.in_(PREVIOUS_REFERENCE_DIGESTS)).order_by(Question.short_code).with_for_update())
    for question in rows:
        corrected = current_reference(question)
        if corrected != question.answer_html:
            question.answer_html = corrected
