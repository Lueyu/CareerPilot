"""Workspace maintenance: create / edit by id / delete by id (local owner)."""
from uuid import uuid4

import httpx
import pytest
from credential_fixtures import enroll_token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Question, User, Workspace, WorkspaceMember


@pytest.mark.asyncio
async def test_local_owner_can_create_edit_and_delete_workspaces(db_session, test_workspace, monkeypatch):
    db = db_session
    _fixture_ws = test_workspace
    owner_token = "owner-" + uuid4().hex
    cfg = get_settings()
    monkeypatch.setattr(cfg, "workspace_token_hashes", {})
    monkeypatch.setattr(cfg, "api_token", owner_token)
    await enroll_token(db, owner_token)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as conn:
            yield conn

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)

    headers = {"X-Api-Token": owner_token}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            # 未认证 401
            assert (await client.get("/settings/workspaces")).status_code == 401

            # 创建
            created = await client.post(
                "/settings/workspaces",
                headers=headers,
                json={"name": "  新空间  ", "target_role": "AI Agent 工程师", "target_level": "P6"},
            )
            assert created.status_code == 200, created.text
            ws = created.json()["workspace"]
            assert ws["name"] == "新空间"
            assert ws["slug"].startswith("ws-") and len(ws["slug"]) == 11  # 中文名称 -> ws-<8hex>
            assert ws["target_role"] == "AI Agent 工程师"
            assert ws["target_level"] == "P6"
            assert ws["is_demo"] is False
            assert "personal" not in ws["slug"]

            # owner 成员建立
            row = (
                await db.execute(
                    select(User).join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                    .where(WorkspaceMember.workspace_id == ws["id"], WorkspaceMember.role == "owner")
                )
            ).scalar_one_or_none()
            assert row is not None
            owner_user = row

            # 列表包含新空间
            listing = (await client.get("/settings/workspaces", headers=headers)).json()["workspaces"]
            assert any(item["id"] == ws["id"] for item in listing)

            # 空名称 422
            blank = await client.post("/settings/workspaces", headers=headers, json={"name": "   "})
            assert blank.status_code == 422, blank.text

            # 重名自动分配唯一 slug
            again = await client.post("/settings/workspaces", headers=headers, json={"name": "新空间"})
            assert again.status_code == 200
            assert again.json()["workspace"]["slug"] != ws["slug"]

            # 编辑
            updated = await client.patch(
                f"/settings/workspaces/{ws['id']}",
                headers=headers,
                json={"name": "改名后", "target_role": "SRE", "target_level": "P7"},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["workspace"]["name"] == "改名后"
            assert updated.json()["workspace"]["target_role"] == "SRE"
            fresh = (
                await db.execute(
                    select(Workspace)
                    .where(Workspace.id == ws["id"])
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            assert fresh.name == "改名后" and fresh.target_role == "SRE" and fresh.target_level == "P7"

            # 空编辑 422
            empty = await client.patch(f"/settings/workspaces/{ws['id']}", headers=headers, json={})
            assert empty.status_code == 422, empty.text

            # 不存在 id 404
            missing = await client.patch("/settings/workspaces/999999", headers=headers, json={"name": "x"})
            assert missing.status_code == 404, missing.text

            # 删除: 确认标识不匹配 422
            bad_delete = await client.request(
                "DELETE", f"/settings/workspaces/{ws['id']}", headers=headers, json={"confirm_slug": "wrong"}
            )
            assert bad_delete.status_code == 422, bad_delete.text

            # 删除: 级联清理业务数据
            q = Question(workspace_id=ws["id"], short_code=("del-" + uuid4().hex)[:32], title="to delete", topic="backend")
            db.add(q)
            await db.commit()

            deleted = await client.request(
                "DELETE", f"/settings/workspaces/{ws['id']}", headers=headers, json={"confirm_slug": ws["slug"]}
            )
            # 删除用 DB 中真实 slug(改名后仍为原 slug)
            assert deleted.status_code == 200, deleted.text
            assert (
                await db.execute(select(func.count()).select_from(Workspace).where(Workspace.id == ws["id"]))
            ).scalar() == 0
            assert (await db.execute(select(func.count()).select_from(Question).where(Question.id == q.id))).scalar() == 0
            assert (
                await db.execute(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == ws["id"]))
            ).scalar() == 0
            # owner 用户自身保留(由 personal 空间维护)
            assert (await db.get(User, owner_user.id)) is not None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_scoped_tenant_cannot_create_or_manage_other_workspaces(db_session, test_workspace, monkeypatch):
    db = db_session
    _fixture_ws = test_workspace
    other = Workspace(slug="tenant-" + uuid4().hex, name="tenant space", is_demo=False)
    db.add(other)
    await db.commit()
    a_token, owner_token = "a-" + uuid4().hex, "owner-" + uuid4().hex
    cfg = get_settings()
    await enroll_token(db, a_token, other.slug)
    monkeypatch.setattr(cfg, "api_token", owner_token)
    await enroll_token(db, owner_token)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)

    async def request_db():
        async with factory() as conn:
            yield conn

    monkeypatch.setitem(app.dependency_overrides, get_session, request_db)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture/api/v1") as client:
            # 租户 token: 列表只见自己空间
            scoped = {"X-Api-Token": a_token}
            listing = (await client.get("/settings/workspaces", headers=scoped)).json()["workspaces"]
            assert [w["slug"] for w in listing] == [other.slug]

            # 租户不能创建空间(local owner required)
            assert (await client.post("/settings/workspaces", headers=scoped, json={"name": "x"})).status_code == 403

            # 租户不能编辑/删除其他空间(local owner required 403)
            assert (await client.patch(f"/settings/workspaces/{other.id}", headers=scoped, json={"name": "hack"})).status_code == 403
            assert (await client.request(
                "DELETE", f"/settings/workspaces/{other.id}", headers=scoped, json={"confirm_slug": other.slug}
            )).status_code == 403

            # 本地 owner 也不能操作租户空间(404)
            owner = {"X-Api-Token": owner_token}
            assert (await client.patch(f"/settings/workspaces/{other.id}", headers=owner, json={"name": "hack"})).status_code == 404
            assert (await client.request(
                "DELETE", f"/settings/workspaces/{other.id}", headers=owner, json={"confirm_slug": other.slug}
            )).status_code == 404
    finally:
        app.dependency_overrides.clear()
