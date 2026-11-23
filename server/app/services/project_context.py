"""Freeze selected project context without treating repository code as personal work."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.services.project_memory import active_project_memories, memory_fact

PROFILE_FIELDS = (
    "background", "role", "responsibilities", "stack", "decisions",
    "challenges", "outcomes", "boundaries",
)
PROFILE_LABELS = {
    "background": "项目背景", "role": "我的角色", "responsibilities": "我的职责与贡献",
    "stack": "技术栈", "decisions": "方案选择与取舍", "challenges": "问题与解决过程",
    "outcomes": "实际结果", "boundaries": "职责边界与未完成部分",
}


def project_kind(project: Project) -> str:
    """仓库宿主: "github" | "gitcode" | "manual"(手工, 无仓库)。缺省按历史 github 处理。"""
    return (project.meta or {}).get("kind") or "github"


def project_profile(project: Project) -> dict[str, str]:
    """Only explicit personal-profile fields enter prompts; unrelated meta is excluded."""
    raw = (project.meta or {}).get("profile")
    raw = raw if isinstance(raw, dict) else {}
    return {key: raw[key].strip() if isinstance(raw.get(key), str) else "" for key in PROFILE_FIELDS}


async def build_project_context(
    session: AsyncSession, workspace_id: int, project_ids: list[int],
) -> list[dict]:
    """Return JSON snapshots with stable fact IDs and no live ORM references.

    A snapshot fingerprint hashes its contents. The historical ``scan.commit_sha``
    stores a scanner fingerprint, so it is deliberately not labeled a Git SHA here.
    """
    ids = list(dict.fromkeys(project_ids))
    if not ids:
        return []
    rows = (await session.execute(select(Project).where(
        Project.workspace_id == workspace_id, Project.id.in_(ids), Project.archived_at.is_(None),
    ))).scalars().all()
    by_id = {project.id: project for project in rows}
    if set(by_id) != set(ids):
        # Do not disclose which foreign workspace owns an inaccessible ID.
        raise ValueError("selected project not found in current workspace")
    contexts = []
    for project_id in ids:
        project = by_id[project_id]
        profile = project_profile(project)
        facts = [{
            "id": f"P{project.id}-{key}", "text": f"{PROFILE_LABELS[key]}：{value}",
            "source": "user_statement", "field": key,
        } for key, value in profile.items() if value]
        facts.extend(memory_fact(m) for m in await active_project_memories(session, workspace_id, project.id))
        context = {
            "id": project.id, "name": project.name, "kind": project_kind(project),
            "profile": profile, "facts": facts,
            "has_personal_context": bool(profile["role"] and profile["responsibilities"]),
            "repo_url": project.repo_url if project_kind(project) != "manual" else None,
        }
        encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        context["snapshot_fingerprint"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        contexts.append(context)
    return contexts
