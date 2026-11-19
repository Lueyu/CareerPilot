"""Build public-content drafts from cited AI report observations."""
import difflib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import AssetUpdateRun, Project
from app.models.project_analysis import ProjectAnalysis
from app.models.public_content import PublicContentDraft
from app.models.trace import Trace
from app.models.workspace import Workspace
from app.services.content_validation import run_all


async def _next_code(session: AsyncSession, model, prefix: str) -> str:
    """与 public_content._next_code 同款: CT-0001 / AU-0001。"""
    rows = (await session.execute(select(model.short_code))).scalars().all()
    n = 0
    for c in rows:
        if c.startswith(prefix + "-"):
            try:
                n = max(n, int(c.split("-", 1)[1]))
            except ValueError:
                pass
    return f"{prefix}-{n + 1:04d}"


def repository_observations(report: ProjectAnalysis) -> list[dict]:
    return [
        claim for claim in (report.report or {}).get("claims", [])
        if claim.get("kind") == "CODE_OBSERVED" and claim.get("text") and claim.get("citations")
    ]


def _render_markdown(project: Project, report: ProjectAnalysis, observations: list[dict], freshness: str) -> str:
    lines = [
        f"# {project.name} · AI 项目观察",
        "",
        f"- 仓库: {project.repo_url or f'{project.owner}/{project.repo}'}",
        f"- 报告版本: {report.source_revision or '未知'}",
        f"- 报告发布时间: {report.report_published_at.isoformat()}",
    ]
    if freshness != "CURRENT":
        lines.append("- 报告与当前代码版本的对应关系需要复核。")
    lines.extend(["", "## 有代码引用的项目观察", "", "以下内容描述仓库实现。个人职责和结果需要本人单独确认。", ""])
    for claim in observations:
        lines.append(f"- **{claim['title']}**：{claim['text']}")
        for citation in claim["citations"]:
            lines.append(f"  - 代码位置：`{citation['path']}:{citation['start_line']}-{citation['end_line']}`")
    return "\n".join(lines)


def _risk_grade(check, freshness: str) -> tuple[str, str]:
    if check.status == "FAIL":
        messages = "；".join(e["message"] for e in check.errors[:3])
        return "HIGH", f"内置校验未通过：{messages}"
    if freshness != "CURRENT":
        return "MEDIUM", "需要复核报告版本与当前代码的对应关系。"
    if check.status == "WARN":
        return "MEDIUM", "内容校验发现需要人工确认的事项。"
    return "LOW", "内置内容校验通过。"


def _diff_changed(old_markdown: str | None, new_markdown: str) -> list[str]:
    """对比上一个草稿, 输出人类可读变更行(最多 12 条)。"""
    if not old_markdown:
        return ["首次生成职业资产内容（此前无草稿版本）"]
    changed: list[str] = []
    for line in difflib.unified_diff(old_markdown.splitlines(), new_markdown.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            changed.append(line[1:].strip()[:100])
        elif line.startswith("-") and not line.startswith("---"):
            changed.append("移除: " + line[1:].strip()[:80])
    return changed[:12] or ["内容无实质变化"]


async def _latest_run_markdown(session: AsyncSession, ws_id: int, project_id: int) -> str | None:
    row = (
        await session.execute(
            select(PublicContentDraft.markdown_body)
            .join(AssetUpdateRun, AssetUpdateRun.draft_id == PublicContentDraft.id)
            .where(
                AssetUpdateRun.workspace_id == ws_id,
                AssetUpdateRun.project_id == project_id,
                AssetUpdateRun.stats["analysis_id"].as_integer().is_not(None),
            )
            .order_by(AssetUpdateRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def create_asset_update(
    session: AsyncSession,
    ws: Workspace,
    project: Project,
    report: ProjectAnalysis,
    observations: list[dict],
    freshness: str,
    trace: Trace | None = None,
) -> AssetUpdateRun:
    """生成内容草稿+风险分级, 落一条 AssetUpdateRun(status=DRAFT_CREATED)。"""
    old_md = await _latest_run_markdown(session, ws.id, project.id)
    markdown = _render_markdown(project, report, observations, freshness)
    changed = _diff_changed(old_md, markdown)
    title = f"{project.name} · AI 项目观察"
    draft_code = await _next_code(session, PublicContentDraft, "CT")
    slug_base = project.repo or project.name.lower().replace(" ", "-")
    slug = f"projects/{slug_base[:200]}-ai-{draft_code.lower()}"

    check = run_all(markdown, sources=[{"license_status": "UNKNOWN"}])
    risk_level, risk_reason = _risk_grade(check, freshness)
    provenance = {"analysis_id": report.id, "source_revision": report.source_revision, "freshness": freshness}

    draft = PublicContentDraft(
        short_code=draft_code,
        workspace_id=ws.id,
        title=title,
        slug=slug,
        description=f"源自 AI 项目报告: {project.owner}/{project.repo} · 风险 {risk_level}",
        markdown_body=markdown,
        json_meta={"kind": "PROJECT_EVIDENCE", "project_id": project.id, **provenance},
        privacy_scan_status="PASS" if check.status != "FAIL" else "FAIL",
        fact_check_status="PASS" if check.status == "PASS" else ("WARN" if check.status == "WARN" else "FAIL"),
        license_status="UNKNOWN",
        validation_errors=check.errors,
        target_path=None,
        status="DRAFT",
    )
    session.add(draft)
    await session.flush()

    run = AssetUpdateRun(
        short_code=await _next_code(session, AssetUpdateRun, "AU"),
        workspace_id=ws.id,
        project_id=project.id,
        status="DRAFT_CREATED",
        diff_markdown="\n".join(changed),
        risk_level=risk_level,
        risk_reason=risk_reason,
        draft_id=draft.id,
        trace_id=trace.id if trace else None,
        stats={"changed": changed, "observations_count": len(observations), **provenance},
    )
    session.add(run)
    await session.flush()
    return run
