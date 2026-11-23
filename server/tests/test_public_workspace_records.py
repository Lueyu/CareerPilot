import pytest
from fastapi import HTTPException

from app.api import public_memory
from app.models import Question, Workspace


@pytest.mark.asyncio
async def test_confirmed_experiences_and_feedback_belong_to_their_owner(db_session, test_workspace):
    from uuid import uuid4
    from sqlalchemy import delete

    other = Workspace(slug="public-peer-" + uuid4().hex, name="同行")
    db_session.add(other)
    await db_session.commit()
    try:
        body = public_memory.Experience(text="我负责校园活动拉新。", confirmed=True)
        saved = await public_memory.add_memory(body, db_session, test_workspace)
        assert await public_memory.confirmed_context(db_session, other.id) == []
        assert await public_memory.confirmed_context(db_session, test_workspace.id) == [body.text]
        with pytest.raises(HTTPException) as denied:
            await public_memory.remove_memory(saved["id"], db_session, other)
        assert denied.value.status_code == 404
        updated = public_memory.Experience(text="我负责活动文案。", confirmed=True)
        assert (await public_memory.edit_memory(saved["id"], updated, db_session, test_workspace))["revision"] == 2
        await public_memory.remove_memory(saved["id"], db_session, test_workspace)
        assert await public_memory.confirmed_context(db_session, test_workspace.id) == []

        question = Question(short_code=uuid4().hex, title="如何评估活动效果？", topic="增长", workspace_id=test_workspace.id)
        db_session.add(question)
        await db_session.commit()
        feedback = public_memory.Feedback(question_id=question.id, text="希望能补充活动背景")
        with pytest.raises(HTTPException) as denied:
            await public_memory.submit_feedback(feedback, db_session, other)
        assert denied.value.status_code == 404
        assert (await public_memory.submit_feedback(feedback, db_session, test_workspace))["id"] > 0
    finally:
        await db_session.execute(delete(Workspace).where(Workspace.id == other.id))
        await db_session.commit()
