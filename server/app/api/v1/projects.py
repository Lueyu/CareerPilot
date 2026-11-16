"""项目 intelligence API: 导入 GitHub 仓库 → 真实只读扫描 → 确定性能力映射。"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.models.project import Project, ProjectFinding, ProjectScan
from app.models.trace import Trace
from app.models.workspace import Workspace
from app.db import get_session
from app.services.github_repo import GitHubRepoError, collect_repo_snapshot, fetch_file_text
from app.services.project_analyzer import analyze_snapshot
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    repo_url: str | None = None            # https://github.com/owner/repo
    owner: str | None = None
    repo: str | None = None
    branch: str | None = None
    description: str | None = None


def _parse_repo_url(p_in: ProjectIn) -> tuple[str, str]:
    if p_in.owner and p_in.repo:
        return p_in.owner.strip(), p_in.repo.strip()
    url = (p_in.repo_url or "").strip().rstrip("/").removesuffix(".git")
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    parts = [x for x in url.split("/") if x]
    if len(parts) != 2:
        raise HTTPException(status_code=422, detail=f"无法从 '{p_in.repo_url}' 解析 owner/repo; 请给 GitHub 仓库链接或 owner+repo")
    return parts[0], parts[1]


async def _get_project(session: AsyncSession, ws: Workspace, project_id: int) -> Project:
    p = (
        await session.execute(select(Project).where(Project.id == project_id, Project.workspace_id == ws.id))
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    return p


def _project_view(p: Project, latest_scan: ProjectScan | None, finding_stats: dict | None) -> dict:
    meta = p.meta or {}
    return {
        "id": p.id,
        "name": p.name,
        "repo_url": p.repo_url,
        "owner": p.owner,
        "repo": p.repo,
        "branch": p.branch,
        "description": meta.get("description"),
        "status": p.status,
        "last_commit_sha": p.last_commit_sha,
        "last_scanned_at": p.last_scanned_at.isoformat() if p.last_scanned_at else None,
        "latest_scan": _scan_view(latest_scan) if latest_scan else None,
        "findings": finding_stats,
        "meta": meta,
    }


def _scan_view(s: ProjectScan | None) -> dict | None:
    if not s:
        return None
    return {
        "id": s.id, "status": s.status, "commit_sha": s.commit_sha,
        "stats": s.stats, "error": s.error,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


async def _finding_stats(session: AsyncSession, ws_id: int, project_id: int) -> dict | None:
    """最新一次成功扫描的发现统计+矩阵(来自 scan.stats)。"""
    s = (
        await session.execute(
            select(ProjectScan)
            .join(Project, Project.id == ProjectScan.project_id)
            .where(Project.workspace_id == ws_id, Project.id == project_id, ProjectScan.status == "SUCCESS")
            .order_by(ProjectScan.id.desc())
            .limit(1)
        )
    ).scalars().first()
    return s.stats if s else None


@router.get("/projects")
async def list_projects(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    projects = (
        await session.execute(select(Project).where(Project.workspace_id == ws.id).order_by(Project.id))
    ).scalars().all()
    scans = (
        await session.execute(select(ProjectScan).order_by(ProjectScan.id.desc()))
    ).scalars().all()
    latest_by_project: dict[int, ProjectScan] = {}
    for s in scans:
        latest_by_project.setdefault(s.project_id, s)
    items = []
    for p in projects:
        ls = latest_by_project.get(p.id)
        stats = ls.stats if ls and ls.status == "SUCCESS" else None
        items.append(_project_view(p, ls, {"matrix": (stats or {}).get("matrix", []), "stats": (stats or {}).get("stats", {})}))
    return {"items": items}


@router.post("/projects")
async def create_project(
    body: ProjectIn,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    owner, repo = _parse_repo_url(body)
    # 去重: 同一工作空间同仓库
    dup = (
        await session.execute(
            select(Project).where(Project.workspace_id == ws.id, Project.owner == owner, Project.repo == repo)
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"仓库 {owner}/{repo} 已导入(project {dup.id})")
    p = Project(
        workspace_id=ws.id,
        name=body.name.strip(),
        repo_url=body.repo_url or f"https://github.com/{owner}/{repo}",
        owner=owner, repo=repo,
        branch=(body.branch or "main").strip(),
        status="NEVER",
        meta={"description": body.description} if body.description else {},
    )
    session.add(p)
    await session.commit()
    return _project_view(p, None, None)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    p = await _get_project(session, ws, project_id)
    await session.delete(p)
    await session.commit()
    return {"deleted": True, "id": project_id}


@router.post("/projects/{project_id}/scan")
async def scan_project(
    project_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """真实扫描: GitHub REST 只读 → 确定性分析 → ProjectScan + ProjectFindings + Trace。"""
    p = await _get_project(session, ws, project_id)
    t0 = time.perf_counter()
    trace = await start_trace(
        session, ws.id, "project_scan", f"扫描仓库 {p.owner}/{p.repo}",
        short_code=f"TR-PS{int(t0)}",
    )
    scan = ProjectScan(project_id=p.id, workspace_id=ws.id, status="RUNNING", trace_id=trace.id)
    session.add(scan)
    p.status = "RUNNING"
    await session.flush()

    try:
        snapshot = await collect_repo_snapshot(p.owner, p.repo, p.branch)
    except GitHubRepoError as e:
        scan.status = "FAILED"
        scan.error = f"GitHub {e.status_code}: {e.detail}"
        p.status = "FAILED"
        add_span(session, trace, "fetch_repo", int((time.perf_counter() - t0) * 1000), status="ERROR", detail={"error": scan.error})
        await finish_trace(session, trace, status="FAILED", duration_ms=int((time.perf_counter() - t0) * 1000), fail_reason=scan.error)
        await session.commit()
        raise HTTPException(status_code=502, detail=f"GitHub 读取失败: {scan.error}")

    async def reader(path: str) -> str | None:
        from app.services.github_repo import get_github_token, fetch_file_text
        return await fetch_file_text(p.owner, p.repo, path, get_github_token(), snapshot.get("branch_used"))

    result = await analyze_snapshot(snapshot, file_reader=reader)
    commit_sha = (snapshot.get("pushed_at") or "")[:0]  # trees API 不给 sha; 用 commits 数+时间作指纹
    fingerprint = f"{snapshot.get('commit_count') or 0}:{len(snapshot.get('tree') or [])}:{snapshot.get('pushed_at')}"
    scan.commit_sha = fingerprint[:64]

    stats = {
        "matrix": result["matrix"],
        "stats": result["stats"],
        "findings_count": len(result["findings"]),
    }
    scan.stats = stats
    scan.status = "SUCCESS"
    p.status = "SUCCESS"
    p.last_scanned_at = datetime.now(timezone.utc)
    p.meta = {**(p.meta or {}), "description": snapshot.get("description") or (p.meta or {}).get("description"), "languages": result["stats"].get("languages")}

    # 旧发现清掉重写(最新扫描为准; findings 无 workspace_id, 经 scan→project 归属)
    old = (
        await session.execute(select(ProjectFinding).where(ProjectFinding.scan_id.in_(
            select(ProjectScan.id).where(ProjectScan.project_id == p.id)
        )))
    ).scalars().all()
    for f in old:
        await session.delete(f)

    for f in result["findings"]:
        session.add(ProjectFinding(
            scan_id=scan.id, project_id=p.id, workspace_id=ws.id,
            domain=f["domain"], level=f["level"],
            title=f["title"], detail=f["detail"], file_path=f["file_path"],
        ))

    add_span(session, trace, "analyze", int((time.perf_counter() - t0) * 1000), detail=stats)
    await finish_trace(session, trace, status="SUCCESS", duration_ms=int((time.perf_counter() - t0) * 1000))
    await emit_event(session, ws.id, "project_scanned", payload={"project_id": p.id, "scan": scan.id, "findings": len(result["findings"])}, ref_type="project", ref_id=p.id)
    await session.commit()
    return {"scan": _scan_view(scan), "result": result}


@router.get("/projects/{project_id}/scans")
async def list_scans(
    project_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    p = await _get_project(session, ws, project_id)
    scans = (
        await session.execute(select(ProjectScan).where(ProjectScan.project_id == p.id).order_by(ProjectScan.id.desc()))
    ).scalars().all()
    return {"items": [_scan_view(s) for s in scans]}


@router.get("/projects/{project_id}/findings")
async def list_findings(
    project_id: int,
    scan_id: int | None = None,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    p = await _get_project(session, ws, project_id)
    if scan_id:
        sid = scan_id
    else:
        s = (
            await session.execute(
                select(ProjectScan).where(ProjectScan.project_id == p.id, ProjectScan.status == "SUCCESS").order_by(ProjectScan.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if not s:
            return {"items": [], "matrix": [], "stats": {}}
        sid = s.id
    items = (
        await session.execute(select(ProjectFinding).where(ProjectFinding.scan_id == sid).order_by(ProjectFinding.id))
    ).scalars().all()
    scan = (await session.execute(select(ProjectScan).where(ProjectScan.id == sid))).scalar_one_or_none()
    return {
        "items": [
            {
                "id": f.id, "domain": f.domain, "level": f.level, "title": f.title,
                "detail": f.detail, "file_path": f.file_path, "evidence_ref": f.evidence_ref,
            }
            for f in items
        ],
        "matrix": (scan.stats or {}).get("matrix", []) if scan else [],
        "stats": (scan.stats or {}).get("stats", {}) if scan else {},
    }
