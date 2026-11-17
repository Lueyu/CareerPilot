"""职业资产 API: 资产同步 / 更新流水线 / 审批发布(复用 public_content)。"""
import hashlib
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.api.v1.public_content import _next_code, publish_draft
from app.db import get_session
from app.models.project import AssetUpdateRun, CareerAsset, Project, ProjectFinding, ProjectScan
from app.services.project_analyzer import coverage_for_scan
from app.models.public_content import ContentPublication, PublicContentDraft
from app.models.trace import Trace
from app.models.workspace import Workspace
from app.services.asset_pipeline import create_asset_update
from app.services.github_repo import GitHubRepoError, fetch_file_text, get_github_token
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()


def _run_view(r: AssetUpdateRun, draft: PublicContentDraft | None, pub: ContentPublication | None, project: Project | None) -> dict:
    return {
        "id": r.id,
        "short_code": r.short_code,
        "project": {"id": project.id, "name": project.name, "owner": project.owner, "repo": project.repo} if project else None,
        "status": r.status,
        "risk_level": r.risk_level,
        "risk_reason": r.risk_reason,
        "diff_markdown": r.diff_markdown,
        "draft": {
            "id": draft.id, "short_code": draft.short_code, "title": draft.title,
            "slug": draft.slug, "status": draft.status,
            "privacy_scan_status": draft.privacy_scan_status,
            "fact_check_status": draft.fact_check_status,
            "validation_errors": draft.validation_errors,
        } if draft else None,
        "publication": {
            "id": pub.id, "short_code": pub.short_code, "status": pub.status,
            "pr_number": pub.pr_number, "pr_url": pub.pr_url,
            "build_status": pub.build_status, "commit_sha": pub.commit_sha,
        } if pub else None,
        "stats": r.stats,
        "error": r.error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def _get_run(session: AsyncSession, ws: Workspace, run_id: int) -> AssetUpdateRun:
    r = (
        await session.execute(select(AssetUpdateRun).where(AssetUpdateRun.id == run_id, AssetUpdateRun.workspace_id == ws.id))
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail=f"asset update run {run_id} not found")
    return r


@router.get("/career-assets")
async def list_assets(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    items = (
        await session.execute(select(CareerAsset).where(CareerAsset.workspace_id == ws.id).order_by(CareerAsset.id))
    ).scalars().all()
    return {
        "items": [
            {
                "id": a.id, "kind": a.kind, "source_url": a.source_url,
                "content_path": a.content_path, "content_hash": a.content_hash,
                "synced_at": a.synced_at.isoformat() if a.synced_at else None,
            }
            for a in items
        ]
    }


@router.post("/career-assets/sync")
async def sync_assets(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """同步线上职业展示: 从门户仓库读 content 目录文件, 记录 hash 与路径(只读)。"""
    from app.config import get_settings
    settings = get_settings()
    repo = settings.portal_target_repo  # owner/repo
    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "asset_sync", f"同步职业资产: {repo}", short_code=f"TR-AS{int(t0)}")
    owner, _, name = repo.partition("/")
    synced, errors = [], []
    try:
        from app.services.github_repo import fetch_tree
        tree = await fetch_tree(owner, name, get_github_token(), None)
        content_files = [
            it["path"] for it in tree
            if it["type"] == "blob" and it["path"].endswith((".md", ".json")) and "/" in it["path"]
        ][:20]
        for path in content_files:
            text = await fetch_file_text(owner, name, path, get_github_token(), None)
            if text is None:
                continue
            h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            existing = (
                await session.execute(
                    select(CareerAsset).where(CareerAsset.workspace_id == ws.id, CareerAsset.content_path == path)
                )
            ).scalars().first()
            if existing:
                existing.content_hash = h
                existing.synced_at = trace.started_at
                existing.source_url = f"https://github.com/{repo}/blob/main/{path}"
            else:
                session.add(CareerAsset(
                    workspace_id=ws.id, kind="WEBSITE_PAGE",
                    source_url=f"https://github.com/{repo}/blob/main/{path}",
                    content_path=path, content_hash=h, synced_at=trace.started_at,
                ))
            synced.append(path)
        add_span(session, trace, "sync", int((time.perf_counter() - t0) * 1000), detail={"files": len(synced)})
        await finish_trace(session, trace, status="SUCCESS", duration_ms=int((time.perf_counter() - t0) * 1000))
        await session.commit()
    except GitHubRepoError as e:
        errors.append(f"GitHub {e.status_code}: {e.detail}")
        await finish_trace(session, trace, status="FAILED", duration_ms=int((time.perf_counter() - t0) * 1000), fail_reason="; ".join(errors))
        await session.commit()
        raise HTTPException(status_code=502, detail="; ".join(errors))
    return {"synced": synced, "errors": errors, "trace": trace.short_code}


@router.get("/career-assets/updates")
async def list_updates(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    runs = (
        await session.execute(select(AssetUpdateRun).where(AssetUpdateRun.workspace_id == ws.id).order_by(AssetUpdateRun.id.desc()))
    ).scalars().all()
    draft_ids = [r.draft_id for r in runs if r.draft_id]
    pub_ids = [r.publication_id for r in runs if r.publication_id]
    proj_ids = [r.project_id for r in runs if r.project_id]
    drafts = {
        d.id: d for d in (
            await session.execute(select(PublicContentDraft).where(PublicContentDraft.id.in_(draft_ids or [0])))
        ).scalars().all()
    }
    pubs = {
        p.id: p for p in (
            await session.execute(select(ContentPublication).where(ContentPublication.id.in_(pub_ids or [0])))
        ).scalars().all()
    }
    projs = {
        p.id: p for p in (
            await session.execute(select(Project).where(Project.id.in_(proj_ids or [0])))
        ).scalars().all()
    }
    return {"items": [_run_view(r, drafts.get(r.draft_id), pubs.get(r.publication_id), projs.get(r.project_id)) for r in runs]}


@router.post("/career-assets/updates/run")
async def run_update_pipeline(
    project_id: int | None = None,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """跑一次资产更新流水线: 取项目最新扫描发现 → 生成草稿+风险分级 → AssetUpdateRun。

    project_id 省略时取最近扫描成功的一个项目。
    """
    if project_id:
        p = (
            await session.execute(select(Project).where(Project.id == project_id, Project.workspace_id == ws.id))
        ).scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    else:
        p = (
            await session.execute(
                select(Project)
                .join(ProjectScan, ProjectScan.project_id == Project.id)
                .where(Project.workspace_id == ws.id, ProjectScan.status == "SUCCESS")
                .order_by(ProjectScan.id.desc())
                .limit(1)
            )
        ).scalars().first()
        if not p:
            raise HTTPException(status_code=409, detail="没有已扫描的项目; 先导入仓库并执行扫描")

    scan = (
        await session.execute(
            select(ProjectScan).where(ProjectScan.project_id == p.id, ProjectScan.status == "SUCCESS").order_by(ProjectScan.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    findings = (
        await session.execute(select(ProjectFinding).where(ProjectFinding.scan_id == scan.id).order_by(ProjectFinding.id))
    ).scalars().all()
    stats = scan.stats or {}

    t0 = time.perf_counter()
    trace = await start_trace(session, ws.id, "asset_update", f"更新职业资产: {p.name}", short_code=f"TR-AU{int(t0)}")
    add_span(session, trace, "collect_findings", int((time.perf_counter() - t0) * 1000), detail={"findings": len(findings)})

    run = await create_asset_update(
        session, ws, p,
        [{"domain": f.domain, "level": f.level, "title": f.title, "detail": f.detail, "file_path": f.file_path} for f in findings],
        coverage_for_scan(stats),
        stats.get("stats", {}),
        trace=trace,
    )
    await finish_trace(session, trace, status="SUCCESS", duration_ms=int((time.perf_counter() - t0) * 1000))
    await emit_event(session, ws.id, "asset_update_created", payload={"run": run.id, "risk": run.risk_level}, ref_type="asset_update_run", ref_id=run.id)
    await session.commit()

    draft = (await session.execute(select(PublicContentDraft).where(PublicContentDraft.id == run.draft_id))).scalar_one_or_none()
    return _run_view(run, draft, None, p)


@router.post("/career-assets/updates/{run_id}/approve")
async def approve_update(
    run_id: int,
    auto_merge: bool | None = None,
    dry_run: bool = False,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """审批资产更新: 校验草稿 → APPROVED → (低风险+auto_merge)自动发布(复用 public_content.publish_draft)。"""
    if auto_merge is not False and dry_run is not True:
        raise HTTPException(403, "public_writes_disabled: 可显式 auto_merge=false 保存本地审批")
    r = await _get_run(session, ws, run_id)
    if not r.draft_id:
        raise HTTPException(status_code=409, detail="该运行无内容草稿")
    d = (await session.execute(select(PublicContentDraft).where(PublicContentDraft.id == r.draft_id))).scalar_one_or_none()
    if not d:
        raise HTTPException(status_code=404, detail="草稿不存在")
    if d.status not in ("DRAFT", "NEEDS_REVIEW", "APPROVED"):
        raise HTTPException(status_code=409, detail=f"草稿状态 {d.status} 不可审批")
    # 高风险必须人工审核: 不允许 auto_merge 跳过(即便参数为 true)
    if r.risk_level == "HIGH":
        auto_merge = False

    if d.status != "APPROVED":
        d.status = "APPROVED"
        d.reviewed_by = "workspace-owner"
        from datetime import datetime, timezone as _tz
        d.reviewed_at = datetime.now(_tz.utc)
        await session.flush()

    if auto_merge is None:
        auto_merge = r.risk_level == "LOW"

    if auto_merge:
        # 进程内复用发布端点(同一 session/事务域)
        pub_view = await publish_draft(d.id, dry_run=dry_run, auto_merge=True, ws=ws, session=session)
        pub = (
            await session.execute(select(ContentPublication).where(ContentPublication.draft_id == d.id).order_by(ContentPublication.id.desc()).limit(1))
        ).scalars().first()
        if pub:
            r.publication_id = pub.id
        # dry_run 只出计划, 不算已发布
        r.status = "DRAFT_CREATED" if dry_run else "PUBLISHED"
        await session.commit()
        d2 = d
        return {**_run_view(r, d2, pub, None), "publication_view": pub_view}

    r.status = "DRAFT_CREATED"  # 已审批未发布: 停在草稿态, 由公开内容页手动发布
    await session.commit()
    return _run_view(r, d, None, None)


@router.post("/career-assets/updates/{run_id}/rollback")
async def rollback_update(
    run_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """回滚资产发布: 复用 public_content.rollback_publication(进程内)。"""
    raise HTTPException(403, "public_writes_disabled: 本轮关闭远程回滚")
