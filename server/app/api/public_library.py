"""Public question library. All persisted records are scoped by the authenticated workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import PurePosixPath
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from app.api.public_auth import current_workspace
from app.db import get_session
from app.i18n import message as localized_message
from app.i18n import request_language
from app.models import ImportItem, ImportSession, Question, Workspace
from app.models.public_library import PublicLibraryRole, PublicLibraryStarter
from app.models.question import SOURCE_GENERATED, SOURCE_PRIVATE
from app.services.interview_import_text import extract_structured_questions, read_json
from app.services.public_library_extract import MAX_UPLOAD_BYTES, SUFFIXES, extract_async
from app.services.public_library_presets import example_titles, preset_directions
from app.services.public_model import complete_text
from app.services.untrusted_source import SOURCE_AUTHORITY_CONTRACT, source_json, source_review

router = APIRouter(prefix="/library", tags=["public-library"])

ORIGIN = "public_library"
MAX_CANDIDATES = 20
BODY_LIMIT = MAX_UPLOAD_BYTES + 16_384
PARSE_SLOTS = asyncio.Semaphore(2)
STARTER_MODEL_TIMEOUT = 135
STARTER_LEASE = timedelta(minutes=3)

Db = Annotated[AsyncSession, Depends(get_session)]
Space = Annotated[Workspace, Depends(current_workspace)]
RoleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\x00]+$")]
TopicName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[^\x00]+$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=240, pattern=r"^[^\x00]+$")]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000, pattern=r"^[^\x00]*$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleInput(Input):
    role: RoleName


class RolePatch(RoleInput):
    jd: Annotated[str, StringConstraints(strip_whitespace=True, max_length=6000, pattern=r"^[^\x00]*$")] | None = None


class DirectionInput(RoleInput):
    topic: TopicName


class DirectionDraft(Input):
    name: TopicName
    example: Title


class ModelQuestion(Input):
    title: Title
    answer: Answer = ""


class ImportSuggestion(ModelQuestion):
    source_answer: Answer = ""


class CandidateEdit(Input):
    id: int = Field(gt=0)
    title: Title
    answer: Answer
    selected: bool


class ReviewInput(Input):
    items: list[CandidateEdit] = Field(min_length=1, max_length=MAX_CANDIDATES)


def _title_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold()) or value.casefold()


async def _lock_space(db: AsyncSession, workspace_id: int) -> Workspace:
    return await db.scalar(
        select(Workspace)
        .where(Workspace.id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _role_rows(workspace_id: int):
    return select(PublicLibraryRole).where(PublicLibraryRole.workspace_id == workspace_id)


async def _role(db: AsyncSession, workspace_id: int, name: str) -> PublicLibraryRole:
    role = await db.get(PublicLibraryRole, (workspace_id, name), populate_existing=True)
    if role is None:
        raise HTTPException(404, localized_message("errors.save_the_role_before_continuing"))
    return role


def _direction(role: PublicLibraryRole, topic: str) -> dict[str, str]:
    for entry in role.directions:
        if entry["name"] == topic:
            return entry
    raise HTTPException(404, localized_message("errors.practice_topic_not_found"))


def _role_view(role: PublicLibraryRole) -> dict:
    return dict(role=role.name, jd=role.jd, selected_topic=role.selected_topic,
                directions=[{**d, "examples": example_titles(d, role.name)} for d in role.directions])


@router.get("/roles")
async def list_roles(db: Db, ws: Space):
    roles = [_role_view(role) for role in await db.scalars(_role_rows(ws.id).order_by(PublicLibraryRole.name))]
    selected = ws.target_role if any(role["role"] == ws.target_role for role in roles) else None
    return {"selected_role": selected, "roles": roles}


@router.put("/role")
async def save_role(body: RolePatch, db: Db, ws: Space):
    workspace = await _lock_space(db, ws.id)
    role = await db.get(PublicLibraryRole, (workspace.id, body.role), populate_existing=True)
    if role is None:
        role = PublicLibraryRole(workspace_id=workspace.id, name=body.role, directions=preset_directions(body.role))
        db.add(role)
    if "jd" in body.model_fields_set:
        role.jd = body.jd or None
    workspace.target_role = body.role
    view = _role_view(role)
    await db.commit()
    return view


@router.put("/direction")
async def select_direction(body: DirectionInput, db: Db, ws: Space):
    await _lock_space(db, ws.id)
    role = await _role(db, ws.id, body.role)
    _direction(role, body.topic)
    role.selected_topic = body.topic
    view = _role_view(role)
    await db.commit()
    return view


async def _model_list(
    workspace_id: int, purpose: str, instruction: str, payload: dict, schema: type[BaseModel], limit: int,
    *, attempt_id: str | None = None,
):
    raw = await complete_text(
        workspace_id,
        f"library:{purpose}:{attempt_id or uuid4().hex}",
        [
            {
                "role": "system",
                "content": localized_message("library.system") + "\n" + instruction + "\n" + SOURCE_AUTHORITY_CONTRACT,
            },
            {"role": "user", "content": source_json(payload)},
        ],
        max_tokens=1600,
    )
    try:
        data = read_json(raw)
        if isinstance(data, dict):
            data = data.get("directions" if purpose == "directions" else "questions")
        return TypeAdapter(list[schema]).validate_python(data[:limit] if isinstance(data, list) else data)
    except (ValidationError, RecursionError):
        raise HTTPException(502, localized_message("errors.the_model_response_could_not_be_read_try_again"))


@router.post("/directions")
async def recommend_directions(body: RoleInput, db: Db, ws: Space):
    role = await _role(db, ws.id, body.role)
    workspace_id, jd = ws.id, role.jd
    await db.rollback()
    drafts = await _model_list(
        workspace_id,
        "directions",
        localized_message("library.directions"),
        {"role": body.role, "jd": jd},
        DirectionDraft,
        5,
    )
    if not drafts:
        raise HTTPException(502, localized_message("errors.the_model_returned_no_usable_topics_try_again"))
    await _lock_space(db, workspace_id)
    role = await _role(db, workspace_id, body.role)
    if role.jd != jd:
        raise HTTPException(409, localized_message("errors.the_job_requirements_have_changed_generate_again_using_the"))
    directions = {entry["name"]: entry for entry in role.directions}
    for draft in drafts:
        if draft.name in directions or len(directions) < 24:
            directions[draft.name] = {**draft.model_dump(), "origin": "model"}
    role.directions = list(directions.values())
    view = _role_view(role)
    await db.commit()
    return view


def _questions(workspace_id: int):
    return select(Question).where(Question.workspace_id == workspace_id, Question.is_demo.is_(False))


def _question_view(question: Question) -> dict:
    meta = question.generation_meta or {}
    return dict(
        id=question.id,
        title=question.title,
        role=question.role,
        topic=question.topic,
        answer=meta.get("library_answer", ""),
        origin=meta.get("origin"),
        source_type=question.source_type,
        filename=meta.get("filename"),
    )


@router.get("/questions")
async def list_questions(
    db: Db,
    ws: Space,
    role: RoleName | None = None,
    topic: TopicName | None = None,
    q: Annotated[str | None, Query(max_length=240)] = None,
    before: Annotated[int | None, Query(gt=0)] = None,
):
    query = _questions(ws.id)
    if role is not None:
        query = query.where(Question.role == role)
    if topic is not None:
        query = query.where(Question.topic == topic)
    if q and q.strip():
        query = query.where(Question.title.icontains(q.strip(), autoescape=True))
    if before is not None:
        query = query.where(Question.id < before)
    rows = (await db.scalars(query.order_by(Question.id.desc()).limit(51))).all()
    return {
        "questions": [_question_view(row) for row in rows[:50]],
        "next_before": rows[49].id if len(rows) > 50 else None,
    }


@router.get("/questions/{question_id}")
async def read_question(question_id: int, db: Db, ws: Space):
    question = await db.scalar(_questions(ws.id).where(Question.id == question_id))
    if question is None:
        raise HTTPException(404, localized_message("errors.this_question_could_not_be_found"))
    return _question_view(question)


def _new_question(workspace_id: int, role: str, topic: str, title: str, answer: str, origin: str, **meta) -> Question:
    return Question(
        workspace_id=workspace_id,
        short_code=f"PL-{uuid4().hex[:29]}",
        title=title,
        role=role,
        topic=topic,
        source_type=SOURCE_PRIVATE if origin == "library_import" else SOURCE_GENERATED,
        answer_html=f"<pre>{escape(answer)}</pre>" if answer else None,
        generation_meta={
            "assessment_use": "PRACTICE_ONLY",
            "origin": origin,
            "library_answer": answer,
            **meta,
        },
    )


async def _by_title(db: AsyncSession, workspace_id: int, role: str, topic: str) -> dict[str, Question]:
    query = _questions(workspace_id).where(Question.role == role, Question.topic == topic).order_by(Question.id.desc())
    return {_title_key(row.title): row for row in await db.scalars(query)}


def _starter_inputs(role: PublicLibraryRole, topic: str) -> dict:
    language = request_language()
    example = example_titles(_direction(role, topic), role.name)[language]
    inputs = {"role": role.name, "jd": role.jd, "topic": topic, "example": example}
    if language != "zh-CN":
        inputs["response_language"] = language
    return inputs


def _starter_view(rows: Iterable[Question]) -> dict:
    return {"questions": [_question_view(row) for row in rows]}


def _starter_pending() -> HTTPException:
    return HTTPException(409, localized_message("errors.starter_questions_are_being_generated_check_again_shortly"))


async def _adopt_legacy_starters(
    db: AsyncSession, operation: PublicLibraryStarter, body: DirectionInput
) -> list[Question]:
    query = _questions(operation.workspace_id).where(
        Question.role == body.role,
        Question.topic == body.topic,
        Question.generation_meta["origin"].as_string() == "library_starter",
        Question.generation_meta["starter_input_digest"].as_string().is_(None),
    )
    rows = list(await db.scalars(query.order_by(Question.id)))
    if rows:
        # Older batches lack input provenance. Adopt them once for this direction.
        for row in rows:
            row.generation_meta = {**row.generation_meta, "starter_input_digest": operation.input_digest}
        operation.question_ids = [row.id for row in rows]
    return rows


@router.post("/example")
async def save_example(body: DirectionInput, db: Db, ws: Space):
    await _lock_space(db, ws.id)
    role = await _role(db, ws.id, body.role)
    direction = _direction(role, body.topic)
    title = example_titles(direction, role.name)[request_language()]
    known = await _by_title(db, ws.id, body.role, body.topic)
    question = known.get(_title_key(title))
    if question is None:
        question = _new_question(
            ws.id,
            body.role,
            body.topic,
            title,
            "",
            "library_example",
            direction_origin=direction["origin"],
        )
        db.add(question)
        await db.flush()
    view = _question_view(question)
    await db.commit()
    return view


@router.post("/starter")
async def starter_questions(body: DirectionInput, db: Db, ws: Space):
    workspace_id = ws.id
    await _lock_space(db, workspace_id)
    role = await _role(db, workspace_id, body.role)
    inputs = _starter_inputs(role, body.topic)
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    operation_key = (workspace_id, digest)
    operation = await db.get(PublicLibraryStarter, operation_key)
    if operation is not None and operation.question_ids is not None:
        query = _questions(workspace_id).where(Question.id.in_(operation.question_ids))
        by_id = {row.id: row for row in await db.scalars(query)}
        view = _starter_view(by_id[question_id] for question_id in operation.question_ids if question_id in by_id)
        await db.rollback()
        return view

    now = await db.scalar(select(func.clock_timestamp()))
    token = uuid4().hex
    legacy = []
    if operation is None:
        operation = PublicLibraryStarter(workspace_id=workspace_id, input_digest=digest, attempt_id=token)
        db.add(operation)
        legacy = await _adopt_legacy_starters(db, operation, body) if request_language() == "zh-CN" else []
    elif operation.failed_at is not None:
        # A new submission can retry, but a submission that overlapped the failure cannot.
        if operation.failed_at >= await db.scalar(select(func.transaction_timestamp())):
            raise HTTPException(409, localized_message("errors.the_last_attempt_to_generate_starter_questions_failed_submit"))
        operation.attempt_id = token
        operation.failed_at = None
    elif operation.lease_until > now:
        raise _starter_pending()

    # An expired lease keeps the same gateway key to replay any settled response.
    operation.lease_token = token
    operation.lease_until = now + STARTER_LEASE
    attempt_id = operation.attempt_id
    await db.commit()
    if legacy:
        return _starter_view(legacy)

    try:
        async with asyncio.timeout(STARTER_MODEL_TIMEOUT):
            drafts = await _model_list(
                workspace_id,
                "starter",
                localized_message("library.starter"),
                inputs,
                ModelQuestion,
                3,
                attempt_id=attempt_id,
            )
        if not drafts:
            raise HTTPException(502, localized_message("errors.the_model_returned_no_starter_questions_try_again"))
    except TimeoutError:
        raise HTTPException(504, localized_message("errors.the_starter_question_request_timed_out_submit_again_to")) from None
    except HTTPException as error:
        await _lock_space(db, workspace_id)
        current = await db.get(PublicLibraryStarter, operation_key, populate_existing=True)
        if current is None or current.lease_token != token:
            raise _starter_pending() from None
        now = await db.scalar(select(func.clock_timestamp()))
        if error.status_code == 503:
            # Admission or settlement may have committed before a database error.
            # Let the next submission reconcile against the same reservation.
            current.lease_until = now
        else:
            current.failed_at = now
        await db.commit()
        if error.status_code == 409:
            raise HTTPException(409, localized_message("errors.the_last_starter_question_attempt_did_not_finish_submit")) from None
        raise

    await _lock_space(db, workspace_id)
    operation = await db.get(PublicLibraryStarter, operation_key, populate_existing=True)
    if operation is None or operation.lease_token != token:
        raise _starter_pending()
    role = await _role(db, workspace_id, body.role)
    if _starter_inputs(role, body.topic) != inputs:
        operation.lease_until = await db.scalar(select(func.clock_timestamp()))
        await db.commit()
        raise HTTPException(409, localized_message("errors.the_requirements_or_example_question_changed_generate_starter_questions"))
    known = await _by_title(db, workspace_id, body.role, body.topic)
    rows: dict[str, Question] = {}
    for draft in drafts:
        key = _title_key(draft.title)
        if key not in known:
            known[key] = _new_question(
                workspace_id, body.role, body.topic, draft.title, draft.answer, "library_starter",
                starter_input_digest=digest,
            )
            db.add(known[key])
        rows[key] = known[key]
    await db.flush()
    operation.question_ids = [row.id for row in rows.values()]
    view = _starter_view(rows.values())
    await db.commit()
    return view


def _imports(workspace_id: int):
    return select(ImportSession).where(
        ImportSession.workspace_id == workspace_id,
        ImportSession.meta["origin"].as_string() == ORIGIN,
    )


async def _owned_import(db: AsyncSession, workspace_id: int, import_id: int, *, lock: bool = False) -> ImportSession:
    query = _imports(workspace_id).where(ImportSession.id == import_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    item = await db.scalar(query)
    if item is None:
        raise HTTPException(404, localized_message("errors.import_record_not_found"))
    return item


def _import_summary(batch: ImportSession) -> dict:
    meta = batch.meta
    return dict(
        id=batch.id,
        filename=batch.filename,
        status=batch.status,
        role=meta["role"],
        topic=meta["topic"],
        error=meta.get("error"),
        suggested=meta.get("suggested", False),
        created_at=batch.created_at,
    )


def _import_view(batch: ImportSession) -> dict:
    return {
        **_import_summary(batch),
        "source_text": batch.paste_text or "",
        "source_review": batch.meta.get("source_review"),
        "items": [
            dict(
                id=item.id,
                title=item.title,
                answer=item.draft_answer or "",
                status=item.status,
                question_id=item.question_id or item.merged_into,
                selected=item.generation_meta["selected"],
                source_title=item.generation_meta.get("source_title"),
                source_answer=item.generation_meta.get("source_answer"),
                ai_answer=item.generation_meta.get("ai_answer"),
                ai_title=item.generation_meta.get("ai_title", False),
            )
            for item in sorted(batch.items, key=lambda item: item.id)
        ],
    }


@router.get("/imports")
async def list_imports(db: Db, ws: Space, before: Annotated[int | None, Query(gt=0)] = None):
    query = _imports(ws.id)
    if before is not None:
        query = query.where(ImportSession.id < before)
    batches = (await db.scalars(query.order_by(ImportSession.id.desc()).limit(31))).all()
    return {
        "imports": [_import_summary(batch) for batch in batches[:30]],
        "next_before": batches[29].id if len(batches) > 30 else None,
    }


@router.get("/imports/{import_id}")
async def read_import(import_id: int, db: Db, ws: Space):
    return _import_view(await _owned_import(db, ws.id, import_id))


async def _upload(request: Request) -> tuple[DirectionInput, str, str, bytes]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, localized_message("errors.use_the_file_upload_form"))
    size = 0

    async def receive():
        nonlocal size
        message = await request.receive()
        size += len(message.get("body", b""))
        if size > BODY_LIMIT:
            raise MultiPartException("upload limit exceeded")
        return message

    bounded = Request(request.scope, receive)
    try:
        async with bounded.form(max_files=1, max_fields=2, max_part_size=1024) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile) or not file.filename:
                raise HTTPException(422, localized_message("errors.select_one_file"))
            direction = DirectionInput.model_validate({"role": form.get("role"), "topic": form.get("topic")})
            filename = PurePosixPath(file.filename.replace("\\", "/")).name.replace("\x00", "")[-255:]
            suffix = PurePosixPath(filename).suffix.lower()
            if suffix not in SUFFIXES:
                raise HTTPException(415, localized_message("errors.select_a_pdf_docx_or_markdown_file"))
            raw = await file.read(MAX_UPLOAD_BYTES + 1)
    except ValidationError:
        raise HTTPException(422, localized_message("errors.select_a_valid_role_and_practice_topic"))
    except StarletteHTTPException as exc:
        if size > BODY_LIMIT:
            raise HTTPException(413, localized_message("errors.the_upload_exceeds_6_mb_split_the_document_into"))
        if exc.status_code != 400:
            raise
        raise HTTPException(400, localized_message("errors.the_upload_form_could_not_be_read_select_the"))
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, localized_message("errors.the_upload_exceeds_6_mb_split_the_document_into"))
    return direction, filename, suffix, raw


async def _parse_document(suffix: str, raw: bytes) -> dict[str, str]:
    return await extract_async(suffix, raw)


def _candidate(
    title: str,
    source_answer: str,
    *,
    source_title: str | None = None,
    ai_answer: str | None = None,
) -> ImportItem:
    return ImportItem(
        title=title,
        draft_answer=source_answer or ai_answer or "",
        generation_meta={
            "selected": True,
            "source_title": source_title,
            "source_answer": source_answer,
            "ai_title": source_title is None,
            "ai_answer": ai_answer,
        },
    )


@router.post("/imports")
async def upload_import(request: Request, db: Db, ws: Space):
    workspace_id = ws.id
    await db.rollback()
    async with PARSE_SLOTS:
        return await _parse_import(request, db, workspace_id)


async def _parse_import(request: Request, db: AsyncSession, workspace_id: int):
    body, filename, suffix, raw = await _upload(request)
    role = await _role(db, workspace_id, body.role)
    _direction(role, body.topic)
    digest = hashlib.sha256(raw).hexdigest()
    prior = (
        _imports(workspace_id)
        .where(
            ImportSession.meta["role"].as_string() == body.role,
            ImportSession.meta["topic"].as_string() == body.topic,
            ImportSession.meta["digest"].as_string() == digest,
            ImportSession.meta["suffix"].as_string() == suffix,
            ImportSession.status != "FAILED",
        )
        .order_by(ImportSession.id)
        .limit(1)
    )
    batch = await db.scalar(prior)
    if batch:
        return _import_view(batch)
    await db.rollback()
    result = await _parse_document(suffix, raw)
    await _lock_space(db, workspace_id)
    batch = await db.scalar(prior)
    if batch:
        return _import_view(batch)

    source_text = result.get("text", "")
    candidates = {}
    for draft in extract_structured_questions(source_text, include_numbered_answers=True)[:MAX_CANDIDATES]:
        title = draft["title"]
        candidates.setdefault(_title_key(title), _candidate(title, draft["answer"][:4000], source_title=title))
    batch = ImportSession(
        workspace_id=workspace_id,
        short_code=f"PL-{uuid4().hex[:29]}",
        filename=filename,
        paste_text=source_text,
        status="FAILED" if "error" in result else ImportSession.STATUS_REVIEW,
        meta={
            "origin": ORIGIN,
            "role": body.role,
            "topic": body.topic,
            "digest": digest,
            "suffix": suffix,
            "error": result.get("error"),
            "source_review": source_review([(filename, source_text)]),
        },
        items=list(candidates.values()),
    )
    db.add(batch)
    await db.flush()
    view = _import_view(batch)
    await db.commit()
    return view


@router.post("/imports/{import_id}/suggest")
async def suggest_import(import_id: int, db: Db, ws: Space):
    workspace_id = ws.id
    batch = await _owned_import(db, workspace_id, import_id)
    if batch.status != ImportSession.STATUS_REVIEW or batch.meta.get("suggested"):
        return _import_view(batch)
    source_text = batch.paste_text or ""
    payload = {"role": batch.meta["role"], "topic": batch.meta["topic"], "document": source_text}
    await db.rollback()
    drafts = await _model_list(
        workspace_id,
        "import",
        localized_message("library.import"),
        payload,
        ImportSuggestion,
        10,
    )
    if not drafts:
        raise HTTPException(502, localized_message("errors.no_usable_questions_found_check_the_source_or_upload"))
    batch = await _owned_import(db, workspace_id, import_id, lock=True)
    if batch.status != ImportSession.STATUS_REVIEW or batch.meta.get("suggested"):
        return _import_view(batch)
    known = {_title_key(item.title): item for item in batch.items}
    plain = " ".join(source_text.split())
    for draft in drafts:
        title = " ".join(draft.title.split())
        source_title = draft.title if title in plain else None
        source_answer = draft.source_answer if " ".join(draft.source_answer.split()) in plain else ""
        ai_answer = draft.answer or None
        item = known.get(_title_key(draft.title))
        if item is None and len(batch.items) < MAX_CANDIDATES:
            item = _candidate(draft.title, source_answer, source_title=source_title, ai_answer=ai_answer)
            batch.items.append(item)
            known[_title_key(draft.title)] = item
        elif item is not None and item.status == ImportItem.STATUS_DRAFT and not item.draft_answer and ai_answer:
            item.draft_answer = ai_answer
            item.generation_meta = {**item.generation_meta, "ai_answer": ai_answer}
    batch.meta = {**batch.meta, "suggested": True}
    await db.flush()
    view = _import_view(batch)
    await db.commit()
    return view


async def _review(import_id: int, body: ReviewInput, db: AsyncSession, workspace_id: int, *, approve: bool):
    if approve:
        await _lock_space(db, workspace_id)
    batch = await _owned_import(db, workspace_id, import_id, lock=True)
    if batch.status == "FAILED":
        raise HTTPException(409, localized_message("errors.the_file_could_not_be_parsed_upload_it_again"))

    by_id = {item.id: item for item in batch.items}
    edit_ids = {edit.id for edit in body.items}
    if edit_ids - by_id.keys() or len(edit_ids) != len(body.items):
        raise HTTPException(422, localized_message("errors.this_draft_question_belongs_to_a_different_import_refresh"))

    role, topic = batch.meta["role"], batch.meta["topic"]
    known = await _by_title(db, workspace_id, role, topic) if approve else {}
    links: list[tuple[ImportItem, Question]] = []
    for edit in body.items:
        item = by_id[edit.id]
        if item.status != ImportItem.STATUS_DRAFT:
            continue
        item.title, item.draft_answer = edit.title, edit.answer
        item.generation_meta = {**item.generation_meta, "selected": edit.selected}
        if approve and edit.selected:
            key = _title_key(edit.title)
            question = known.get(key)
            if question is None:
                question = _new_question(
                    workspace_id,
                    role,
                    topic,
                    edit.title,
                    edit.answer,
                    "library_import",
                    filename=batch.filename,
                    source_import_id=batch.id,
                    source_item_id=item.id,
                    source_title=item.generation_meta["source_title"],
                    source_answer=item.generation_meta["source_answer"],
                    ai_title=item.generation_meta["ai_title"],
                    ai_answer=item.generation_meta["ai_answer"],
                )
                db.add(question)
                known[key] = question
                item.status = ImportItem.STATUS_APPROVED
            else:
                item.status = ImportItem.STATUS_MERGED
            links.append((item, question))

    await db.flush()
    for item, question in links:
        if item.status == ImportItem.STATUS_MERGED:
            item.merged_into = question.id
        else:
            item.question_id = question.id
    if batch.items and all(item.status != ImportItem.STATUS_DRAFT for item in batch.items):
        batch.status = ImportSession.STATUS_APPROVED
        batch.reviewed_at = batch.reviewed_at or datetime.now(timezone.utc)
    view = _import_view(batch)
    await db.commit()
    return view


@router.put("/imports/{import_id}/draft")
async def save_import_draft(import_id: int, body: ReviewInput, db: Db, ws: Space):
    return await _review(import_id, body, db, ws.id, approve=False)


@router.post("/imports/{import_id}/approve")
async def approve_import(import_id: int, body: ReviewInput, db: Db, ws: Space):
    return await _review(import_id, body, db, ws.id, approve=True)


async def export_library(db: AsyncSession, workspace_id: int) -> dict:
    workspace = await db.get(Workspace, workspace_id)
    roles = [_role_view(role) for role in await db.scalars(_role_rows(workspace_id).order_by(PublicLibraryRole.name))]
    batches = [_import_view(row) for row in await db.scalars(_imports(workspace_id).order_by(ImportSession.id))]
    questions = [
        {
            **_question_view(row),
            "answer_html": row.answer_html,
            "qtype": row.qtype,
            "difficulty": row.difficulty,
            "options": row.options,
            "rubric": row.rubric,
            "created_at": row.created_at.isoformat(),
        }
        for row in await db.scalars(_questions(workspace_id).order_by(Question.id))
    ]
    return {
        "selected_role": workspace.target_role
        if workspace and any(role["role"] == workspace.target_role for role in roles)
        else None,
        "roles": roles,
        "imports": [{**batch, "created_at": batch["created_at"].isoformat()} for batch in batches],
        "questions": questions,
    }
