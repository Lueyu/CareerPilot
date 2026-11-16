"""Career Assets 更新流水线(确定性规则, 复用 public_content 发布管线)。

流程: 检测项目变更 → 提取证据(项目扫描 findings) → 映射能力 → 对比现有展示
      → 生成内容草稿(PublicContentDraft) → 风险分级 → 创建 AssetUpdateRun
发布: approve(复用 public_content 的 approve/publish, 进程内调用) → 回滚(publications/rollback)。
"""
import difflib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import AssetUpdateRun, CareerAsset, Project
from app.models.public_content import PublicContentDraft
from app.models.skill import Skill
from app.models.trace import Trace
from app.models.workspace import Workspace
from app.services.content_validation import run_all


async def _next_code(session: AsyncSession, model, prefix: str) -> str:
    """与 public_content._next_code 同款: CT-0001 / AU-0001。"""
    table = model.__tablename__
    rows = (await session.execute(select(model.short_code))).scalars().all()
    n = 0
    for c in rows:
        if c.startswith(prefix + "-"):
            try:
                n = max(n, int(c.split("-", 1)[1]))
            except ValueError:
                pass
    return f"{prefix}-{n + 1:04d}"


async def _skills(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(Skill))).scalars().all()
    return {s.key: s.name for s in rows}


def _render_markdown(project: Project, findings: list[dict], matrix: list[dict], skill_names: dict[str, str], stats: dict) -> str:
    """生成"项目能力证据"页的 Markdown(门户可渲染)。"""
    lines: list[str] = []
    lines.append(f"# {project.name} · 项目能力证据")
    lines.append("")
    desc = project.meta.get("description") if isinstance(project.meta, dict) else None
    if desc:
        lines.append(f"> {desc}")
        lines.append("")
    lines.append(f"- 仓库: {project.repo_url or f'{project.owner}/{project.repo}'}")
    if stats.get("commits"):
        lines.append(f"- Commit 数: {stats['commits']}")
    if stats.get("languages"):
        lines.append(f"- 主要语言: {' / '.join(map(str, stats['languages']))}")
    lines.append(f"- 最近扫描: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC（CareerPilot 自动分析）")
    lines.append("")
    lines.append("## 能力矩阵")
    lines.append("")
    lines.append("| 能力域 | 证据强度 | 发现数 |")
    lines.append("|---|---|---|")
    for m in matrix:
        if m["findings"] > 0:
            lines.append(f"| {skill_names.get(m['key'], m['key'])} | {m['level']} | {m['findings']} |")
    lines.append("")
    lines.append("## 关键发现")
    lines.append("")
    for f in findings:
        icon = {"VERIFIED": "✅", "HIGH": "🔶", "UNVERIFIED_CLAIM": "⚠️"}.get(f["level"], "·")
        lines.append(f"- {icon} **[{f['level']}] {f['title']}** — {f['detail']}")
        lines.append(f"  - 证据: `{f.get('file_path') or '-'}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*本页由 CareerPilot 从仓库只读分析自动生成；证据等级: VERIFIED=已读内容确认, HIGH=结构证据, UNVERIFIED_CLAIM=仅声明未验证。*")
    return "\n".join(lines)


def _risk_grade(findings: list[dict], check) -> tuple[str, str]:
    """确定性风险分级: LOW / MEDIUM / HIGH。"""
    if check.status == "FAIL":
        return "HIGH", f"内置校验未通过: {'; '.join(e.get('message', '') for e in check.errors[:3])}"
    n_claim = sum(1 for f in findings if f["level"] == "UNVERIFIED_CLAIM")
    if check.status == "WARN" or n_claim >= 3:
        return "MEDIUM", f"存在未验证声明 {n_claim} 条或校验 WARN; 建议人工确认后发布。"
    return "LOW", "无隐私/事实/版权问题, 且未验证声明少于 3 条。"


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
            .where(AssetUpdateRun.workspace_id == ws_id, AssetUpdateRun.project_id == project_id)
            .order_by(AssetUpdateRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def create_asset_update(
    session: AsyncSession,
    ws: Workspace,
    project: Project,
    findings: list[dict],
    matrix: list[dict],
    stats: dict,
    trace: Trace | None = None,
) -> AssetUpdateRun:
    """生成内容草稿+风险分级, 落一条 AssetUpdateRun(status=DRAFT_CREATED)。"""
    skill_names = await _skills(session)
    old_md = await _latest_run_markdown(session, ws.id, project.id)
    markdown = _render_markdown(project, findings, matrix, skill_names, stats)
    changed = _diff_changed(old_md, markdown)
    title = f"{project.name} · 项目能力证据（自动生成）"
    slug = f"projects/{project.repo or project.name.lower().replace(' ', '-')}"

    # 内置校验(隐私/事实/版权/Markdown lint); 自有仓库内容 license=OWNED
    check = run_all(markdown, sources=[{"license_status": "OWNED"}])
    risk_level, risk_reason = _risk_grade(findings, check)

    draft = PublicContentDraft(
        short_code=await _next_code(session, PublicContentDraft, "CT"),
        workspace_id=ws.id,
        title=title,
        slug=slug,
        description=f"由项目扫描自动生成: {project.owner}/{project.repo} · 风险 {risk_level}",
        markdown_body=markdown,
        json_meta={"kind": "PROJECT_EVIDENCE", "project_id": project.id, "matrix": matrix, "stats": stats},
        privacy_scan_status="PASS" if check.status != "FAIL" else "FAIL",
        fact_check_status="PASS" if check.status == "PASS" else ("WARN" if check.status == "WARN" else "FAIL"),
        license_status="OWNED",
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
        stats={"changed": changed, "findings_count": len(findings), "matrix": matrix},
    )
    session.add(run)
    await session.flush()
    return run
