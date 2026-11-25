"""Build papers for the conversation-first exam workspace."""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Text, cast, func, or_, select

from app.i18n import request_language
from app.models import Exam, ExamQuestion, ImportSession, Question, Skill
from app.services.assessment_versions import question_snapshot
from app.services.bank_practice import bank_list_question_clause, is_private_library_question
from app.services.experienced_bank import owned_question_clause
from app.services.learning_exposure import exposed_families

CODE_TAGS = frozenset({"code", "coding", "code-writing", "代码", "代码题", "编程", "编程题"})


def answer_format(question: Question) -> str:
    if question.qtype == "open" and any(
        isinstance(tag, str) and tag.strip().casefold() in CODE_TAGS for tag in (question.tags or [])
    ):
        return "code"
    return question.qtype


def localized(zh: str, en: str) -> str:
    return en if request_language() == "en" else zh


class InterviewExamConfig(BaseModel):
    mode: Literal["test", "practice"]
    source: Literal["bank", "catalog", "library", "interview_set"]
    origin_session_id: int | None = Field(default=None, ge=1)
    question_types: list[Literal["mcq", "open", "code"]] = Field(min_length=1, max_length=3)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    topic: str = Field(default="", max_length=120)
    interview_set_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def check_source(self):
        if self.mode == "test" and self.source != "bank":
            raise ValueError("Only assessment-ready bank questions can be used in a test")
        if (self.source == "interview_set") != (self.interview_set_id is not None):
            raise ValueError("Select one interview set when using that source")
        return self


async def select_interview_paper(session, workspace_id, config: InterviewExamConfig, count: int):
    project_name = None
    if config.source == "interview_set":
        imports = await session.scalars(select(ImportSession).where(
            ImportSession.workspace_id == workspace_id,
            ImportSession.meta["interview_sets"].contains([{"id": config.interview_set_id}]),
        ))
        entry = next((row for imported in imports for row in imported.meta.get("interview_sets", [])
                      if row["id"] == config.interview_set_id), None)
        if entry is None:
            raise HTTPException(404, localized("找不到所选面经套题。", "The selected interview set is unavailable."))
        project_name = entry.get("project_name")
        source = (Question.workspace_id == workspace_id) & (
            Question.generation_meta["interview_set_id"].as_string() == config.interview_set_id
        ) & Question.id.in_([row["id"] for row in entry["questions"]])
    elif config.source == "catalog":
        source = owned_question_clause()
    elif config.source == "library":
        source = bank_list_question_clause(workspace_id, mine=True)
    else:
        source = bank_list_question_clause(workspace_id) & (
            Question.generation_meta["assessment_use"].as_string().is_distinct_from("PRACTICE_ONLY")
        ) & Question.skill_id.is_not(None)

    stored_types = {"open" if kind == "code" else kind for kind in config.question_types}
    query = select(Question).where(source, Question.qtype.in_(stored_types))
    if config.difficulty is not None:
        query = query.where(Question.difficulty == config.difficulty)
    topic = config.topic.strip()
    if topic:
        query = query.where(or_(
            Question.title.icontains(topic, autoescape=True),
            Question.topic.icontains(topic, autoescape=True),
            cast(Question.keywords, Text).icontains(topic, autoescape=True),
            cast(Question.tags, Text).icontains(topic, autoescape=True),
            Question.skill.has(Skill.key == topic.lower()),
            Question.skill.has(Skill.name.icontains(topic, autoescape=True)),
        ))

    excluded = set()
    if config.mode == "test":
        excluded = await exposed_families(session, workspace_id)
        prior = await session.scalars(select(func.coalesce(
            ExamQuestion.creation_snapshot["canonical_question_id"].as_integer(),
            Question.canonical_question_id, Question.id,
        )).select_from(ExamQuestion).join(Question, ExamQuestion.question_id == Question.id)
            .join(Exam, Exam.id == ExamQuestion.exam_id)
            .where(Exam.workspace_id == workspace_id))
        excluded.update(prior)
    chosen = []
    families = set()
    for question in await session.scalars(query.order_by(Question.id)):
        format = answer_format(question)
        if format not in config.question_types or (format == "code" and not question.rubric):
            continue
        family = question.canonical_question_id or question.id
        if family in families or family in excluded or question.id in excluded or question.is_study_chat_review:
            continue
        try:
            question_snapshot(question, private_library_practice=is_private_library_question(question, workspace_id))
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            continue
        chosen.append(question)
        families.add(family)
        if len(chosen) == count:
            return chosen, project_name
    raise HTTPException(409, localized(
        f"符合条件且具备评分标准的题目有 {len(chosen)} 道，本次需要 {count} 道。请调整组卷条件。",
        f"{len(chosen)} scorable questions match these settings. This paper needs {count}. Adjust the configuration.",
    ))
