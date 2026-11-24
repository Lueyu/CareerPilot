"""Turn a saved study conversation into workspace-owned review material."""
import json
import re
from html import escape
from typing import Annotated
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.i18n import message as localized_message
from app.i18n import request_language
from app.models import Question, Workspace
from app.models.question import SOURCE_GENERATED, STUDY_CHAT_REVIEW_META
from app.models.study_chat import StudyInterviewQuestion, StudyMessage, StudySession
from app.services.assessment_versions import digest
from app.services.llm import get_llm_config
from app.services.study_notes import GenerationError, read_json
from app.services.study_routing import profile
from app.services.study_teaching import teaching_prompt

MAX_SNAPSHOT_BYTES = 128_000
PROMPT_FRAMING_TOKENS = 64
_ASCII_WORD = re.compile(r" ?[a-zA-Z]+")

Title = Annotated[str, StringConstraints(min_length=1, max_length=240, pattern=r"^[^\x00]+$")]
Paragraph = Annotated[str, StringConstraints(min_length=1, max_length=1800, pattern=r"^[^\x00]+$")]
Cell = Annotated[str, StringConstraints(min_length=1, max_length=320, pattern=r"^[^\x00]+$")]


class GuideInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class FlowStep(GuideInput):
    step: Cell
    relation: Cell


class Followup(GuideInput):
    question: Cell
    answer: Paragraph


class ProjectQuote(GuideInput):
    message_id: int = Field(gt=0)
    quote: Paragraph


class GuideContent(GuideInput):
    title: Title
    plain: Paragraph
    spoken: Paragraph
    analogy: Paragraph
    steps: list[FlowStep] = Field(min_length=2, max_length=8)
    comparison: list[tuple[Cell, Cell, Cell]] = Field(min_length=2, max_length=6)
    example: Paragraph
    followups: list[Followup] = Field(min_length=1, max_length=5)


class Guide(GuideContent):
    project_context: list[ProjectQuote] = Field(default_factory=list, max_length=5)


class FreeStudyGuide(GuideContent):
    project_context: tuple[()] = ()


class InterviewQuestionResult(BaseModel):
    id: int
    short_code: str
    title: str
    answer_html: str


def estimate_guide_prompt_tokens(text: str, utf8_bytes: int) -> int:
    """Estimate ASCII word tokens and charge all other UTF-8 bytes in full."""
    for word in _ASCII_WORD.findall(text):
        # A leading space often shares the token. Estimate one token per four letters.
        utf8_bytes -= len(word) - (len(word.lstrip(" ")) + 3) // 4
    return utf8_bytes


def render_guide(guide: Guide | FreeStudyGuide, turns: list[dict]) -> str:
    # The model supplies plain text only. Markup, including the table structure, is server-owned.
    def section(label, text):
        return f"<h3>{label}</h3><p>{escape(text)}</p>"

    steps = "".join(
        f'<li><strong class="answer-flow-step">{escape(item.step)}</strong>'
        f'<p class="answer-flow-relation">{escape(item.relation)}</p></li>'
        for item in guide.steps
    )
    rows = "".join("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
                   for row in guide.comparison)
    followups = "".join(f"<details><summary>{escape(item.question)}</summary><p>{escape(item.answer)}</p></details>"
                        for item in guide.followups)
    sections = [
        f"<p><strong>{localized_message('guide.unverified')}</strong></p>",
        section(localized_message("guide.question"), guide.title),
        section(localized_message("guide.plain"), guide.plain),
        section(localized_message("guide.spoken"), guide.spoken),
        section(localized_message("guide.analogy"), guide.analogy),
        f'<h3>{localized_message("guide.steps")}</h3><ol class="answer-flow">{steps}</ol>',
        f"<h3>{localized_message('guide.comparison')}</h3><table><thead><tr><th>{localized_message('guide.scenario')}</th><th>{localized_message('guide.mechanism')}</th><th>{localized_message('guide.limit')}</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>",
        section(localized_message("guide.example"), guide.example),
        f"<h3>{localized_message('guide.followups')}</h3>" + followups,
    ]
    if guide.project_context:
        messages = {
            turn[role]["id"]: (label, turn["project_id"], turn[role]["text"])
            for turn in turns if turn["project_id"] is not None
            for role, label in (("user", localized_message("guide.learner")), ("assistant", localized_message("guide.assistant")))
        }
        sections.append(f"<h3>{localized_message('guide.sources')}</h3><p>{localized_message('guide.attribution')}</p>")
        for reference in guide.project_context:
            quoted_turn = messages.get(reference.message_id)
            if quoted_turn is None or reference.quote not in quoted_turn[2]:
                raise GenerationError(localized_message("errors.the_project_citation_does_not_belong_to_this_conversation"))
            label, project_id, _ = quoted_turn
            sections.append(f"<p>{localized_message('guide.citation').format(project_id=project_id, speaker=label, message_id=reference.message_id)}</p>"
                            f"<blockquote>{escape(reference.quote)}</blockquote>")
    return "".join(sections)


async def complete_turns(db, session_id):
    reply = aliased(StudyMessage)
    rows = await db.execute(select(StudyMessage, reply).join(reply, and_(
        reply.session_id == StudyMessage.session_id,
        reply.request_id == StudyMessage.request_id,
        reply.project_id.is_not_distinct_from(StudyMessage.project_id),
        reply.id > StudyMessage.id,
        reply.role == "assistant",
        reply.status == "completed",
    )).where(StudyMessage.session_id == session_id, StudyMessage.role == "user",
             StudyMessage.status == "completed").order_by(StudyMessage.id))
    turns = []
    latest = None
    for user, assistant in rows:
        if not user.content.strip() or not assistant.content.strip():
            continue
        metadata = user.coach or {}
        lineage = {key: metadata[key] for key in ("parent_message_id", "revision_of") if key in metadata}
        answer = {"id": assistant.id, "text": assistant.content}
        if blocks := (assistant.coach or {}).get("blocks"):
            answer["blocks"] = blocks
        turns.append({"project_id": user.project_id, "user": {"id": user.id, "text": user.content, **lineage},
                      "assistant": answer})
        latest = user, assistant
    return turns, latest


def organizer_profile(base, user, assistant, session_mode) -> tuple[dict, int] | None:
    settings = get_settings()
    coach = assistant.coach or {}
    trace = coach.get("trace") or {}
    mode = coach.get("mode") or (user.coach or {}).get("mode") or trace.get("mode")
    tier = mode if mode in ("flash", "strong") else trace.get("tier")
    if tier not in ("flash", "strong") and mode is None:
        tier = session_mode

    def usable(selected):
        cfg = profile(base, selected, settings)
        if not cfg or not all(cfg.get(key) for key in ("model", "provider", "base_url", "api_key")):
            return None
        output_limit = min(settings.study_output_budget, getattr(settings, f"study_{selected}_output_tokens"))
        if cfg.get("output_limit") is not None:
            output_limit = min(output_limit, cfg["output_limit"])
        return {**cfg, "output_limit": output_limit}, getattr(settings, f"study_{selected}_context")

    if tier in ("flash", "strong"):
        return usable(tier)
    # Older turns can have a model name but no routing trace.
    flash_model = settings.study_flash_model or base.get("model")
    if assistant.model and assistant.model == settings.study_strong_model and assistant.model != flash_model:
        return usable("strong")
    return usable("flash") or usable("strong")


async def saved_result(db, workspace_id, session_id, snapshot_hash=None):
    stmt = select(Question.id, Question.short_code, Question.title, Question.answer_html,
                  StudyInterviewQuestion.last_message_id).join(
        StudyInterviewQuestion, StudyInterviewQuestion.question_id == Question.id,
    ).where(StudyInterviewQuestion.session_id == session_id, Question.workspace_id == workspace_id)
    if snapshot_hash is not None:
        stmt = stmt.where(StudyInterviewQuestion.snapshot_hash == snapshot_hash)
    else:
        stmt = stmt.order_by(StudyInterviewQuestion.last_message_id.desc().nulls_last(), Question.id.desc())
    result = (await db.execute(stmt.limit(1))).mappings().first()
    if result is None:
        return None
    if snapshot_hash is None and result["last_message_id"] is None:
        # Migration 019 may already have been applied before progress was tracked.
        turns, _ = await complete_turns(db, session_id)
        if turns:
            exact = await saved_result(db, workspace_id, session_id, digest({"turns": turns}))
            if exact is not None:
                return exact
    return InterviewQuestionResult(**result)


async def generate_question(db, workspace_id, session_id, *, session_mode):
    turns, latest = await complete_turns(db, session_id)
    if not turns:
        raise HTTPException(409, localized_message("errors.this_study_session_has_no_complete_question_and_reply"))
    payload = {"turns": turns}
    if request_language() != "zh-CN":
        payload["response_language"] = request_language()
    snapshot_hash = digest(payload)
    saved = await saved_result(db, workspace_id, session_id, snapshot_hash)
    if saved:
        return saved
    # Match read_json's transcript serialization.
    transcript = json.dumps(payload, ensure_ascii=False)
    transcript_bytes = len(transcript.encode("utf-8"))
    if transcript_bytes > MAX_SNAPSHOT_BYTES:
        raise HTTPException(422, localized_message("errors.the_conversation_is_too_long_to_organize_in_one"))

    # Resolve the model outside the transaction that will hold the advisory lock.
    resolved = organizer_profile(await get_llm_config(), *latest, session_mode)
    await db.commit()
    lock_hash = digest(["study-interview-question", session_id, snapshot_hash])
    lock_key = int.from_bytes(bytes.fromhex(lock_hash[:16]), byteorder="big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    saved = await saved_result(db, workspace_id, session_id, snapshot_hash)
    if saved:
        await db.commit()
        return saved
    if resolved is None:
        raise HTTPException(502, localized_message("errors.no_model_is_configured_for_this_study_tier_check"))
    cfg, context = resolved
    output_limit = cfg["output_limit"]
    if output_limit <= 0:
        raise HTTPException(502, localized_message("guide.no_output_budget"))

    has_project = any(turn["project_id"] is not None for turn in turns)
    schema = Guide if has_project else FreeStudyGuide
    instruction = "\n".join((
        teaching_prompt("interview_question"),
        localized_message("guide.project_quotes" if has_project else "guide.no_project_quotes"),
        localized_message("guide.output_instruction"),
        json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":")),
    ))
    prompt_bytes = len(instruction.encode("utf-8")) + transcript_bytes
    context_remaining = context - prompt_bytes - PROMPT_FRAMING_TOKENS
    if context_remaining < output_limit:
        # A byte count overstates token count for prose. The provider still enforces its actual limit.
        estimated_tokens = estimate_guide_prompt_tokens(instruction + transcript, prompt_bytes)
        context_remaining = context - estimated_tokens - PROMPT_FRAMING_TOKENS
    if context_remaining <= 0:
        raise HTTPException(422, localized_message("guide.insufficient_context"))
    try:
        guide, _ = await read_json(instruction, payload, schema, config={
            **cfg, "output_limit": min(output_limit, context_remaining),
        })
        answer_html = render_guide(guide, turns)
    except GenerationError:
        raise HTTPException(502, localized_message("errors.could_not_generate_the_interview_question_try_again")) from None

    # Match chat's lock order and recheck visibility after the model returns.
    await db.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    visible_session = await db.scalar(select(StudySession.id).where(
        StudySession.id == session_id, StudySession.workspace_id == workspace_id,
        StudySession.hidden_at.is_(None),
    ).with_for_update())
    if visible_session is None:
        raise HTTPException(404, localized_message("errors.study_session_not_found"))

    question = Question(workspace_id=workspace_id, short_code="sc-" + uuid4().hex[:29],
                        title=guide.title, topic="study", source_type=SOURCE_GENERATED,
                        answer_html=answer_html, generation_meta={**STUDY_CHAT_REVIEW_META, "study_session_id": session_id})
    db.add(question)
    await db.flush()
    db.add(StudyInterviewQuestion(question_id=question.id, session_id=session_id, snapshot_hash=snapshot_hash,
                                   last_message_id=turns[-1]["assistant"]["id"]))
    await db.commit()
    return InterviewQuestionResult(id=question.id, short_code=question.short_code,
                                   title=question.title, answer_html=question.answer_html)
