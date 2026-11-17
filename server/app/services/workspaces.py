"""Idempotent local identity setup; never copy demo evidence or learning tasks."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Workspace, WorkspaceMember


async def ensure_personal_workspace(session: AsyncSession) -> Workspace:
    await session.execute(
        insert(Workspace).values(slug="personal", name="个人学习空间", is_demo=False)
        .on_conflict_do_nothing(index_elements=[Workspace.slug])
    )
    workspace = (await session.execute(select(Workspace).where(Workspace.slug == "personal"))).scalar_one()
    if workspace.is_demo:
        raise ValueError("personal workspace is marked as demo; explicit data review required")
    await session.execute(
        insert(User).values(email="personal@local.invalid", display_name="个人用户")
        .on_conflict_do_nothing(index_elements=[User.email])
    )
    user_id = (await session.execute(select(User.id).where(User.email == "personal@local.invalid"))).scalar_one()
    await session.execute(
        insert(WorkspaceMember).values(workspace_id=workspace.id, user_id=user_id, role="owner")
        .on_conflict_do_nothing(index_elements=[WorkspaceMember.workspace_id, WorkspaceMember.user_id])
    )
    await session.flush()
    return workspace
