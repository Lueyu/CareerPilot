"""Turn a saved study conversation into workspace-owned review material."""
from html import escape
import json
from typing import Annotated
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from app.config import get_settings
from app.models import Question, Workspace
from app.models.question import SOURCE_GENERATED, STUDY_CHAT_REVIEW_META
from app.models.study_chat import StudyInterviewQuestion, StudyMessage, StudySession
from app.services.assessment_versions import digest
from app.services.llm import get_llm_config
from app.services.study_notes import GenerationError, read_json
from app.services.study_routing import profile
from app.services.study_teaching import teaching_prompt


MAX_SNAPSHOT_BYTES = 128_000

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


class Guide(GuideInput):
    title: Title
    plain: Paragraph
    spoken: Paragraph
    analogy: Paragraph
    steps: list[FlowStep] = Field(min_length=2, max_length=8)
    comparison: list[tuple[Cell, Cell, Cell]] = Field(min_length=2, max_length=6)
    example: Paragraph
    followups: list[Followup] = Field(min_length=1, max_length=5)
    project_context: list[ProjectQuote] = Field(default_factory=list, max_length=5)


class InterviewQuestionResult(BaseModel):
    id: int
    short_code: str
    title: str
    answer_html: str


def render_guide(guide: Guide, turns: list[dict]) -> str:
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
        "<p><strong>未验证的练习</strong></p>",
        section("题干", guide.title),
        section("先说人话", guide.plain),
        section("面试口述", guide.spoken),
        section("通俗类比", guide.analogy),
        f'<h3>步骤拆解</h3><ol class="answer-flow">{steps}</ol>',
        "<h3>对照表</h3><table><thead><tr><th>场景或选项</th><th>机制</th><th>适用边界</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>",
        section("算例或反例", guide.example),
        "<h3>面试追问</h3>" + followups,
    ]
    if guide.project_context:
        messages = {
            turn[role]["id"]: (label, turn["project_id"], turn[role]["text"])
            for turn in turns if turn["project_id"] is not None
            for role, label in (("user", "学习者陈述"), ("assistant", "助手讲解"))
        }
        sections.append("<h3>项目相关对话引用</h3><p>以下引文保留对话中的归因。个人贡献及项目实绩有待独立核实。</p>")
        for reference in guide.project_context:
            message = messages.get(reference.message_id)
            if message is None or reference.quote not in message[2]:
                raise GenerationError("项目引用不属于本次对话快照")
            label, project_id, _ = message
            sections.append(f"<p>项目 {project_id} · {label} · 消息 {reference.message_id}</p>"
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


def organizer_profile(base, user, assistant, session_mode):
    coach = assistant.coach or {}
    trace = coach.get("trace") or {}
    mode = coach.get("mode") or (user.coach or {}).get("mode") or trace.get("mode")
    tier = mode if mode in ("flash", "strong") else trace.get("tier")
    if tier not in ("flash", "strong") and mode is None:
        tier = session_mode

    def usable(selected):
        cfg = profile(base, selected)
        return cfg if cfg and all(cfg.get(key) for key in ("model", "provider", "base_url", "api_key")) else None

    if tier in ("flash", "strong"):
        return usable(tier)
    # Older turns can have a model name but no routing trace.
    settings = get_settings()
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
        raise HTTPException(409, "学习会话尚无完整的提问和回复")
    payload = {"turns": turns}
    snapshot_hash = digest(payload)
    saved = await saved_result(db, workspace_id, session_id, snapshot_hash)
    if saved:
        return saved
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(422, "完整对话超出一次整理的范围，请另建短一些的主题会话")

    # Resolve the model outside the transaction that will hold the advisory lock.
    cfg = organizer_profile(await get_llm_config(), *latest, session_mode)
    await db.commit()
    lock_hash = digest(["study-interview-question", session_id, snapshot_hash])
    lock_key = int.from_bytes(bytes.fromhex(lock_hash[:16]), byteorder="big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))
    saved = await saved_result(db, workspace_id, session_id, snapshot_hash)
    if saved:
        await db.commit()
        return saved
    if cfg is None:
        raise HTTPException(502, "当前学习档位未配置可用模型，请检查配置后重试")

    instruction = teaching_prompt("interview_question") + "\n仅输出符合以下 JSON Schema 的对象：\n" + json.dumps(
        Guide.model_json_schema(), ensure_ascii=False)
    output_limit = min(4500, max(1, get_settings().study_output_budget))
    try:
        guide, _ = await read_json(instruction, payload, Guide, config={**cfg, "output_limit": output_limit})
        answer_html = render_guide(guide, turns)
    except GenerationError:
        raise HTTPException(502, "生成面试题失败，可以重试") from None

    # Match chat's lock order and recheck visibility after the model returns.
    await db.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
    visible_session = await db.scalar(select(StudySession.id).where(
        StudySession.id == session_id, StudySession.workspace_id == workspace_id,
        StudySession.hidden_at.is_(None),
    ).with_for_update())
    if visible_session is None:
        raise HTTPException(404, "学习会话不存在")

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
