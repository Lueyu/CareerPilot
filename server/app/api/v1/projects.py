"""项目 API: 导入仓库、维护资料与查看静态扫描覆盖。"""
import time
from uuid import uuid4
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.models.project import Project, ProjectFinding, ProjectScan
from app.models.trace import Trace
from app.models.workspace import Workspace
from app.db import get_session
from app.services.github_repo import (
    GitHubRepoError, collect_repo_snapshot, fetch_file_text, get_github_token,
)
from app.services.gitcode_repo import (
    GitCodeRepoError, collect_gitcode_snapshot, fetch_gitcode_file_text, get_gitcode_token,
)
from app.services.project_analyzer import analyze_snapshot, coverage_for_scan
from app.services.project_context import project_kind, project_profile
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()


class ProjectProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    background: str = Field(default="", max_length=5000)
    role: str = Field(default="", max_length=5000)
    responsibilities: str = Field(default="", max_length=5000)
    stack: str = Field(default="", max_length=5000)
    decisions: str = Field(default="", max_length=5000)
    challenges: str = Field(default="", max_length=5000)
    outcomes: str = Field(default="", max_length=5000)
    boundaries: str = Field(default="", max_length=5000)


class ProjectProfileIn(BaseModel):
    profile: ProjectProfile


class ProjectIn(BaseModel):
    name: str = Field(default='', max_length=120)
    repo_url: str | None = None            # https://github.com/owner/repo 或 https://gitcode.com/owner/repo
    owner: str | None = None
    repo: str | None = None
    branch: str | None = None
    description: str | None = None
    kind: Literal["github", "gitcode", "manual"] = "github"
    profile: ProjectProfile = Field(default_factory=ProjectProfile)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value = value.strip()
        return value


# 仓库宿主注册表: prefixes 用于从 URL 识别 provider, home 用于补全默认仓库地址。
_PROVIDERS = {
    "github": {
        "label": "GitHub", "home": "https://github.com",
        "prefixes": ("https://github.com/", "http://github.com/", "git@github.com:"),
    },
    "gitcode": {
        "label": "GitCode", "home": "https://gitcode.com",
        "prefixes": ("https://gitcode.com/", "http://gitcode.com/", "git@gitcode.com:"),
    },
}


def _detect_provider(url: str) -> str | None:
    for name, cfg in _PROVIDERS.items():
        if any(url.startswith(prefix) for prefix in cfg["prefixes"]):
            return name
    return None


def _parse_repo_url(p_in: ProjectIn) -> tuple[str, str, str]:
    """返回 (provider, owner, repo)。有 repo_url 时按 URL 识别宿主(优先于 kind)。"""
    if p_in.owner and p_in.repo:
        provider = p_in.kind if p_in.kind in _PROVIDERS else "github"
        return provider, p_in.owner.strip(), p_in.repo.strip()
    url = (p_in.repo_url or "").strip().rstrip("/").removesuffix(".git")
    provider = _detect_provider(url)
    if provider is None:
        raise HTTPException(
            status_code=422,
            detail=f"无法从 '{p_in.repo_url}' 解析 owner/repo; 请给 GitHub/GitCode 仓库链接或 owner+repo",
        )
    for prefix in _PROVIDERS[provider]["prefixes"]:
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    parts = [x for x in url.split("/") if x]
    if len(parts) != 2:
        raise HTTPException(
            status_code=422,
            detail=f"无法从 '{p_in.repo_url}' 解析 owner/repo; 请给 GitHub/GitCode 仓库链接或 owner+repo",
        )
    return provider, parts[0], parts[1]


async def _get_project(session: AsyncSession, ws: Workspace, project_id: int) -> Project:
    p = (
        await session.execute(select(Project).where(Project.id == project_id, Project.workspace_id == ws.id, Project.archived_at.is_(None)))
    ).scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    return p


def _project_view(p: Project, latest_scan: ProjectScan | None, finding_stats: dict | None) -> dict:
    meta = p.meta or {}
    return {
        "id": p.id,
        "name": p.name,
        "kind": project_kind(p),
        "profile": project_profile(p),
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
        "stats": {key: value for key, value in (s.stats or {}).items() if key != "matrix"} | {"coverage": coverage_for_scan(s.stats)}, "error": s.error,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


async def _finding_stats(session: AsyncSession, ws_id: int, project_id: int) -> dict | None:
    """最新一次成功扫描的覆盖信息；历史分数不再作为能力输出。"""
    s = (
        await session.execute(
            select(ProjectScan)
            .join(Project, Project.id == ProjectScan.project_id)
            .where(Project.workspace_id == ws_id, Project.id == project_id, ProjectScan.status == "SUCCESS")
            .order_by(ProjectScan.id.desc())
            .limit(1)
        )
    ).scalars().first()
    return ({key: value for key, value in (s.stats or {}).items() if key != "matrix"} | {"coverage": coverage_for_scan(s.stats)}) if s else None


@router.get("/projects")
async def list_projects(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    projects = (
        await session.execute(select(Project).where(Project.workspace_id == ws.id, Project.archived_at.is_(None)).order_by(Project.id))
    ).scalars().all()
    scans = (
        await session.execute(select(ProjectScan).where(ProjectScan.workspace_id == ws.id).order_by(ProjectScan.id.desc()))
    ).scalars().all()
    latest_by_project: dict[int, ProjectScan] = {}
    for s in scans:
        latest_by_project.setdefault(s.project_id, s)
    items = []
    for p in projects:
        ls = latest_by_project.get(p.id)
        stats = ls.stats if ls and ls.status == "SUCCESS" else None
        view = _project_view(p, ls, {"coverage": coverage_for_scan(stats), "stats": (stats or {}).get("stats", {})} if stats else None)
        from app.api.v1.project_autopilot import autopilot_view
        view['autopilot'] = await autopilot_view(session, p)
        items.append(view)
    return {"items": items}


@router.post("/projects")
async def create_project(
    body: ProjectIn,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    if body.kind == "manual":
        if not body.name.strip():
            raise HTTPException(422, '手工项目名称不能为空')
        if body.repo_url or body.owner or body.repo:
            raise HTTPException(status_code=422, detail="手工项目无需仓库；如需扫描仓库，请选择仓库项目")
        owner, repo, provider = "", "", "manual"
    else:
        provider, owner, repo = _parse_repo_url(body)
        # 去重仅针对仓库项目，手工项目没有虚构的仓库地址。
        dup = (
            await session.execute(
                select(Project).where(Project.workspace_id == ws.id, Project.owner == owner, Project.repo == repo,
                    Project.archived_at.is_(None), func.coalesce(Project.meta['kind'].as_string(),'github')==provider,
                    (Project.branch==body.branch.strip()) if body.branch else True)
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=409, detail=f"仓库 {owner}/{repo} 已导入(project {dup.id})")
    p = Project(
        workspace_id=ws.id,
        name=body.name.strip() or repo,
        repo_url="" if provider == "manual" else (body.repo_url or f"{_PROVIDERS[provider]['home']}/{owner}/{repo}"),
        owner=owner, repo=repo,
        branch="" if provider == "manual" else (body.branch or "").strip(),
        status="NEVER",
        meta={"description": body.description, "kind": provider,
              "profile": {key: value.strip() for key, value in body.profile.model_dump().items()},
              "profile_version": 1},
    )
    session.add(p)
    await session.flush()
    run = None
    if provider != 'manual':
        from app.services.project_autopilot_config import runtime_config, model_fingerprint
        from app.services.project_jobs import enqueue
        from app.services.project_scheduler import versions
        config, _, settings = await runtime_config(session, ws.id, require_consent=False)
        pipeline, prompt = versions()
        run = await enqueue(session, p, uuid4().hex, budget=settings['budget'],
            model_config_fingerprint=model_fingerprint(config),pipeline_version=pipeline,prompt_bundle_version=prompt)
    await session.commit()
    return {**_project_view(p, None, None), 'run_id':run.id if run else None, 'run_status':run.status if run else None}


@router.get("/projects/{project_id}")
async def get_project(
    project_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    p = await _get_project(session, ws, project_id)
    latest = (await session.execute(select(ProjectScan).where(
        ProjectScan.project_id == p.id, ProjectScan.workspace_id == ws.id,
    ).order_by(ProjectScan.id.desc()).limit(1))).scalar_one_or_none()
    return _project_view(p, latest, latest.stats if latest and latest.status == "SUCCESS" else None)


@router.patch("/projects/{project_id}/profile")
async def update_project_profile(
    project_id: int,
    body: ProjectProfileIn,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    p = (await session.execute(select(Project).where(
        Project.id == project_id, Project.workspace_id == ws.id,
    ).with_for_update())).scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail=f"project {project_id} not found")
    changes = {key: value.strip() for key, value in body.profile.model_dump(exclude_unset=True).items()}
    meta = p.meta or {}
    p.meta = {**meta, "profile": {**project_profile(p), **changes},
              "profile_version": int(meta.get("profile_version") or 0) + 1,
              "profile_updated_at": datetime.now(timezone.utc).isoformat()}
    await session.commit()
    return await get_project(project_id, ws=ws, session=session)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    from app.services.project_memory import lock_project_memories
    from app.models import MemoryItem,ProjectAnalysis,AnalysisStep
    from app.services.project_jobs import OPEN
    await session.execute(text('SELECT pg_advisory_xact_lock(20261117, 0)'))
    await lock_project_memories(session, ws.id)
    p = await _get_project(session, ws, project_id)
    memories=(await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id==ws.id,
        MemoryItem.meta['project_id'].as_integer()==project_id))).all()
    for memory in memories:
        if memory.status in ('ACTIVE','PENDING_REVIEW'):memory.status = 'SUPERSEDED'
        memory.revision += 1
        memory.meta = {**(memory.meta or {}), 'project_deleted': True}
    p.archived_at = datetime.now(timezone.utc)
    p.pending_update = None
    runs=(await session.scalars(select(ProjectAnalysis).where(ProjectAnalysis.project_id==p.id,
        ProjectAnalysis.status.in_(OPEN)).with_for_update())).all()
    for run in runs:
        run.status='CANCELLED'
        steps=(await session.scalars(select(AnalysisStep).where(AnalysisStep.run_id==run.id,AnalysisStep.status!='SUCCEEDED'))).all()
        for step in steps:
            step.status='CANCELLED';step.fencing_token+=1;step.lease_owner=None;step.lease_expires_at=None
    await session.commit()
    return {"deleted": True, "id": project_id}


@router.post("/projects/{project_id}/scan")
async def scan_project(
    project_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """真实扫描: 仓库 REST 只读 → 确定性分析 → ProjectScan + ProjectFindings + Trace。"""
    p = await _get_project(session, ws, project_id)
    if project_kind(p) == "manual":
        raise HTTPException(status_code=409, detail="手工项目不支持仓库扫描，请维护项目背景与本人职责资料")
    t0 = time.perf_counter()
    trace = await start_trace(
        session, ws.id, "project_scan", f"扫描仓库 {p.owner}/{p.repo}",
        short_code=f"TR-PS{int(t0)}",
    )
    scan = ProjectScan(project_id=p.id, workspace_id=ws.id, status="RUNNING", trace_id=trace.id)
    session.add(scan)
    p.status = "RUNNING"
    await session.flush()

    kind = project_kind(p)
    if kind == "gitcode":
        collector, err_cls, label = collect_gitcode_snapshot, GitCodeRepoError, "GitCode"
        get_token, fetch_file = get_gitcode_token, fetch_gitcode_file_text
    elif kind == "github":
        collector, err_cls, label = collect_repo_snapshot, GitHubRepoError, "GitHub"
        get_token, fetch_file = get_github_token, fetch_file_text
    else:
        raise HTTPException(status_code=409, detail=f"不支持的仓库宿主: {kind}")
    try:
        snapshot = await collector(p.owner, p.repo, p.branch)
    except err_cls as e:
        scan.status = "FAILED"
        scan.error = f"{label} {e.status_code}: {e.detail}"
        p.status = "FAILED"
        add_span(session, trace, "fetch_repo", int((time.perf_counter() - t0) * 1000), status="ERROR", detail={"error": scan.error})
        await finish_trace(session, trace, status="FAILED", duration_ms=int((time.perf_counter() - t0) * 1000), fail_reason=scan.error)
        await session.commit()
        raise HTTPException(status_code=502, detail=f"{label} 读取失败: {scan.error}")

    async def reader(path: str) -> str | None:
        return await fetch_file(p.owner, p.repo, path, await get_token(), snapshot.get("branch_used"))

    result = await analyze_snapshot(snapshot, file_reader=reader)
    commit_sha = (snapshot.get("pushed_at") or "")[:0]  # trees API 不给 sha; 用 commits 数+时间作指纹
    fingerprint = f"{snapshot.get('commit_count') or 0}:{len(snapshot.get('tree') or [])}:{snapshot.get('pushed_at')}"
    scan.commit_sha = fingerprint[:64]

    stats = {
        "coverage": result["coverage"],
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
    if scan_id is not None:
        sid = scan_id
    else:
        s = (
            await session.execute(
                select(ProjectScan).where(ProjectScan.project_id == p.id, ProjectScan.status == "SUCCESS").order_by(ProjectScan.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if not s:
            return {"items": [], "coverage": None, "stats": {}}
        sid = s.id
    scan = (await session.execute(select(ProjectScan).where(
        ProjectScan.id == sid, ProjectScan.project_id == p.id, ProjectScan.workspace_id == ws.id,
    ))).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail="project scan not found")
    items = (
        await session.execute(select(ProjectFinding).where(
            ProjectFinding.scan_id == sid, ProjectFinding.project_id == p.id, ProjectFinding.workspace_id == ws.id,
        ).order_by(ProjectFinding.id))
    ).scalars().all()
    return {
        "items": [
            {
                "id": f.id, "domain": f.domain, "level": f.level, "title": f.title,
                "detail": f.detail, "file_path": f.file_path, "evidence_ref": f.evidence_ref,
            }
            for f in items
        ],
        "coverage": coverage_for_scan(scan.stats),
        "stats": (scan.stats or {}).get("stats", {}) if scan else {},
    }
