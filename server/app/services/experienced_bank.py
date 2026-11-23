"""Install and read the shared experienced-interview practice pack."""
import json
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, raiseload

from app.models import Question, Skill
from app.models.question import SOURCE_PUBLIC

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "experienced_bank"
LEGACY_DOMAINS_PATH = CATALOG_PATH.parent / "legacy_domains.json"
PACK = "experienced_bank"
ORIGIN = "AUTHORED_ORIGINAL"
PRACTICE_ONLY = "PRACTICE_ONLY"

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ShortCode = Annotated[Text, Field(max_length=32)]
Criterion = Annotated[Text, Field(max_length=1750)]


class Followup(BaseModel):
    question: Text
    answer: Text


class AuthoredQuestion(BaseModel):
    code: ShortCode
    level: Literal["mid", "senior"]
    title: Text
    skill: Text
    tags: list[Text]
    answer: Text
    followups: list[Followup] = Field(min_length=2, max_length=2)
    criteria: list[Criterion] = Field(min_length=3, max_length=3)


class Domain(BaseModel):
    key: Text
    name: Annotated[Text, Field(max_length=64)]
    order: int
    focus: Text
    questions: list[AuthoredQuestion]


def load_catalog() -> list[Domain]:
    domains = []
    keys = set()
    codes = set()
    for path in sorted(CATALOG_PATH.glob("*.json")):
        try:
            domain = Domain.model_validate_json(path.read_text(encoding="utf-8-sig"))
        except ValueError as exc:
            raise ValueError(f"Invalid experienced bank file {path.name}: {exc}") from exc
        if domain.key in keys:
            raise ValueError(f"Duplicate experienced bank domain {domain.key} in {path.name}")
        keys.add(domain.key)
        for question in domain.questions:
            if question.code in codes:
                raise ValueError(f"Duplicate experienced bank short code {question.code} in {path.name}")
            codes.add(question.code)
            if len(set(question.criteria)) != len(question.criteria):
                raise ValueError(f"Duplicate scoring criteria for {question.code} in {path.name}")
        domains.append(domain)
    return sorted(domains, key=lambda domain: domain.order)


def owned_question_clause():
    meta = Question.generation_meta
    return (
        Question.workspace_id.is_(None)
        & Question.canonical_question_id.is_(None)
        & Question.is_demo.is_(False)
        & (Question.source_type == SOURCE_PUBLIC)
        & (meta["pack"].as_string() == PACK)
        & (meta["origin"].as_string() == ORIGIN)
        & (meta["assessment_use"].as_string() == PRACTICE_ONLY)
        & (meta["domain_key"].as_string() != "")
    )


def is_owned_question(question: Question) -> bool:
    meta = question.generation_meta or {}
    return (
        question.workspace_id is None and question.canonical_question_id is None
        and not question.is_demo and question.source_type == SOURCE_PUBLIC
        and meta.get("pack") == PACK and meta.get("origin") == ORIGIN
        and meta.get("assessment_use") == PRACTICE_ONLY and bool(meta.get("domain_key"))
    )


def _answer_html(question: AuthoredQuestion) -> str:
    def paragraph(text: str) -> str:
        return "<p>" + escape(text).replace("\n", "<br>") + "</p>"

    followups = "".join(
        f"<li>{paragraph(item.question)}{paragraph(item.answer)}</li>" for item in question.followups
    )
    criteria = "".join(f"<li>{escape(item)}</li>" for item in question.criteria)
    return (
        f"<h3>参考答案</h3>{paragraph(question.answer)}"
        f"<h3>追问与参考回答</h3><ol>{followups}</ol>"
        f"<h3>评分要点</h3><ol>{criteria}</ol>"
    )


async def install_pack(session: AsyncSession, skills: dict[str, Skill]) -> tuple[int, int, list[str]]:
    """Return created count, already installed count, and conflicting short codes."""
    domains = load_catalog()
    rows = []
    domains_by_code = {}
    for domain in domains:
        for question in domain.questions:
            skill = skills.get(question.skill)
            if skill is None:
                raise ValueError(f"Unknown skill {question.skill} for experienced bank question {question.code}")
            domains_by_code[question.code] = domain.key
            rows.append({
                "short_code": question.code,
                "workspace_id": None,
                "title": question.title,
                "qtype": "open",
                "topic": domain.name,
                "skill_id": skill.id,
                "difficulty": 3 if question.level == "mid" else 4,
                "answer_html": _answer_html(question),
                "rubric": {"max_points": 5.0, "points": [
                    {"title": criterion if len(criterion) <= 200 else f"{criterion[:190]}…（{index}）",
                     "detail": criterion}
                    for index, criterion in enumerate(question.criteria, 1)
                ]},
                "level": question.level,
                "source_type": SOURCE_PUBLIC,
                "tags": question.tags,
                "generation_meta": {
                    "pack": PACK, "domain_key": domain.key, "origin": ORIGIN,
                    "assessment_use": PRACTICE_ONLY,
                },
                "is_demo": False,
            })
    if not rows:
        return 0, 0, []

    # Stable conflict order for workers seeding the same codes in separate transactions.
    rows.sort(key=lambda row: row["short_code"])
    created = set(await session.scalars(
        insert(Question).values(rows)
        .on_conflict_do_nothing(index_elements=[Question.short_code])
        .returning(Question.short_code)
    ))
    remaining = domains_by_code.keys() - created
    if not remaining:
        return len(created), 0, []

    installed = (await session.execute(
        select(Question.short_code, Question.generation_meta["domain_key"].as_string())
        .where(Question.short_code.in_(remaining), owned_question_clause())
    )).all()
    owned = {code for code, domain_key in installed if domains_by_code[code] == domain_key}
    return len(created), len(owned), sorted(remaining - owned)


def _legacy_domains() -> dict[str, dict[str, list[str]]]:
    if not LEGACY_DOMAINS_PATH.is_file():
        return {}
    return json.loads(LEGACY_DOMAINS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _original_identities(path: Path, version: tuple[int, int, int, int]) -> dict[str, tuple[str, str]]:
    return {
        entry["id"]: (entry["title"], entry["topic"])
        for entry in json.loads(path.read_text(encoding="utf-8"))["questions"]
    }


def original_identities() -> dict[str, tuple[str, str]]:
    from app.seed import BANK_PATH

    try:
        stat = BANK_PATH.stat()
    except FileNotFoundError:
        return {}
    return _original_identities(BANK_PATH, (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))


def catalog_questions(
    domains: list[Domain], rows: list[Question],
    legacy_domains: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, list[dict]]:
    by_code = {row.short_code: row for row in rows if (
        row.workspace_id is None and row.canonical_question_id is None
        and not row.is_demo and row.source_type == SOURCE_PUBLIC
    )}
    if legacy_domains is None:
        legacy_domains = _legacy_domains()
    originals = original_identities() if legacy_domains else {}

    questions_by_domain: dict[str, list[dict]] = {}
    seen: set[int] = set()
    for domain in domains:
        questions = questions_by_domain[domain.key] = []
        for entry in domain.questions:
            row = by_code.get(entry.code)
            if (row is not None and is_owned_question(row)
                    and row.generation_meta["domain_key"] == domain.key):
                questions.append(_catalog_entry(row, entry.level, "authored"))
                seen.add(row.id)

    for domain in domains:
        for level in ("mid", "senior"):
            for code in legacy_domains.get(domain.key, {}).get(level, []):
                row = by_code.get(code)
                original = originals.get(code)
                if (row is None or original is None or row.id in seen or row.qtype != "open"
                        or row.generation_meta or (row.title, row.topic) != original):
                    continue
                questions_by_domain[domain.key].append(_catalog_entry(row, level, "legacy"))
                seen.add(row.id)
    return questions_by_domain


def _catalog_entry(row: Question, level: Literal["mid", "senior"], source: Literal["authored", "legacy"]) -> dict:
    return {
        "id": row.id, "short_code": row.short_code, "title": row.title,
        "level": level, "tags": row.tags or [], "source": source, "company": row.company,
    }


async def list_installed_pack(session: AsyncSession) -> dict:
    domains = load_catalog()
    legacy_domains = _legacy_domains()
    codes = {entry.code for domain in domains for entry in domain.questions}
    codes.update(
        code for domain in domains for level in ("mid", "senior")
        for code in legacy_domains.get(domain.key, {}).get(level, [])
    )
    rows = (await session.scalars(select(Question).options(
        load_only(
            Question.id, Question.short_code, Question.title, Question.tags, Question.topic,
            Question.company, Question.qtype, Question.generation_meta, Question.workspace_id,
            Question.canonical_question_id, Question.is_demo, Question.source_type,
            raiseload=True,
        ),
        raiseload("*"),
    ).where(
        Question.short_code.in_(codes),
        Question.workspace_id.is_(None), Question.canonical_question_id.is_(None),
        Question.is_demo.is_(False), Question.source_type == SOURCE_PUBLIC,
    ))).all()
    questions_by_domain = catalog_questions(domains, rows, legacy_domains)
    return {"domains": [
        {
            "key": domain.key, "name": domain.name, "order": domain.order,
            "focus": domain.focus, "questions": questions_by_domain[domain.key],
        }
        for domain in domains if questions_by_domain[domain.key]
    ]}
