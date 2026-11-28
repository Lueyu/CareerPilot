"""Public coach turns, backed by the public model gateway and cloud database."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import select, update

from app.db import SessionLocal
from app.i18n import message as localized_message
from app.i18n import request_language, using_language
from app.models.public_coach import CoachCandidate, CoachThread, CoachTurn
from app.services.public_model import _MAX_INPUT_BYTES, PublicModelError, complete_text, stream_text

log = logging.getLogger(__name__)
MODEL_TIMEOUT = 135
TURN_LEASE = timedelta(seconds=MODEL_TIMEOUT + 15)
POLL_INTERVAL = 0.25

Mode = Literal["study", "interview"]
Action = Literal["chat", "generate", "hint", "answer", "followup_answer", "finish"]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=500, pattern=r"^[^\x00]+$")]
Paragraph = Annotated[str, StringConstraints(min_length=1, max_length=1800, pattern=r"^[^\x00]+$")]
UserText = Annotated[str, StringConstraints(min_length=1, max_length=6000, pattern=r"^[^\x00]+$")]
STUDY_ACTIONS = ("chat", "generate")
USER_ORIGINS = {
    "chat": "user_statement",
    "generate": "user_request",
    "answer": "independent_answer",
    "followup_answer": "followup_answer",
}
REPLY_ORIGINS = {
    "chat": "model_explanation",
    "generate": "model_candidates",
    "hint": "model_hint",
    "answer": "model_followup",
    "finish": "model_feedback",
}
OUTPUT_BUDGET = {"chat": 3200, "hint": 1800, "generate": 6000, "answer": 3000, "finish": 4000}


class EmptyReply(ValueError):
    pass


class InvalidReply(ValueError):
    pass


def now():
    return datetime.now(timezone.utc)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ThreadInput(InputModel):
    mode: Mode
    question_id: int | None = Field(default=None, gt=0)
    direction: str = Field(default="", max_length=64, pattern=r"^[^\x00]*$")


class TurnInput(InputModel):
    request_id: UUID
    action: Action
    text: UserText | None = None
    target_number: int | None = Field(default=None, gt=0)


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class CandidateDraft(ModelOutput):
    title: ShortText
    reference: Paragraph | None = None
    replace_number: int | None = Field(default=None, gt=0)


class CandidateChanges(ModelOutput):
    reply: ShortText
    questions: list[CandidateDraft] = Field(min_length=1, max_length=8)


class Followup(ModelOutput):
    quote: ShortText
    question: ShortText


class CoveredPoint(ModelOutput):
    answer: Literal["initial", "followup"]
    quote: ShortText
    comment: ShortText


class Feedback(ModelOutput):
    covered: list[CoveredPoint]
    gaps: list[ShortText]
    next_steps: list[ShortText]


def effective_status(turn):
    if turn.status == "generating" and turn.expires_at and turn.expires_at <= now():
        return "failed"
    return turn.status


def turn_view(turn):
    status = effective_status(turn)
    origin = USER_ORIGINS.get(turn.action)
    if turn.action == "answer" and turn.assisted:
        origin = "assisted_answer"
    return {
        "id": turn.id,
        "action": turn.action,
        "user_text": turn.user_text,
        "user_origin": origin,
        "assisted": turn.assisted,
        "target_number": turn.target_number,
        "reply": turn.reply,
        "reply_origin": REPLY_ORIGINS.get(turn.action),
        "result": turn.result,
        "status": status,
        "error": turn.error or (localized_message("coach.expired", turn.response_language or "zh-CN") if status != turn.status else None),
        "attempt": turn.attempt,
        "created_at": turn.created_at.isoformat(),
    }


def interview_actions(turns):
    initial = next((turn for turn in turns if turn.action == "answer"), None)
    if initial is None:
        return ["answer"] if turns else ["answer", "hint"]
    if initial.status != "completed" or any(turn.action == "finish" for turn in turns):
        return []
    if any(turn.action == "followup_answer" for turn in turns):
        return ["finish"]
    actions = ["followup_answer", "finish"]
    if not any(turn.action == "hint" and turn.id > initial.id for turn in turns):
        actions.append("hint")
    return actions


async def thread_turns(db, thread_id):
    return list(await db.scalars(select(CoachTurn).where(CoachTurn.thread_id == thread_id).order_by(CoachTurn.id)))


async def thread_candidates(db, thread_id):
    return list(
        await db.scalars(
            select(CoachCandidate)
            .where(CoachCandidate.thread_id == thread_id)
            .order_by(CoachCandidate.number, CoachCandidate.id)
        )
    )


def _hint_exposure(turns):
    initial = next((turn for turn in turns if turn.action == "answer"), None)
    return any(
        turn.action == "hint" and bool(turn.reply) and (initial is None or turn.id > initial.id) for turn in turns
    )


def admit_turn(thread, turns, body):
    actions = STUDY_ACTIONS if thread.mode == "study" else interview_actions(turns)
    if turns and effective_status(turns[-1]) == "generating":
        raise HTTPException(409, localized_message("errors.wait_for_the_current_reply_to_finish"))
    if body.action not in actions:
        raise HTTPException(409, localized_message("errors.this_action_is_unavailable_at_the_current_step"))
    if body.action in USER_ORIGINS and body.text is None:
        raise HTTPException(422, localized_message("errors.enter_some_text"))
    if body.action not in USER_ORIGINS and body.text is not None:
        raise HTTPException(422, localized_message("errors.this_step_does_not_need_a_text_response"))
    if body.target_number is not None and body.action != "generate":
        raise HTTPException(422, localized_message("errors.specify_a_question_number_only_when_revising_a_candidate"))

    last = turns[-1] if turns else None
    assisted = _hint_exposure(turns) if body.action in {"answer", "followup_answer"} else None
    turn = CoachTurn(
        thread_id=thread.id,
        request_id=str(body.request_id),
        action=body.action,
        user_text=body.text,
        response_language=request_language(),
        assisted=assisted,
        target_number=body.target_number,
        status="completed" if body.action == "followup_answer" else "generating",
    )
    if turn.status == "generating":
        turn.expires_at = now() + TURN_LEASE
    # Fence off an expired worker before admitting a newer turn.
    if last and last.status == "generating":
        last.status = "failed"
        last.error = localized_message("coach.expired", last.response_language or "zh-CN")
        last.expires_at = None
    return turn


def _json_bytes(value):
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _fit_context(retained, items, byte_limit):
    used = 0
    for key, item in items:
        if used >= byte_limit:
            break
        # json.dumps separates list entries with a comma and a space.
        size = _json_bytes(item) + (2 if retained else 0)
        if used + size <= byte_limit:
            retained[key] = item
            used += size
    return used


def _prompt_turn(item, study):
    return {
        "动作": item.action,
        "用户原文": item.user_text[:1200] if study and item.user_text else item.user_text,
        "助手回复": item.reply[:1600],
        "结构化结果": item.result,
        "已获提示": item.assisted,
    }


@dataclass
class TurnContext:
    thread: CoachThread
    turn: CoachTurn
    history: list[CoachTurn]
    candidates: list[CoachCandidate]
    memories: list[str]

    def messages(self):
        action = self.turn.action
        study = self.thread.mode == "study"
        language = self.turn.response_language or "zh-CN"
        system = "\n".join(localized_message(key, language) for key in ("coach.system", f"coach.{action}", "language"))
        history = {
            item.id: _prompt_turn(item, study)
            for item in self.history
            if not study and item.action in {"answer", "followup_answer"} and item.status == "completed"
        }
        context = {
            "岗位": self.thread.role,
            "方向": self.thread.direction or None,
            "题目": self.thread.question_title,
            "用户确认的经历": [],
            "近几轮记录": list(history.values()),
            "当前用户原文": self.turn.user_text,
            "已获提示": self.turn.assisted,
        }
        active = {}
        selected = {}
        if action == "generate":
            active = {item.number: item for item in self.candidates if not item.superseded}
            target = active.get(self.turn.target_number)
            if target is not None:
                selected[target.number] = {"题号": target.number, "题干": target.title}
            context["当前候选题"] = list(selected.values())
            context["明确指定的修改题号"] = self.turn.target_number

        remaining = _MAX_INPUT_BYTES - len(system.encode("utf-8")) - _json_bytes(context)
        recent = (
            (item.id, _prompt_turn(item, study))
            for item in reversed(self.history[-8:])
            if item.id not in history and (item.status == "completed" or item.action == "hint" and item.reply)
        )
        remaining -= _fit_context(history, recent, min(remaining, 16_000) if study else remaining)
        context["近几轮记录"] = [history[turn_id] for turn_id in sorted(history)]

        memories = {}
        remaining -= _fit_context(memories, enumerate(self.memories), min(remaining, 12_000))
        context["用户确认的经历"] = list(memories.values())

        if action == "generate":
            candidates = (
                (number, {"题号": number, "题干": active[number].title})
                for number in sorted(active)
                if number not in selected
            )
            target_bytes = _json_bytes(context["当前候选题"]) - 2
            _fit_context(selected, candidates, min(remaining, 12_000 - target_bytes))
            context["当前候选题"] = [selected[number] for number in sorted(selected)]

        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]


def _model_json(raw):
    value = raw.strip()
    first_line, _, body = value.partition("\n")
    if first_line.strip().lower() in {"```", "```json"} and body.endswith("```"):
        value = body[:-3].strip()
    if not value:
        raise EmptyReply
    return value


def _structured_result(context, raw):
    turn = context.turn
    value = _model_json(raw)
    if turn.action == "generate":
        changes = CandidateChanges.model_validate_json(value)
        available = {item.number for item in context.candidates if not item.superseded}
        replacements = [item.replace_number for item in changes.questions if item.replace_number is not None]
        if len(replacements) != len(set(replacements)) or not set(replacements).issubset(available):
            raise InvalidReply
        if turn.target_number is not None and (replacements != [turn.target_number] or len(changes.questions) != 1):
            raise InvalidReply
        return changes.reply, changes
    if turn.action == "answer":
        followup = Followup.model_validate_json(value)
        if followup.quote not in (turn.user_text or ""):
            raise InvalidReply
        return followup.question, followup

    feedback = Feedback.model_validate_json(value)
    answers = {
        "initial": next((item.user_text for item in context.history if item.action == "answer"), None),
        "followup": next((item.user_text for item in context.history if item.action == "followup_answer"), None),
    }
    feedback.covered = [point for point in feedback.covered if point.quote in (answers[point.answer] or "")][:5]
    feedback.gaps = feedback.gaps[:5]
    feedback.next_steps = feedback.next_steps[:4]
    return "", feedback


def _apply_candidates(db, thread, turn, candidates, changes):
    active = {item.number: item for item in candidates if not item.superseded}
    next_number = max(active, default=0) + 1
    for draft in changes.questions:
        number = draft.replace_number
        if number is None:
            number = next_number
            next_number += 1
        else:
            active[number].superseded = True
        db.add(
            CoachCandidate(
                thread_id=thread.id,
                turn_id=turn.id,
                number=number,
                title=draft.title,
                reference=draft.reference,
            )
        )


async def _load_context(workspace_id, thread_id, turn_id, attempt):
    from app.api.public_memory import confirmed_context

    async with SessionLocal() as db:
        thread = await db.scalar(
            select(CoachThread).where(
                CoachThread.id == thread_id,
                CoachThread.workspace_id == workspace_id,
            )
        )
        if thread is None:
            return None
        turns = await thread_turns(db, thread_id)
        turn = next((row for row in turns if row.id == turn_id), None)
        if turn is None or turn.attempt != attempt or effective_status(turn) != "generating":
            return None
        candidates = await thread_candidates(db, thread_id) if turn.action == "generate" else []
        memories = await confirmed_context(db, workspace_id)
        return TurnContext(thread, turn, [row for row in turns if row.id < turn.id], candidates, memories)


async def _update_turn(turn_id, attempt, **values):
    async with SessionLocal() as db:
        changed = await db.execute(
            update(CoachTurn)
            .where(
                CoachTurn.id == turn_id,
                CoachTurn.attempt == attempt,
                CoachTurn.status == "generating",
                CoachTurn.expires_at > now(),
            )
            .values(**values)
        )
        await db.commit()
        return bool(changed.rowcount)


async def _finish_turn(context, reply, result=None):
    turn_id = context.turn.id
    async with SessionLocal() as db:
        thread = await db.scalar(
            select(CoachThread)
            .where(
                CoachThread.id == context.thread.id,
                CoachThread.workspace_id == context.thread.workspace_id,
            )
            .with_for_update()
        )
        if thread is None:
            return
        turn = await db.scalar(
            select(CoachTurn)
            .where(
                CoachTurn.id == turn_id,
                CoachTurn.thread_id == thread.id,
            )
            .with_for_update()
        )
        if turn is None or turn.attempt != context.turn.attempt or effective_status(turn) != "generating":
            return
        if isinstance(result, CandidateChanges):
            _apply_candidates(db, thread, turn, await thread_candidates(db, thread.id), result)
        else:
            turn.result = result.model_dump() if result else None
        turn.reply = reply
        turn.status = "completed"
        turn.expires_at = None
        thread.updated_at = now()
        await db.commit()


def _public_error(exc, language=None):
    from app.services.model_outbound import SAFE_MESSAGE
    if (isinstance(exc, PublicModelError) and isinstance(exc.detail, dict)
            and exc.detail.get("code") == "MODEL_OUTBOUND_POLICY"):
        return SAFE_MESSAGE
    if isinstance(exc, PublicModelError) and isinstance(exc.detail, str):
        return exc.detail
    status = getattr(exc, "status_code", None)
    if status == 429:
        return localized_message("coach.error.quota", language)
    if isinstance(exc, TimeoutError) or status in {408, 504}:
        return localized_message("coach.error.timeout", language)
    if isinstance(exc, EmptyReply):
        return localized_message("coach.error.empty", language)
    if isinstance(exc, (ValidationError, InvalidReply)):
        return localized_message("coach.error.format", language)
    return localized_message("coach.error.failed", language)


async def run_turn(workspace_id, thread_id, turn_id, attempt):
    context = None
    try:
        async with asyncio.timeout(MODEL_TIMEOUT):
            context = await _load_context(workspace_id, thread_id, turn_id, attempt)
            if context is None:
                return
            with using_language(context.turn.response_language or "zh-CN"):
                action = context.turn.action
                key = f"coach:{turn_id}:{attempt}"
                messages = context.messages()
                if action in {"chat", "hint"}:
                    reply = ""
                    last_saved = 0.0
                    async for fragment in stream_text(
                        workspace_id,
                        key,
                        messages,
                        max_tokens=OUTPUT_BUDGET[action],
                    ):
                        reply += fragment.replace("\x00", "")
                        if reply.strip() and monotonic() - last_saved >= 0.15:
                            if not await _update_turn(turn_id, attempt, reply=reply):
                                return
                            last_saved = monotonic()
                    if not reply.strip():
                        raise EmptyReply
                    await _finish_turn(context, reply)
                else:
                    raw = await complete_text(workspace_id, key, messages, max_tokens=OUTPUT_BUDGET[action])
                    reply, result = _structured_result(context, raw)
                    await _finish_turn(context, reply, result)
    except Exception as exc:
        log.warning(
            "Public coach turn failed",
            extra={
                "coach_turn_id": turn_id,
                "attempt": attempt,
                "error_type": type(exc).__name__,
            },
        )
        await _update_turn(turn_id, attempt, status="failed", error=_public_error(exc, context.turn.response_language if context else None), expires_at=None)


async def turn_events(workspace_id, thread_id, turn_id):
    previous = None
    last_heartbeat = monotonic()
    while True:
        async with SessionLocal() as db:
            turn = await db.scalar(
                select(CoachTurn)
                .join(CoachThread)
                .where(
                    CoachTurn.id == turn_id,
                    CoachThread.id == thread_id,
                    CoachThread.workspace_id == workspace_id,
                )
            )
        if turn is None:
            yield "event: unavailable\ndata: {}\n\n"
            return
        snapshot = turn_view(turn)
        if snapshot != previous:
            yield "data: " + json.dumps(snapshot, ensure_ascii=False) + "\n\n"
            previous = snapshot
        if snapshot["status"] != "generating":
            return
        if monotonic() - last_heartbeat >= 10:
            yield ": keep-alive\n\n"
            last_heartbeat = monotonic()
        await asyncio.sleep(POLL_INTERVAL)
