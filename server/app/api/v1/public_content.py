"""公开内容发布 API: 草稿 CRUD + 校验 + 审核 + 发布运行 + 回滚 + 规则 + 审计。

这是本地私人数据系统与公开静态网站之间的唯一显式边界。所有端点经
get_workspace 强制 Workspace 隔离, 每次发布走 Trace + 幂等键 + 审计。
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.config import get_settings
from app.db import get_session
from app.models import (
    ContentPublication,
    ContentPublicationRule,
    ContentReleaseAudit,
    PublicContentDraft,
    PublicContentSource,
    Workspace,
)
from app.models.public_content import (
    DRAFT_STATUS_APPROVED,
    DRAFT_STATUS_BUILD_FAILED,
    DRAFT_STATUS_BUILDING,
    DRAFT_STATUS_DRAFT,
    DRAFT_STATUS_NEEDS_REVIEW,
    DRAFT_STATUS_PR_CREATED,
    DRAFT_STATUS_PUBLISHED,
    DRAFT_STATUS_REJECTED,
    DRAFT_STATUS_ROLLED_BACK,
    DRAFT_STATUS_VALIDATION_FAILED,
)
from app.services.content_validation import is_low_risk, run_all
from app.services.github_publish import GitHubError, GitHubPublisher, make_publisher_rt
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class SourceIn(BaseModel):
    kind: str = "EXTERNAL_URL"
    title: str = ""
    url: str | None = None
    license_status: str = "UNKNOWN"
    license_note: str | None = None


class DraftCreate(BaseModel):
    title: str = Field(min_length=1)
    slug: str = ""
    description: str | None = None
    markdown_body: str = ""
    json_meta: dict | None = None
    target_repo: str | None = None
    target_path: str | None = None
    sources: list[SourceIn] = Field(default_factory=list)


class DraftUpdate(BaseModel):
    title: str | None = None
    slug: str | None = None
    description: str | None = None
    markdown_body: str | None = None
    json_meta: dict | None = None
    target_repo: str | None = None
    target_path: str | None = None


class RejectIn(BaseModel):
    reason: str = ""


# --------------------------------------------------------------------------
# 序列化辅助
# --------------------------------------------------------------------------
def _slugify(title: str) -> str:
    import re
    s = title.strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", s)
    return s.strip("-") or "-"


def _draft_view(d: PublicContentDraft) -> dict:
    return {
        "id": d.id,
        "short_code": d.short_code,
        "title": d.title,
        "slug": d.slug,
        "description": d.description,
        "markdown_body": d.markdown_body,
        "json_meta": d.json_meta,
        "privacy_scan_status": d.privacy_scan_status,
        "fact_check_status": d.fact_check_status,
        "license_status": d.license_status,
        "validation_errors": d.validation_errors,
        "target_repo": d.target_repo,
        "target_path": d.target_path,
        "status": d.status,
        "reviewed_by": d.reviewed_by,
        "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
        "reject_reason": d.reject_reason,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        "sources": [
            {
                "id": s.id, "kind": s.kind, "title": s.title, "url": s.url,
                "license_status": s.license_status, "license_note": s.license_note,
                "privacy_scan": s.privacy_scan, "fact_check": s.fact_check,
            }
            for s in (d.sources or [])
        ],
    }


def _pub_view(p: ContentPublication) -> dict:
    return {
        "id": p.id,
        "short_code": p.short_code,
        "draft_id": p.draft_id,
        "idempotency_key": p.idempotency_key,
        "dry_run": p.dry_run,
        "target_repo": p.target_repo,
        "branch": p.branch,
        "base_branch": p.base_branch,
        "base_sha": p.base_sha,
        "head_sha": p.head_sha,
        "commit_sha": p.commit_sha,
        "pr_number": p.pr_number,
        "pr_url": p.pr_url,
        "build_status": p.build_status,
        "build_url": p.build_url,
        "status": p.status,
        "error": p.error,
        "rollback_commit_sha": p.rollback_commit_sha,
        "rollback_to_publication_id": p.rollback_to_publication_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "published_at": p.published_at.isoformat() if p.published_at else None,
    }


async def _next_code(session: AsyncSession, model, prefix: str) -> str:
    n = (await session.execute(select(func.count()).select_from(model))).scalar() or 0
    return f"{prefix}-{n + 1}"


async def _audit(session: AsyncSession, ws: Workspace, action: str, draft_id=None, publication_id=None, detail=None):
    session.add(ContentReleaseAudit(
        workspace_id=ws.id, draft_id=draft_id, publication_id=publication_id,
        action=action, detail=detail,
    ))


async def _get_draft(session: AsyncSession, ws: Workspace, draft_id: int) -> PublicContentDraft:
    d = (
        await session.execute(
            select(PublicContentDraft).where(
                PublicContentDraft.id == draft_id,
                PublicContentDraft.workspace_id == ws.id,
            )
        )
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    return d


# --------------------------------------------------------------------------
# 草稿 CRUD
# --------------------------------------------------------------------------
@router.get("/public-content/drafts")
async def list_drafts(
    status: str | None = None,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(PublicContentDraft).where(PublicContentDraft.workspace_id == ws.id)
    if status:
        stmt = stmt.where(PublicContentDraft.status == status)
    stmt = stmt.order_by(PublicContentDraft.id.desc())
    drafts = (await session.execute(stmt)).scalars().all()
    return {"items": [_draft_view(d) for d in drafts]}


@router.post("/public-content/drafts")
async def create_draft(
    body: DraftCreate,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    slug = body.slug or _slugify(body.title)
    # 同 workspace 下 slug 唯一
    exists = (
        await session.execute(
            select(PublicContentDraft).where(
                PublicContentDraft.workspace_id == ws.id,
                PublicContentDraft.slug == slug,
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"slug '{slug}' 已存在")
    d = PublicContentDraft(
        short_code=await _next_code(session, PublicContentDraft, "CT"),
        workspace_id=ws.id, title=body.title, slug=slug,
        description=body.description, markdown_body=body.markdown_body,
        json_meta=body.json_meta, target_repo=body.target_repo,
        target_path=body.target_path, status=DRAFT_STATUS_DRAFT,
    )
    session.add(d)
    await session.flush()
    for s in body.sources:
        session.add(PublicContentSource(
            workspace_id=ws.id, draft_id=d.id, kind=s.kind, title=s.title,
            url=s.url, license_status=s.license_status, license_note=s.license_note,
        ))
    await _audit(session, ws, "CREATE", draft_id=d.id, detail={"slug": slug})
    await session.commit()
    # 重新查询以触发 relationship 的 selectin eager load(新建对象直接访问 sources 会触发同步懒加载)
    d = await _get_draft(session, ws, d.id)
    return _draft_view(d)


@router.get("/public-content/drafts/{draft_id}")
async def get_draft(
    draft_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    return _draft_view(await _get_draft(session, ws, draft_id))


@router.patch("/public-content/drafts/{draft_id}")
async def update_draft(
    draft_id: int,
    body: DraftUpdate,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    d = await _get_draft(session, ws, draft_id)
    if d.status not in (DRAFT_STATUS_DRAFT, DRAFT_STATUS_VALIDATION_FAILED, DRAFT_STATUS_REJECTED, DRAFT_STATUS_PUBLISHED, DRAFT_STATUS_PR_CREATED, DRAFT_STATUS_ROLLED_BACK, DRAFT_STATUS_BUILD_FAILED):
        raise HTTPException(status_code=409, detail=f"状态 {d.status} 不可编辑")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(d, k, v)
    if "status" not in data:
        d.status = DRAFT_STATUS_DRAFT  # 编辑后回到草稿态, 需重新校验(可开启新版本)
    await session.commit()
    d = await _get_draft(session, ws, d.id)  # reload: updated_at 有 onupdate, commit 后需重读
    return _draft_view(d)


@router.delete("/public-content/drafts/{draft_id}")
async def delete_draft(
    draft_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    d = await _get_draft(session, ws, draft_id)
    if d.status not in (DRAFT_STATUS_DRAFT, DRAFT_STATUS_VALIDATION_FAILED, DRAFT_STATUS_REJECTED):
        raise HTTPException(status_code=409, detail=f"状态 {d.status} 不可删除")
    await session.delete(d)
    await _audit(session, ws, "DELETE", draft_id=draft_id)
    await session.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# 校验 / 审核
# --------------------------------------------------------------------------
@router.post("/public-content/drafts/{draft_id}/validate")
async def validate_draft(
    draft_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    d = await _get_draft(session, ws, draft_id)
    sources = [
        {"license_status": s.license_status, "title": s.title, "url": s.url}
        for s in (d.sources or [])
    ]
    result = run_all(d.markdown_body, sources)
    d.validation_errors = result.errors
    d.privacy_scan_status = _reduce(result.errors, "privacy", "FAIL", "FAIL", "PASS")
    d.fact_check_status = _reduce(result.errors, "fact", "FAIL", "WARN", "PASS")
    d.license_status = _reduce(result.errors, "license", "RESTRICTED", "UNKNOWN", "CLEAR")
    # 状态机: FAIL → VALIDATION_FAILED; 否则 → NEEDS_REVIEW
    low_risk = is_low_risk(result)
    if result.status == "FAIL":
        d.status = DRAFT_STATUS_VALIDATION_FAILED
    else:
        d.status = DRAFT_STATUS_NEEDS_REVIEW
    await _audit(session, ws, "VALIDATE", draft_id=d.id, detail={"status": result.status, "low_risk": low_risk})
    await session.commit()
    d = await _get_draft(session, ws, d.id)  # reload: updated_at 有 onupdate
    return {**_draft_view(d), "validation_summary": result.status, "low_risk": low_risk}


@router.post("/public-content/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    d = await _get_draft(session, ws, draft_id)
    if d.status != DRAFT_STATUS_NEEDS_REVIEW:
        raise HTTPException(status_code=409, detail=f"状态 {d.status} 不可审核(需 NEEDS_REVIEW)")
    d.status = DRAFT_STATUS_APPROVED
    d.reviewed_by = "workspace-owner"
    d.reviewed_at = dt.datetime.now(dt.timezone.utc)
    await _audit(session, ws, "APPROVE", draft_id=d.id)
    await session.commit()
    d = await _get_draft(session, ws, d.id)  # reload: updated_at 有 onupdate
    return _draft_view(d)


@router.post("/public-content/drafts/{draft_id}/reject")
async def reject_draft(
    draft_id: int,
    body: RejectIn,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    d = await _get_draft(session, ws, draft_id)
    if d.status != DRAFT_STATUS_NEEDS_REVIEW:
        raise HTTPException(status_code=409, detail=f"状态 {d.status} 不可拒绝(需 NEEDS_REVIEW)")
    d.status = DRAFT_STATUS_REJECTED
    d.reject_reason = body.reason
    await _audit(session, ws, "REJECT", draft_id=d.id, detail={"reason": body.reason})
    await session.commit()
    d = await _get_draft(session, ws, d.id)  # reload: updated_at 有 onupdate
    return _draft_view(d)


# --------------------------------------------------------------------------
# 发布运行
# --------------------------------------------------------------------------
@router.post("/public-content/drafts/{draft_id}/publish")
async def publish_draft(
    draft_id: int,
    dry_run: bool = Query(False),
    auto_merge: bool | None = Query(None),
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    if dry_run is not True:
        raise HTTPException(403, "public_writes_disabled: 仅允许本地 dry-run 预览")
    d = await _get_draft(session, ws, draft_id)
    if d.status != DRAFT_STATUS_APPROVED:
        raise HTTPException(status_code=409, detail=f"状态 {d.status} 不可发布(需 APPROVED)")
    settings = get_settings()
    repo = d.target_repo or settings.portal_target_repo
    base_branch = settings.portal_base_branch
    content_dir = settings.portal_content_dir.rstrip("/")
    path = d.target_path or f"{content_dir}/{d.slug}.md"

    # 幂等键: 同一草稿 + 内容指纹, 同一版本只发一次(dry-run 与真实发布分属不同幂等空间)
    fingerprint = await _content_fingerprint(d)
    idem_key = f"{'dryrun' if dry_run else 'publish'}-draft-{d.id}-{fingerprint}"
    dup = (
        await session.execute(
            select(ContentPublication).where(ContentPublication.idempotency_key == idem_key)
        )
    ).scalar_one_or_none()
    if dup:
        return {**_pub_view(dup), "duplicate": True}

    # 规则: auto_merge
    do_auto_merge = auto_merge if auto_merge is not None else settings.portal_auto_merge
    if do_auto_merge:
        rule = (
            await session.execute(
                select(ContentPublicationRule).where(
                    ContentPublicationRule.workspace_id == ws.id,
                    ContentPublicationRule.key == "auto_merge",
                )
            )
        ).scalar_one_or_none()
        if rule is not None:
            do_auto_merge = rule.is_enabled

    # Trace
    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "public_content_publish", f"发布公开内容: {d.title}", short_code=f"TR-P{int(t0)}")
    pub = ContentPublication(
        short_code=await _next_code(session, ContentPublication, "PB"),
        workspace_id=ws.id, draft_id=d.id, trace_id=trace.id,
        idempotency_key=idem_key, dry_run=dry_run,
        target_repo=repo, branch="", base_branch=base_branch,
        status="CREATED", markdown_snapshot=d.markdown_body, json_snapshot=d.json_meta,
    )
    session.add(pub)
    await session.flush()

    try:
        publisher = await make_publisher_rt(dry_run=dry_run)
        # 1) base sha(只读)
        base_sha = await publisher.get_base_sha(repo, base_branch)
        pub.base_sha = base_sha
        add_span(session, trace, "base_sha", 10, detail={"repo": repo, "branch": base_branch, "sha": base_sha})

        # 2) 分支
        branch = f"content/{d.slug[:40]}-{pub.short_code.lower()}"
        pub.branch = branch
        await publisher.create_branch(repo, branch, base_sha)
        add_span(session, trace, "create_branch", 20, detail={"branch": branch})

        # 3) 写文件(若已存在则需 sha)
        existing = await publisher.get_file(repo, path, base_branch)
        write = await publisher.write_file(
            repo, path, d.markdown_body, branch,
            f"content(publish): {d.title}", sha=(existing["sha"] if existing else None),
        )
        add_span(session, trace, "write_file", 30, detail={"path": path, "sha": write.get("content", {}).get("sha") if isinstance(write, dict) else None})

        # 4) head sha
        head_sha = None
        if not dry_run and isinstance(write, dict):
            head_sha = write.get("commit", {}).get("sha")
        pub.head_sha = head_sha

        # 5) PR
        title = f"content: {d.title}"
        body = f"由 CareerPilot 发布\n\n- 内容: {d.title}\n- slug: {d.slug}\n- 草稿: {d.short_code}\n- 工作空间: {ws.slug}\n"
        pr = await publisher.create_pr(repo, branch, base_branch, title, body)
        if not dry_run:
            pub.pr_number = pr.get("number")
            pub.pr_url = pr.get("html_url")
            pub.head_sha = pr.get("head", {}).get("sha") if pub.head_sha is None else pub.head_sha
        add_span(session, trace, "create_pr", 40, detail={"pr": pub.pr_number, "url": pub.pr_url})

        # 6) 构建追踪(有 workflow 才有 run, 否则查 commit status)
        if not dry_run and pub.head_sha:
            runs = await publisher.get_workflow_runs(repo, pub.head_sha)
            if runs:
                latest = max(runs, key=lambda r: r.get("created_at") or "")
                pub.build_status = latest.get("status", "PENDING").upper()
                pub.build_url = latest.get("html_url")
            else:
                status = await publisher.get_commit_status(repo, pub.head_sha)
                # combined status: state ∈ pending/success/failure
                state = status.get("state")
                pub.build_status = (state or "NONE").upper() if state else "NONE"
            add_span(session, trace, "build_check", 50, detail={"build_status": pub.build_status, "url": pub.build_url})

        # 7) 自动合并(低风险 + 规则开启 + 非 dry_run)
        if do_auto_merge and not dry_run and pub.pr_number and is_low_risk_result(d):
            merged = await publisher.merge_pr(repo, pub.pr_number)
            if merged.get("merged"):
                pub.commit_sha = merged.get("sha")
                pub.status = "PUBLISHED"
                pub.published_at = dt.datetime.now(dt.timezone.utc)
                d.status = DRAFT_STATUS_PUBLISHED
                add_span(session, trace, "merge_pr", 60, detail={"merged_sha": pub.commit_sha})
        else:
            pub.status = "PR_CREATED" if not dry_run else "DRY_RUN"
            d.status = DRAFT_STATUS_PR_CREATED if not dry_run else DRAFT_STATUS_APPROVED

        await finish_trace(session, trace, "SUCCESS", int((time.perf_counter() - t0) * 1000), 0.0)
        await emit_event(session, ws.id, "public_content.published", {"draft_id": d.id, "publication": pub.short_code, "dry_run": dry_run}, "publication", pub.id)
        await _audit(session, ws, "PUBLISH", draft_id=d.id, publication_id=pub.id, detail={"dry_run": dry_run, "pr": pub.pr_number, "status": pub.status})
        await session.commit()
        return _pub_view(pub)
    except GitHubError as e:
        pub.status = "FAILED"
        pub.error = str(e)
        await finish_trace(session, trace, "FAILED", int((time.perf_counter() - t0) * 1000), 0.0, fail_reason=str(e))
        await _audit(session, ws, "PUBLISH_FAILED", draft_id=d.id, publication_id=pub.id, detail={"error": str(e)})
        await session.commit()
        raise HTTPException(status_code=502, detail=f"GitHub 发布失败: {e}")


@router.get("/public-content/publications")
async def list_publications(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    pubs = (
        await session.execute(
            select(ContentPublication).where(ContentPublication.workspace_id == ws.id).order_by(ContentPublication.id.desc())
        )
    ).scalars().all()
    return {"items": [_pub_view(p) for p in pubs]}


@router.get("/public-content/publications/{pub_id}")
async def get_publication(
    pub_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    p = (
        await session.execute(
            select(ContentPublication).where(ContentPublication.id == pub_id, ContentPublication.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail=f"publication {pub_id} not found")
    return _pub_view(p)


@router.post("/public-content/publications/{pub_id}/refresh-build")
async def refresh_build(
    pub_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """刷新构建状态(查 GitHub Actions runs / commit status)。"""
    p = (
        await session.execute(
            select(ContentPublication).where(ContentPublication.id == pub_id, ContentPublication.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail=f"publication {pub_id} not found")
    if not p.head_sha:
        return _pub_view(p)
    publisher = await make_publisher_rt()
    runs = await publisher.get_workflow_runs(p.target_repo, p.head_sha)
    if runs:
        latest = max(runs, key=lambda r: r.get("created_at") or "")
        p.build_status = latest.get("status", "PENDING").upper()
        p.build_url = latest.get("html_url")
    else:
        status = await publisher.get_commit_status(p.target_repo, p.head_sha)
        state = status.get("state")
        p.build_status = (state or "NONE").upper() if state else "NONE"
    await session.commit()
    return _pub_view(p)


@router.post("/public-content/publications/{pub_id}/rollback")
async def rollback_publication(
    pub_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """回滚: 恢复到上一个已发布版本, 建回滚分支 + PR(不自动合并)。"""
    raise HTTPException(403, "public_writes_disabled: 本轮关闭远程回滚")


# --------------------------------------------------------------------------
# 规则 / 审计
# --------------------------------------------------------------------------
class RuleIn(BaseModel):
    key: str
    value: dict | None = None
    is_enabled: bool = True


@router.get("/public-content/rules")
async def get_rules(ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(ContentPublicationRule).where(ContentPublicationRule.workspace_id == ws.id)
        )
    ).scalars().all()
    return {"items": [{"id": r.id, "key": r.key, "value": r.value, "is_enabled": r.is_enabled} for r in rows]}


@router.put("/public-content/rules")
async def put_rule(
    body: RuleIn,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    r = (
        await session.execute(
            select(ContentPublicationRule).where(
                ContentPublicationRule.workspace_id == ws.id,
                ContentPublicationRule.key == body.key,
            )
        )
    ).scalar_one_or_none()
    if r is None:
        r = ContentPublicationRule(workspace_id=ws.id, key=body.key)
        session.add(r)
    r.value = body.value
    r.is_enabled = body.is_enabled
    await session.commit()
    return {"id": r.id, "key": r.key, "value": r.value, "is_enabled": r.is_enabled}


@router.get("/public-content/audit")
async def list_audit(ws: Workspace = Depends(get_workspace), session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(ContentReleaseAudit).where(ContentReleaseAudit.workspace_id == ws.id).order_by(ContentReleaseAudit.id.desc()).limit(200)
        )
    ).scalars().all()
    return {"items": [
        {"id": a.id, "draft_id": a.draft_id, "publication_id": a.publication_id,
         "action": a.action, "detail": a.detail, "created_at": a.created_at.isoformat() if a.created_at else None}
        for a in rows
    ]}


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _reduce(errors: list[dict], check: str, error_val: str, warn_val: str, pass_val: str) -> str:
    """把校验 errors 里某 check 的 severity 归约成状态值。"""
    levels = [e["level"] for e in errors if e.get("check") == check]
    if "error" in levels:
        return error_val
    if "warning" in levels:
        return warn_val
    return pass_val


async def _content_fingerprint(d: PublicContentDraft) -> str:
    import hashlib
    raw = (d.markdown_body or "") + (d.title or "") + (d.slug or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def is_low_risk_result(d: PublicContentDraft) -> bool:
    """低风险判定: 隐私/事实/许可均无 error 且无 warning(即校验 PASS)。"""
    return d.privacy_scan_status not in ("FAIL",) and d.fact_check_status not in ("FAIL", "WARN") and d.license_status not in ("RESTRICTED", "UNKNOWN")
