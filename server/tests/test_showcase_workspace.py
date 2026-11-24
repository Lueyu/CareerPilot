"""Exercise the opt-in workspace installer using only the isolated database."""

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app import showcase
from app.models import Question, Skill, Workspace
from app.seed import SKILLS


@pytest.fixture
def identity(monkeypatch):
    slug = f"showcase-{uuid4().hex}"
    monkeypatch.setattr(showcase, "SLUG", slug)
    monkeypatch.setattr(showcase, "OWNER_EMAIL", f"{slug}@example.invalid")
    return slug


@pytest.mark.asyncio
async def test_second_install_preserves_edits_and_private_question_ids(db_session, identity):
    for key, name, description, _, _ in SKILLS:
        await db_session.execute(insert(Skill).values(key=key, name=name, description=description)
                                 .on_conflict_do_nothing(index_elements=[Skill.key]))
    today = date(2026, 11, 24)
    assert await showcase.install_workspace(db_session, today)
    await db_session.flush()
    workspace = await db_session.scalar(select(Workspace).where(Workspace.slug == identity))
    questions = select(Question.id).where(Question.workspace_id == workspace.id).order_by(Question.id)
    before = list(await db_session.scalars(questions))
    assert len(before) >= 16
    workspace.name = "我的面试记录"
    await db_session.flush()

    assert not await showcase.install_workspace(db_session, today + timedelta(days=9))
    await db_session.refresh(workspace)
    assert workspace.name == "我的面试记录"
    assert list(await db_session.scalars(questions)) == before


@pytest.mark.asyncio
async def test_unrelated_workspace_with_same_slug_is_never_adopted(db_session, identity):
    existing = Workspace(slug=identity, name="既有资料")
    db_session.add(existing)
    await db_session.flush()
    with pytest.raises(ValueError, match="已有同名工作空间"):
        await showcase.install_workspace(db_session, date.today())
    assert existing.name == "既有资料"
    assert await db_session.scalar(select(func.count(Question.id)).where(Question.workspace_id == existing.id)) == 0
