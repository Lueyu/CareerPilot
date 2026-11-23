"""Import authenticated beta workspace snapshots into the separate native database."""

from hashlib import sha256
from json import dumps
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import lazyload

from app.models import MemoryItem, Question, StudySession, Workspace
from app.models.study_notes import StudyNote, StudyNoteVersion

TRANSFER_KEY = "native_beta_transfer_v1"
QUESTION_FIELDS = (
    "title", "qtype", "topic", "difficulty", "options", "answer_html", "rubric",
    "company", "role", "level", "occurred_on", "source_type", "tags", "keywords", "created_at",
)
QUESTION_META = (
    "assessment_use", "origin", "library_answer", "filename", "source_title", "source_answer",
    "ai_title", "ai_answer", "direction_origin",
)
EXPERIENCE_FIELDS = ("content", "confirmed_at", "revision", "created_at", "mtype", "source", "status")


def _values(row, fields):
    return {field: getattr(row, field) for field in fields}


def _fingerprint(values):
    return sha256(dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def _question_values(row):
    meta = row.generation_meta or {}
    return {
        **_values(row, QUESTION_FIELDS),
        "generation_meta": {key: meta[key] for key in QUESTION_META if key in meta} or None,
    }


def _owned(model, workspace_id):
    return select(model).options(lazyload("*")).where(model.workspace_id == workspace_id)


def _locked(query):
    return query.with_for_update().execution_options(populate_existing=True)


async def _transfer_rows(destination, snapshots, model, workspace_id, records, create):
    for key, values in snapshots.items():
        fingerprint = _fingerprint(values)
        prior = records.get(key)
        if prior and prior.get("withdrawn"):
            continue
        if prior is None:
            native = create(values)
            destination.add(native)
            await destination.flush()
        else:
            if fingerprint == prior["fingerprint"]:
                continue
            native = await destination.scalar(_locked(_owned(model, workspace_id).where(model.id == prior["id"])))
            if native is None or _fingerprint(_values(native, values)) != prior["fingerprint"]:
                continue
            for field, value in values.items():
                setattr(native, field, value)
        records[key] = {"id": native.id, "fingerprint": fingerprint}


async def _withdraw_experiences(destination, workspace_id, snapshots, records):
    for key in records.keys() - snapshots.keys():
        prior = records[key]
        if prior.get("withdrawn"):
            continue
        native = await destination.scalar(_locked(
            _owned(MemoryItem, workspace_id).where(MemoryItem.id == prior["id"])
        ))
        if native is not None and _fingerprint(_values(native, EXPERIENCE_FIELDS)) == prior["fingerprint"]:
            native.status = MemoryItem.STATUS_DISMISSED
        records[key] = {**prior, "withdrawn": True}


async def _transfer_notes(destination, snapshots, destination_id, records):
    for key, title, updated_at, values in snapshots:
        prior = records.get(key)
        fingerprint = _fingerprint([title, values])
        if prior is None:
            origin = StudySession(
                workspace_id=destination_id, title=title, status="ended", context={},
                # The origin satisfies the notebook FK without appearing as a conversation.
                hidden_at=updated_at,
            )
            destination.add(origin)
            await destination.flush()
            native = StudyNote(
                workspace_id=destination_id, title=title, origin_session_id=origin.id,
            )
            destination.add(native)
            await destination.flush()
        else:
            if fingerprint == prior["fingerprint"]:
                continue
            native = await destination.scalar(_locked(
                _owned(StudyNote, destination_id).where(StudyNote.id == prior["id"])
            ))
            if native is None or native.current_version_id != prior["version_id"] or native.title != prior["title"]:
                continue

        copied = StudyNoteVersion(
            note_id=native.id, base_version_id=native.current_version_id,
            sources=[], session_ids=[], coverage=[], **values,
        )
        destination.add(copied)
        await destination.flush()
        native.current_version_id = copied.id
        native.title = title
        native.updated_at = updated_at
        records[key] = {
            "id": native.id, "version_id": copied.id, "title": native.title, "fingerprint": fingerprint,
        }


async def transfer_native_workspace(
    source: AsyncSession,
    destination: AsyncSession,
    source_workspace_id: int,
    destination_workspace_id: int,
) -> None:
    """Copy one trusted account binding. The caller commits the destination on success.

    Run in a clean destination transaction against the separated native DB. This
    reads beta only. Withdraw untouched experience projections from native
    context when their beta records disappear. Preserve the native row for history.
    Missing or edited native rows retain their ledger entry and are never replaced
    on a retry. Beta conversations, feedback, and source-only links stay in beta.
    """
    if source is destination or source.bind is destination.bind:
        raise ValueError("Transfer requires separate beta and native database sessions")
    native = await destination.scalar(_locked(select(Workspace).where(Workspace.id == destination_workspace_id)))
    beta = await source.get(Workspace, source_workspace_id, populate_existing=True)
    if beta is None or native is None or beta.is_demo or native.is_demo:
        raise ValueError("Transfer requires existing private workspaces")

    preferences = native.preferences or {}
    prior = preferences.get(TRANSFER_KEY)
    binding = [beta.id, beta.slug]
    if prior is not None and prior["source"] != binding:
        raise ValueError("Native workspace is already bound to a different beta workspace")

    questions = {
        str(row.id): _question_values(row)
        for row in await source.scalars(_owned(Question, beta.id).where(Question.is_demo.is_(False)))
    }
    experiences = {
        str(row.id): _values(row, EXPERIENCE_FIELDS)
        for row in await source.scalars(_owned(MemoryItem, beta.id).where(
            MemoryItem.mtype == "InterviewExperience", MemoryItem.source == "USER",
            MemoryItem.status == MemoryItem.STATUS_ACTIVE, MemoryItem.confirmed_at.is_not(None),
        ))
    }
    notes_query = (
        select(StudyNote, StudyNoteVersion)
        .join(StudySession, StudySession.id == StudyNote.origin_session_id)
        .join(StudyNoteVersion, (StudyNoteVersion.id == StudyNote.current_version_id)
              & (StudyNoteVersion.note_id == StudyNote.id))
        .where(StudyNote.workspace_id == beta.id, StudySession.workspace_id == beta.id)
        .order_by(StudyNote.id)
    )
    notes = [
        (str(note.id), note.title, note.updated_at, _values(version, ("title", "body", "author", "model", "created_at")))
        for note, version in await source.execute(notes_query)
    ]
    fingerprint = _fingerprint([beta.target_role, questions, experiences, notes])
    if prior and prior.get("fingerprint") == fingerprint:
        return

    default_role = Workspace.__table__.c.target_role.default.arg
    role = prior["role"] if prior else None
    if prior is None and native.target_role in ("", default_role, beta.target_role):
        role = native.target_role
    if native.target_role == role and beta.target_role:
        native.target_role = role = beta.target_role

    ledger = {
        "source": binding,
        "fingerprint": fingerprint,
        "questions": dict(prior["questions"]) if prior else {},
        "experiences": dict(prior["experiences"]) if prior else {},
        "notes": dict(prior["notes"]) if prior else {},
        "role": role,
    }
    await _transfer_rows(
        destination, questions, Question, native.id, ledger["questions"],
        lambda values: Question(workspace_id=native.id, short_code=f"BT-{uuid4().hex[:29]}", **values),
    )
    await _transfer_rows(
        destination, experiences, MemoryItem, native.id, ledger["experiences"],
        lambda values: MemoryItem(workspace_id=native.id, **values),
    )
    await _withdraw_experiences(destination, native.id, experiences, ledger["experiences"])
    await _transfer_notes(destination, notes, native.id, ledger["notes"])
    native.preferences = {**preferences, TRANSFER_KEY: ledger}
    await destination.flush()
