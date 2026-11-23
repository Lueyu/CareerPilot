import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.public_memory import confirmed_context
from app.models import Base, MemoryItem, Question, StudySession, Workspace
from app.models.study_notes import StudyNote, StudyNoteVersion
from app.services.native_workspace_transfer import TRANSFER_KEY, transfer_native_workspace


@pytest.mark.asyncio
async def test_beta_transfer_is_private_retryable_and_preserves_native_changes(db_session):
    source_url = os.environ.get("CAREERPILOT_TRANSFER_SOURCE_TEST_URL")
    if not source_url:
        pytest.skip("Set CAREERPILOT_TRANSFER_SOURCE_TEST_URL to a separate synthetic database")
    parsed = make_url(source_url)
    assert parsed.database.endswith("_test")
    assert parsed.database != db_session.bind.url.database
    engine = create_async_engine(source_url)
    suffix = uuid4().hex
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine, expire_on_commit=False) as source:
            beta = Workspace(slug=f"beta-{suffix}", name="Beta", target_role="AI engineer")
            peer = Workspace(slug=f"peer-{suffix}", name="Peer")
            native = Workspace(slug=f"native-{suffix}", name="Native", target_role=beta.target_role)
            source.add_all([beta, peer])
            db_session.add(native)
            await source.flush()
            await db_session.commit()
            first = Question(short_code=f"A-{suffix[:30]}", workspace_id=beta.id, title="Original", topic="rag",
                             answer_html="<pre>Answer</pre>",
                             generation_meta={"library_answer": "Answer", "source_item_id": 90})
            second = Question(short_code=f"B-{suffix[:30]}", workspace_id=beta.id, title="Second", topic="rag")
            unrelated = Question(short_code=f"C-{suffix[:30]}", workspace_id=peer.id, title="Peer", topic="rag")
            shared = Question(short_code=f"D-{suffix[:30]}", title="Shared", topic="rag")
            now = datetime.now(timezone.utc)
            experience = MemoryItem(workspace_id=beta.id, mtype="InterviewExperience", content="Confirmed",
                                    source="USER", confirmed_at=now)
            independent = MemoryItem(workspace_id=beta.id, mtype="InterviewExperience", content="To edit",
                                     source="USER", confirmed_at=now)
            pending = MemoryItem(workspace_id=beta.id, mtype="InterviewExperience", content="Pending", source="USER")
            origin = StudySession(workspace_id=beta.id, title="Beta lesson", status="ended")
            source.add_all([first, second, unrelated, shared, experience, independent, pending, origin])
            await source.flush()
            note = StudyNote(workspace_id=beta.id, origin_session_id=origin.id, title="Saved")
            source.add(note)
            await source.flush()
            version = StudyNoteVersion(note_id=note.id, title="Saved", body="Beta body", author="USER",
                                       sources=[], session_ids=[origin.id], coverage=[42])
            source.add(version)
            await source.flush()
            note.current_version_id = version.id
            await source.commit()

            async def sync():
                await transfer_native_workspace(source, db_session, beta.id, native.id)
                await db_session.commit()

            await sync()
            await sync()
            rows = await db_session.scalars(select(Question).where(Question.workspace_id == native.id))
            imported = {row.title: row for row in rows}
            assert imported.keys() == {"Original", "Second"}
            assert imported["Original"].answer_html == "<pre>Answer</pre>"
            assert imported["Original"].generation_meta == {"library_answer": "Answer"}
            assert native.target_role == "AI engineer"
            memories = await db_session.scalars(select(MemoryItem).where(MemoryItem.workspace_id == native.id))
            projected = {row.content: row for row in memories}
            assert projected.keys() == {"Confirmed", "To edit"}
            native_note = await db_session.scalar(select(StudyNote).where(StudyNote.workspace_id == native.id))
            native_version = await db_session.get(StudyNoteVersion, native_note.current_version_id)
            assert native_version.body == "Beta body"
            assert native_version.session_ids == []
            assert (await db_session.get(StudySession, native_note.origin_session_id)).hidden_at is not None

            first.title = "Beta update"
            beta.target_role = "Beta role update"
            experience.content = "Beta experience update"
            await source.commit()
            await sync()
            assert imported["Original"].title == "Beta update"
            assert native.target_role == "Beta role update"
            assert projected["Confirmed"].content == "Beta experience update"
            projected["To edit"].content = "Native experience edit"
            native.target_role = "Native role edit"
            imported["Original"].title = "Native edit"
            await db_session.execute(delete(Question).where(Question.id == imported["Second"].id))
            await db_session.commit()

            first.title = "Another beta edit"
            second.title = "Beta changed a deleted question"
            beta.target_role = "Another beta role"
            await source.delete(experience)
            await source.delete(independent)
            await source.commit()
            await sync()
            rows = await db_session.scalars(select(Question).where(Question.workspace_id == native.id))
            assert [row.title for row in rows] == ["Native edit"]
            assert native.target_role == "Native role edit"
            assert len(native.preferences[TRANSFER_KEY]["questions"]) == 2
            assert projected["Confirmed"].status == MemoryItem.STATUS_DISMISSED
            assert await confirmed_context(db_session, native.id) == ["Native experience edit"]
            await sync()
            assert await confirmed_context(db_session, native.id) == ["Native experience edit"]

            with pytest.raises(ValueError, match="already bound"):
                await transfer_native_workspace(source, db_session, peer.id, native.id)
            await db_session.rollback()
    finally:
        await engine.dispose()
