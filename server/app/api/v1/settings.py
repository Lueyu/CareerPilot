"""设置与工作空间 API。

给前端"设置与工作空间"页提供真实数据:
- GET    /settings/overview   账号 + 工作空间 + 成员 + 统计 + 连接状态(只读, 不回显密钥)
- PATCH  /settings/workspace  更新工作空间名称 / 目标岗位 / 目标职级
- GET    /settings/export     导出当前工作空间全部数据(JSON 下载)
- DELETE /settings/workspace  删除工作空间(需要 confirm_slug 确认, 级联删除)

设计原则: 密钥只报告"是否已配置", 绝不回显值; 危险操作要求显式确认字段。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace, require_auth, require_local_owner
from app.config import get_settings
from app.db import get_session
from app.i18n import message as localized_message
from app.models import (
    AppSetting,
    ContentPublication,
    ContentPublicationRule,
    ContentReleaseAudit,
    DecisionRecord,
    Event,
    Evidence,
    Exam,
    ExamAnswer,
    ExamQuestion,
    ImportItem,
    ImportSession,
    LearningTask,
    MemoryItem,
    PublicContentDraft,
    PublicContentSource,
    Question,
    Skill,
    SkillState,
    Trace,
    TraceSpan,
    User,
    Workspace,
    WorkspaceMember,
)
from app.services.github_publish import get_github_token, set_github_token_cache
from app.services.llm import adapter, get_llm_config, set_llm_config_cache
from app.services.llm_endpoint_config import endpoint, resolve_llm_config
from app.services.private_model_billing import BillingScope
from app.services.workspaces import ensure_personal_workspace

router = APIRouter()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _workspace_view(ws: Workspace) -> dict:
    return {
        "id": ws.id, "name": ws.name, "slug": ws.slug,
        "target_role": ws.target_role, "target_level": ws.target_level,
        "is_demo": ws.is_demo, "created_at": _iso(ws.created_at),
    }


@router.get("/settings/workspaces")
async def list_workspaces(session: AsyncSession = Depends(get_session), scope: str | None = Depends(require_auth)):
    cfg = get_settings()
    statement = select(Workspace).order_by(Workspace.is_demo, Workspace.id)
    if isinstance(scope, str):
        statement = statement.where(Workspace.slug == scope)
    elif cfg.workspace_token_hashes:
        statement = statement.where(Workspace.slug.not_in(list(cfg.workspace_token_hashes)))
    rows = (await session.scalars(statement)).all()
    return {"workspaces": [_workspace_view(ws) for ws in rows], "default_slug": scope if isinstance(scope, str) else cfg.default_workspace_slug}


@router.post("/settings/workspaces/personal", dependencies=[Depends(require_local_owner)])
async def create_personal_workspace(session: AsyncSession = Depends(get_session)):
    try:
        ws = await ensure_personal_workspace(session)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.commit()
    return {"workspace": _workspace_view(ws)}


# --------------------------------------------------------------------------
# 工作空间维护: 创建任意空间 / 按 id 编辑 / 按 id 删除(本地 owner)
# --------------------------------------------------------------------------
class WorkspaceCreate(BaseModel):
    name: str
    target_role: str | None = None
    target_level: str | None = None


async def _allocate_slug(session: AsyncSession, name: str) -> str:
    """由名称生成唯一 slug: ASCII 化 -> 截 48 -> 冲突或全中文时追加短随机后缀。"""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]
    if not base:
        return f"ws-{uuid.uuid4().hex[:8]}"
    candidate = base
    for _ in range(8):
        exists = (
            await session.execute(select(Workspace.id).where(Workspace.slug == candidate))
        ).scalar_one_or_none()
        if exists is None:
            return candidate
        candidate = f"{base}-{uuid.uuid4().hex[:8]}"
    return f"ws-{uuid.uuid4().hex[:12]}"


async def _manageable_workspace(session: AsyncSession, workspace_id: int, scope: str | None) -> Workspace:
    """按 id 取工作空间, 并执行与 list 一致的可见性防护:
    - scope 为 str 时只能操作该 slug 的空间;
    - scope 为 None(本地 owner) 时不得操作 token_hashes 里的租户空间。
    """
    cfg = get_settings()
    ws = (
        await session.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one_or_none()
    if ws is None:
        raise HTTPException(status_code=404, detail=localized_message("errors.this_workspace_does_not_exist"))
    if (scope is not None and ws.slug != scope) or (scope is None and ws.slug in cfg.workspace_token_hashes):
        raise HTTPException(status_code=404, detail=localized_message("errors.this_workspace_does_not_exist"))
    return ws


@router.post("/settings/workspaces", dependencies=[Depends(require_local_owner)])
async def create_workspace(
    body: WorkspaceCreate,
    session: AsyncSession = Depends(get_session),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail=localized_message("errors.enter_a_workspace_name"))
    slug = await _allocate_slug(session, name)
    # 本地 owner 身份: 复用 personal 空间的 owner 用户
    personal = await ensure_personal_workspace(session)
    owner = await _owner_user(session, personal)
    ws = Workspace(
        slug=slug,
        name=name,
        target_role=body.target_role,
        target_level=body.target_level,
        is_demo=False,
    )
    session.add(ws)
    await session.flush()
    if owner is not None:
        session.add(WorkspaceMember(workspace_id=ws.id, user_id=owner.id, role="owner"))
    await session.commit()
    return {"workspace": _workspace_view(ws)}


@router.patch("/settings/workspaces/{workspace_id}", dependencies=[Depends(require_local_owner)])
async def update_workspace_by_id(
    workspace_id: int,
    body: WorkspaceUpdate,
    session: AsyncSession = Depends(get_session),
    scope: str | None = Depends(require_auth),
):
    ws = await _manageable_workspace(session, workspace_id, scope)
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=422, detail=localized_message("errors.there_are_no_fields_to_update"))
    for k, v in data.items():
        setattr(ws, k, v)
    await session.commit()
    return {"workspace": _workspace_view(ws)}


@router.delete("/settings/workspaces/{workspace_id}", dependencies=[Depends(require_local_owner)])
async def delete_workspace_by_id(
    workspace_id: int,
    body: WorkspaceDelete,
    session: AsyncSession = Depends(get_session),
    scope: str | None = Depends(require_auth),
):
    ws = await _manageable_workspace(session, workspace_id, scope)
    if body.confirm_slug != ws.slug:
        raise HTTPException(status_code=422, detail=f"确认标识不匹配: 需要输入工作空间标识 '{ws.slug}'")
    await session.execute(delete(Workspace).where(Workspace.id == ws.id))
    await session.commit()
    return {"deleted": ws.slug}


# --------------------------------------------------------------------------
# 概览: 账号 / 工作空间 / 成员 / 统计 / 连接状态
# --------------------------------------------------------------------------
@router.get("/settings/overview")
async def settings_overview(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    cfg = get_settings()

    # 账号与成员(通过 WorkspaceMember 关联 User)
    member_rows = (
        await session.execute(
            select(WorkspaceMember, User)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == ws.id)
            .order_by(WorkspaceMember.role)
        )
    ).all()
    members = [
        {"display_name": u.display_name, "email": u.email, "role": m.role}
        for m, u in member_rows
    ]
    owner = next((m for m in members if m["role"] == "owner"), members[0] if members else None)

    # 统计
    async def count(stmt) -> int:
        return (await session.execute(stmt)).scalar() or 0

    counts = {
        "evidence": await count(select(func.count(Evidence.id)).where(Evidence.workspace_id == ws.id)),
        "memory": await count(select(func.count(MemoryItem.id)).where(MemoryItem.workspace_id == ws.id)),
        "tasks": await count(select(func.count(LearningTask.id)).where(LearningTask.workspace_id == ws.id)),
        "questions": await count(select(func.count(Question.id)).where(Question.workspace_id == ws.id)),
        "imports_review": await count(
            select(func.count(ImportSession.id)).where(
                ImportSession.workspace_id == ws.id, ImportSession.status == ImportSession.STATUS_REVIEW
            )
        ),
        "exams": await count(select(func.count(Exam.id)).where(Exam.workspace_id == ws.id)),
        "drafts": await count(select(func.count(PublicContentDraft.id)).where(PublicContentDraft.workspace_id == ws.id)),
        "publications": await count(select(func.count(ContentPublication.id)).where(ContentPublication.workspace_id == ws.id)),
    }

    return {
        "account": {
            "display_name": owner["display_name"] if owner else None,
            "email": owner["email"] if owner else None,
        },
        "workspace": {
            "id": ws.id,
            "name": ws.name,
            "slug": ws.slug,
            "target_role": ws.target_role,
            "target_level": ws.target_level,
            "is_demo": ws.is_demo,
            "created_at": _iso(ws.created_at),
        },
        "members": members,
        "counts": counts,
        "connections": {
            # LLM 环境默认值，仅用于概览
            "llm": {
                "active": bool(cfg.llm_provider and cfg.llm_api_key),
                "provider": cfg.llm_provider or None,
                "model": cfg.llm_model or None,
            },
            # GitHub 发布: token 已配置即视为已连接
            "github_publish": {
                "active": False,
                "writes_enabled": cfg.public_writes_enabled,
                "target_repo": cfg.portal_target_repo,
                "base_branch": cfg.portal_base_branch,
                "content_dir": cfg.portal_content_dir,
                "auto_merge_default": False,
            },
        },
    }


# --------------------------------------------------------------------------
# 工作空间信息编辑
# --------------------------------------------------------------------------
class WorkspaceUpdate(BaseModel):
    name: str | None = None
    target_role: str | None = None
    target_level: str | None = None


@router.patch("/settings/workspace")
async def update_workspace(
    body: WorkspaceUpdate,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=422, detail=localized_message("errors.there_are_no_fields_to_update"))
    for k, v in data.items():
        setattr(ws, k, v)
    await session.commit()
    return {
        "workspace": {
            "id": ws.id, "name": ws.name, "slug": ws.slug,
            "target_role": ws.target_role, "target_level": ws.target_level,
            "is_demo": ws.is_demo, "created_at": _iso(ws.created_at),
        }
    }


# --------------------------------------------------------------------------
# 数据导出: 当前工作空间全部业务数据 JSON
# --------------------------------------------------------------------------
def _row(obj, fields: list[str]) -> dict:
    out = {}
    for f in fields:
        v = getattr(obj, f, None)
        if isinstance(v, datetime):
            v = v.isoformat()
        out[f] = v
    return out


@router.get("/settings/export")
async def export_workspace(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    wid = ws.id

    async def rows(model, fields: list[str], *conds) -> list[dict]:
        stmt = select(model).where(*conds) if conds else select(model)
        res = (await session.execute(stmt)).scalars().all()
        return [_row(r, fields) for r in res]

    W = lambda model: model.workspace_id == wid  # noqa: E731

    skills = (await session.execute(select(Skill))).scalars().all()
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "workspace": _row(ws, ["id", "slug", "name", "target_role", "target_level", "is_demo", "created_at"]),
        "members": [
            {"display_name": u.display_name, "email": u.email, "role": m.role}
            for m, u in (
                await session.execute(
                    select(WorkspaceMember, User).join(User, User.id == WorkspaceMember.user_id)
                    .where(WorkspaceMember.workspace_id == wid)
                )
            ).all()
        ],
        "skills": [_row(s, ["id", "key", "name", "description", "dimensions"]) for s in skills],
        "skill_states": await rows(SkillState, ["skill_id", "estimated_level", "confidence", "evidence_coverage", "status", "dimension_scores", "last_assessed_at"], W(SkillState)),
        "evidence": await rows(Evidence, ["short_code", "kind", "title", "detail", "grade", "support", "source_ref", "meta", "occurred_at", "created_at"], W(Evidence)),
        "memory": await rows(MemoryItem, ["mtype", "content", "source", "source_ref", "status", "meta", "created_at", "confirmed_at"], W(MemoryItem)),
        "learning_tasks": await rows(LearningTask, ["title", "kind", "status", "acceptance", "est_minutes", "content", "created_at", "completed_at"], W(LearningTask)),
        "exams": await rows(Exam, ["title", "kind", "status", "score", "gained_points", "total_points", "summary", "created_at", "completed_at"], W(Exam)),
    }

    # 考试题/答卷: 经 Exam 关联到工作空间(ExamQuestion/ExamAnswer 无 workspace_id)
    eq_rows = (
        await session.execute(
            select(ExamQuestion).join(Exam, Exam.id == ExamQuestion.exam_id).where(Exam.workspace_id == wid)
        )
    ).scalars().all()
    payload["exam_questions"] = [_row(q, ["id", "exam_id", "order_no", "question_id", "frozen_snapshot"]) for q in eq_rows]
    eq_ids = [q.id for q in eq_rows]
    if eq_ids:
        ea_rows = (await session.execute(select(ExamAnswer).where(ExamAnswer.exam_question_id.in_(eq_ids)))).scalars().all()
        payload["exam_answers"] = [_row(a, ["id", "exam_question_id", "choice_index", "text_answer", "transcript", "points", "max_points", "verdict", "feedback", "evidence_refs"]) for a in ea_rows]
    else:
        payload["exam_answers"] = []
    # 导入明细: 经 ImportSession 关联到工作空间(ImportItem 无 workspace_id)
    is_rows = (
        await session.execute(select(ImportSession).where(ImportSession.workspace_id == wid))
    ).scalars().all()
    is_ids = [s.id for s in is_rows]
    ii_rows = (
        (await session.execute(select(ImportItem).where(ImportItem.session_id.in_(is_ids)))).scalars().all()
        if is_ids else []
    )
    payload["import_items"] = [_row(i, ["id", "session_id", "title", "status", "extract_confidence", "is_duplicate_of"]) for i in ii_rows]
    payload.update({
        "questions": await rows(Question, ["short_code", "title", "qtype", "topic", "difficulty", "company", "role", "level", "occurred_on", "source_type", "tags", "keywords", "created_at"], W(Question)),
        "import_sessions": await rows(ImportSession, ["short_code", "filename", "status", "meta", "created_at"], W(ImportSession)),
        "public_content": {
            "sources": await rows(PublicContentSource, ["kind", "title", "url", "license_status", "license_note", "privacy_scan", "fact_check", "meta", "created_at"], W(PublicContentSource)),
            "drafts": await rows(PublicContentDraft, ["short_code", "title", "slug", "status", "target_repo", "target_path", "privacy_scan_status", "fact_check_status", "license_status", "created_at", "updated_at"], W(PublicContentDraft)),
            "publications": await rows(ContentPublication, ["short_code", "draft_id", "status", "dry_run", "target_repo", "branch", "pr_number", "pr_url", "commit_sha", "build_status", "created_at", "published_at"], W(ContentPublication)),
            "rules": await rows(ContentPublicationRule, ["key", "value", "is_enabled"], W(ContentPublicationRule)),
            "audit": await rows(ContentReleaseAudit, ["action", "draft_id", "publication_id", "detail", "created_at"], W(ContentReleaseAudit)),
        },
        "traces": await rows(Trace, ["short_code", "kind", "title", "status", "duration_ms", "cost_cny", "fail_reason", "started_at", "finished_at"], W(Trace)),
        "decisions": await rows(DecisionRecord, ["short_code", "question", "chosen", "alternatives", "evidence_refs", "rationale", "created_at"], W(DecisionRecord)),
    })

    # span: 经 Trace 关联到工作空间(TraceSpan 无 workspace_id)
    tr_rows = (
        await session.execute(select(Trace).where(Trace.workspace_id == wid))
    ).scalars().all()
    tr_ids = [t.id for t in tr_rows]
    ts_rows = (
        (await session.execute(select(TraceSpan).where(TraceSpan.trace_id.in_(tr_ids)))).scalars().all()
        if tr_ids else []
    )
    payload["trace_spans"] = [_row(s, ["id", "trace_id", "name", "status", "duration_ms", "cost_cny", "detail"]) for s in ts_rows]

    # 事件(append-only, 最近 1000 条)
    ev_rows = (
        await session.execute(select(Event).where(Event.workspace_id == wid).order_by(Event.id.desc()).limit(1000))
    ).scalars().all()
    payload["events"] = [_row(e, ["kind", "payload", "ref_type", "ref_id", "created_at"]) for e in reversed(ev_rows)]

    content = json.dumps(payload, ensure_ascii=False, default=str)
    filename = f"careerpilot-export-{ws.slug}-{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------
# 删除工作空间(危险操作, 需要 confirm_slug 显式确认)
# --------------------------------------------------------------------------
class WorkspaceDelete(BaseModel):
    confirm_slug: str


@router.delete("/settings/workspace")
async def delete_workspace(
    body: WorkspaceDelete,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    if body.confirm_slug != ws.slug:
        raise HTTPException(status_code=422, detail=f"确认标识不匹配: 需要输入工作空间标识 '{ws.slug}'")
    # 所有业务表 workspace_id 外键均为 ondelete=CASCADE, 数据库级联删除
    await session.execute(delete(Workspace).where(Workspace.id == ws.id))
    await session.commit()
    return {"deleted": ws.slug}


# --------------------------------------------------------------------------
# 账号编辑(本地单用户: 直接更新 owner 用户行)
# --------------------------------------------------------------------------
class AccountUpdate(BaseModel):
    display_name: str | None = None
    email: str | None = None


async def _owner_user(session: AsyncSession, ws: Workspace) -> User | None:
    row = (
        await session.execute(
            select(User)
            .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
            .where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.role == "owner")
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        row = (
            await session.execute(
                select(User)
                .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
                .where(WorkspaceMember.workspace_id == ws.id)
                .limit(1)
            )
        ).scalar_one_or_none()
    return row


@router.patch("/settings/account")
async def update_account(
    body: AccountUpdate,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    user = await _owner_user(session, ws)
    if user is None:
        raise HTTPException(status_code=404, detail=localized_message("errors.no_account_record_was_found_for_this_workspace"))
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=422, detail=localized_message("errors.there_are_no_fields_to_update"))
    if "email" in data and ("@" not in data["email"]):
        raise HTTPException(status_code=422, detail=localized_message("errors.enter_a_valid_email_address"))
    for k, v in data.items():
        setattr(user, k, v)
    await session.commit()
    return {"account": {"display_name": user.display_name, "email": user.email}}


# --------------------------------------------------------------------------
# LLM 连接: 设置页在线配置(AppSetting 'llm' 优先, env 兜底), 保存即时生效
# --------------------------------------------------------------------------
class LlmUpdate(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


class LlmTest(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


def _endpoint_key_error(previous: dict, proposed: dict, new_key: str) -> str | None:
    if endpoint(previous) != endpoint(proposed) and not new_key:
        return "更改模型服务商或接口地址时，请填写新端点对应的 API 密钥。"
    if new_key and not all(proposed[field].strip() for field in ("provider", "base_url")):
        return "请填写模型服务商和接口地址，再配置对应的 API 密钥。"
    return None


def _hint(key: str) -> str | None:
    return f"****{key[-4:]}" if key else None


def _llm_configured(config: dict) -> bool:
    return all((config.get(field) or "").strip() for field in ("provider", "api_key", "base_url", "model"))


class LlmReadiness(BaseModel):
    configured: bool


@router.get("/settings/llm/readiness", dependencies=[Depends(require_local_owner)])
async def get_llm_readiness(ws: Workspace = Depends(get_workspace)) -> LlmReadiness | None:
    if ws.is_demo:
        return None
    return LlmReadiness(configured=_llm_configured(await get_llm_config()))


async def _llm_payload() -> dict:
    cfgv = await get_llm_config()
    return {
        "active": _llm_configured(cfgv),
        "provider": cfgv.get("provider") or None,
        "base_url": cfgv.get("base_url") or None,
        "model": cfgv.get("model") or None,
        "api_key_set": bool(cfgv.get("api_key")),
        "source": cfgv.get("source", "none"),
    }


@router.get("/settings/llm", dependencies=[Depends(require_local_owner)])
async def get_llm():
    return await _llm_payload()


@router.put("/settings/llm", dependencies=[Depends(require_local_owner)])
async def update_llm(
    body: LlmUpdate,
    session: AsyncSession = Depends(get_session),
):
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=422, detail=localized_message("errors.there_are_no_fields_to_update"))
    statement = select(AppSetting).where(AppSetting.key == "llm").with_for_update()
    row = (await session.execute(statement)).scalar_one_or_none()
    existing = row.value if row and isinstance(row.value, dict) else {}
    env = get_settings()
    new_key = data.pop("api_key", "").strip()
    updated = {k: v for k, v in {**existing, **data}.items() if v}
    proposed = resolve_llm_config(env, updated)
    error = _endpoint_key_error(resolve_llm_config(env, existing), proposed, new_key)
    if error:
        raise HTTPException(422, error)
    if new_key:
        updated.update(api_key=new_key, provider=proposed["provider"], base_url=proposed["base_url"])
    if row:
        row.value = updated
    else:
        session.add(AppSetting(key="llm", value=updated))
    await session.commit()
    set_llm_config_cache(None)  # 立即重新解析(下次 get_llm_config 查库)
    return await _llm_payload()


@router.delete("/settings/llm", dependencies=[Depends(require_local_owner)])
async def reset_llm(session: AsyncSession = Depends(get_session)):
    """清除 DB 覆盖, 回落到 env 默认配置。"""
    await session.execute(delete(AppSetting).where(AppSetting.key == "llm"))
    await session.commit()
    set_llm_config_cache(None)
    return {"reset": True}


@router.post("/settings/llm/test", dependencies=[Depends(require_local_owner)])
async def test_llm(body: LlmTest, ws: Workspace = Depends(get_workspace)):
    """用「生效配置 + 请求覆盖」真实调用一次 chat, 验证连通性。"""
    current = await get_llm_config()
    data = {k: v for k, v in body.model_dump(exclude_none=True).items() if v.strip()}
    new_key = data.pop("api_key", "").strip()
    cfgv = {**current, **data}
    error = _endpoint_key_error(current, cfgv, new_key)
    cfgv["api_key"] = new_key or current["api_key"]
    if not cfgv["api_key"]:
        error = error or "请为当前模型服务商和接口地址填写对应的 API 密钥，再测试连接。"
    if error:
        return {"ok": False, "latency_ms": 0, "model": cfgv.get("model"), "error": error}
    t0 = time.monotonic()
    result = await adapter.chat_with(cfgv, "你是连接测试助手。", "请只回复两个字符: OK", timeout=30.0, billing_scope=BillingScope(ws.id, "settings:llm_test"))
    latency_ms = int((time.monotonic() - t0) * 1000)
    if result is None:
        return {"ok": False, "latency_ms": latency_ms, "model": cfgv.get("model"), "error": "调用失败(网络/凭证/模型名), 未返回内容"}
    return {"ok": True, "latency_ms": latency_ms, "model": cfgv.get("model")}


# --------------------------------------------------------------------------
# GitHub token: 设置页在线配置(AppSetting 'github' 优先, env 兜底)
# --------------------------------------------------------------------------
class GithubUpdate(BaseModel):
    token: str


async def _github_payload() -> dict:
    token = await get_github_token()
    cfg = get_settings()
    if not token:
        return {"active": False, "token_hint": None, "source": "none", "writes_enabled": False}
    # 判断来源: DB 行存在 → db
    from app.db import SessionLocal

    async with SessionLocal() as s:
        row = (await s.execute(select(AppSetting).where(AppSetting.key == "github"))).scalar_one_or_none()
    return {
        "active": True,
        "writes_enabled": False,
        "token_hint": _hint(token),
        "source": "db" if row else "env",
        "target_repo": cfg.portal_target_repo,
        "base_branch": cfg.portal_base_branch,
        "content_dir": cfg.portal_content_dir,
    }


@router.get("/settings/github", dependencies=[Depends(require_local_owner)])
async def get_github():
    return await _github_payload()


@router.put("/settings/github", dependencies=[Depends(require_local_owner)])
async def update_github(body: GithubUpdate, session: AsyncSession = Depends(get_session)):
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail=localized_message("errors.enter_a_token"))
    row = (await session.execute(select(AppSetting).where(AppSetting.key == "github"))).scalar_one_or_none()
    if not row:
        row = AppSetting(key="github", value={"token": token})
        session.add(row)
    else:
        row.value = {"token": token}
    await session.commit()
    set_github_token_cache(token)
    return await _github_payload()


@router.delete("/settings/github", dependencies=[Depends(require_local_owner)])
async def reset_github(session: AsyncSession = Depends(get_session)):
    await session.execute(delete(AppSetting).where(AppSetting.key == "github"))
    await session.commit()
    set_github_token_cache(None)
    return {"reset": True}

# GitCode project reads use the selected workspace's credential. The personal
# installation continues to support its existing GITCODE_TOKEN fallback.
@router.get("/settings/gitcode")
async def get_gitcode_read_settings(
    ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session),
):
    row = await session.get(AppSetting, f"gitcode:{ws.id}")
    return {"active": bool(row), "writes_enabled": False}


@router.put("/settings/gitcode")
async def update_gitcode_read_settings(
    body: GithubUpdate, ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session),
):
    token = body.token.strip()
    if not token or len(token) > 2048 or any(not 33 <= ord(ch) <= 126 for ch in token):
        raise HTTPException(422, localized_message("errors.invalid_gitcode_token_format"))
    key = f"gitcode:{ws.id}"
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value={"token": token}))
    else:
        row.value = {"token": token}
    await session.commit()
    return {"active": True, "writes_enabled": False}


@router.delete("/settings/gitcode")
async def reset_gitcode_read_settings(
    ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session),
):
    await session.execute(delete(AppSetting).where(AppSetting.key == f"gitcode:{ws.id}"))
    await session.commit()
    return {"active": False, "writes_enabled": False}
