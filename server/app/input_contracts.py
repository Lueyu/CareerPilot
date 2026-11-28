"""Boundary types for JSON requests; internal services keep their existing snapshots."""
import math
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, model_validator


class InputModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def bounded_json(value):
    """Check before recursive JsonValue validation, without changing the supplied JSON."""
    pending = [(value, 0)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if depth > 8 or nodes > 1024:
            raise ValueError('JSON exceeds depth 8 or 1024 nodes')
        if isinstance(current, dict):
            if len(current) > 128:
                raise ValueError('JSON object exceeds 128 fields')
            for key, child in current.items():
                if not isinstance(key, str) or len(key) > 128:
                    raise ValueError('JSON keys must be strings of at most 128 characters')
                pending.append((child, depth + 1))
        elif isinstance(current, list):
            if len(current) > 128:
                raise ValueError('JSON array exceeds 128 items')
            pending.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            if len(current) > 8192:
                raise ValueError('JSON string exceeds 8192 characters')
        elif isinstance(current, float) and not math.isfinite(current):
            raise ValueError('JSON numbers must be finite')
    return value


BoundedObject = Annotated[dict[str, JsonValue], BeforeValidator(bounded_json)]
PositiveId = Annotated[int, Field(gt=0, le=2**31 - 1, strict=True)]


def item_key(value):
    # JSON object keys are strings, even when the domain uses integer IDs.
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 10 and not value.startswith('0'):
        value = int(value)
    if type(value) is not int or not 0 < value <= 2**31 - 1:
        raise ValueError('item key must be a positive integer ID')
    return value


ItemKey = Annotated[PositiveId, BeforeValidator(item_key)]
ShortText = Annotated[str, Field(max_length=2000)]
Label = Annotated[str, Field(max_length=128)]


class DraftEdit(InputModel):
    title: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    skill_key: Annotated[str, Field(max_length=64)] | None = None
    answer_html: Annotated[str, Field(max_length=60000)] | None = None
    rubric: BoundedObject | None = None
    short_code: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    qtype: Literal['open', 'mcq', 'coding', 'behavioral'] | None = None
    topic: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    difficulty: Annotated[int, Field(ge=1, le=5)] | None = None
    options: Annotated[list[ShortText], Field(max_length=32)] | None = None
    company: Annotated[str, Field(max_length=160)] | None = None
    role: Annotated[str, Field(max_length=120)] | None = None
    level: Annotated[str, Field(max_length=32)] | None = None
    tags: Annotated[list[Label], Field(max_length=32)] | None = None

    @model_validator(mode='after')
    def required_when_present(self):
        for key in ('title', 'short_code', 'qtype', 'topic', 'difficulty'):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f'{key} cannot be null')
        return self


class AssistanceDeclarations(InputModel):
    hint_used: bool = False
    used_hint: bool = False
    reference_viewed: bool = False
    viewed_answer: bool = False
    ai_declared: bool = False
    ai_assisted: bool = False


class ActivityPayload(AssistanceDeclarations):
    text_answer: Annotated[str, Field(min_length=1, max_length=60000)] | None = None
    answer_text: Annotated[str, Field(min_length=1, max_length=60000)] | None = None
    assistance: AssistanceDeclarations | None = None

    @model_validator(mode='after')
    def has_answer(self):
        text = self.text_answer if 'text_answer' in self.model_fields_set else self.answer_text
        if text is None or not text.strip():
            raise ValueError('a nonempty text_answer or answer_text is required')
        return self
