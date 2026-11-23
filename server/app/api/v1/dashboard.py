"""总览(Dashboard) API。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.api.deps import get_workspace
from app.api.v1.learning import _today
from app.db import get_session
from app.models import (
    Evidence,
    Exam,
    ImportSession,
    JobStandard,
    LearningTask,
    SkillState,
    Workspace,
)
from app.services.competency import recompute_skill_states
from app.services.tenant_standard import tenant_standard, uses_tenant_standard

router = APIRouter()


@router.get("/dashboard")
async def dashboard(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    # 重算能力状态(保证一致性; 规则引擎开销极小)
    tenant = await tenant_standard(session, ws, persist_seed=True) if uses_tenant_standard(ws) else None
    states = await recompute_skill_states(session, ws.id, standard=tenant)
    if tenant is not None:
        active_ids = {target.skill_id for target in tenant.targets}
        active_states = [state for state in states if state.skill_id in active_ids]
    else:
        active_states = states

    evidence_count = (
        await session.execute(select(func.count(Evidence.id)).where(Evidence.workspace_id == ws.id))
    ).scalar_one()

    pending_skills = sum(
        1 for s in active_states if s.status in (SkillState.STATUS_UNKNOWN, SkillState.STATUS_NEEDS_VALIDATION)
    )

    today_tasks = (
        await session.execute(
            select(LearningTask)
            .where(
                LearningTask.workspace_id == ws.id,
                LearningTask.status != LearningTask.STATUS_DONE,
                LearningTask.kind != LearningTask.KIND_RETEST,
                LearningTask.retest_round == 0,
            )
            .order_by(LearningTask.id)
            .limit(6)
        )
    ).scalars().all()

    parent = aliased(LearningTask)
    due_retests = (
        await session.execute(
            select(LearningTask.id, LearningTask.title, LearningTask.due_date,
                   parent.id.label("parent_id"), parent.title.label("parent_title"))
            .join(parent, LearningTask.retest_of == parent.id)
            .where(
                LearningTask.workspace_id == ws.id,
                LearningTask.kind == LearningTask.KIND_RETEST,
                LearningTask.status != LearningTask.STATUS_DONE,
                LearningTask.due_date <= _today(),
                LearningTask.skill_id.is_not(None),
                parent.workspace_id == ws.id,
                parent.status == LearningTask.STATUS_DONE,
            )
            .order_by(LearningTask.due_date, LearningTask.id)
        )
    ).mappings().all()

    pending_imports = (
        await session.execute(
            select(ImportSession)
            .where(ImportSession.workspace_id == ws.id, ImportSession.status == ImportSession.STATUS_REVIEW)
            .order_by(ImportSession.id.desc())
        )
    ).scalars().all()

    recent_exams = (
        await session.execute(
            select(Exam)
            .where(Exam.workspace_id == ws.id, Exam.status == Exam.STATUS_COMPLETED, Exam.kind != 'practice')
            .order_by(Exam.completed_at.desc())
            .limit(3)
        )
    ).scalars().all()

    # 加权差距 = Σ weight×(target-current)/100
    target_map = {s.skill_id: s for s in states}
    standard = tenant or (
        await session.execute(
            select(JobStandard).where(JobStandard.is_seed.is_(True),
                JobStandard.role == 'AI Agent 应用开发工程师').order_by(JobStandard.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if standard:
        domains = [
            {
                "key": t.skill.key,
                "name": t.skill.name,
                "weight_pct": t.weight_pct,
                "target_level": t.target_level,
                "level": target_map[t.skill_id].estimated_level if t.skill_id in target_map else None,
                "status": target_map[t.skill_id].status if t.skill_id in target_map else "UNKNOWN",
                "confidence": target_map[t.skill_id].confidence if t.skill_id in target_map else 0.0,
                "evidence_coverage": target_map[t.skill_id].evidence_coverage if t.skill_id in target_map else 0,
            }
            for t in sorted(standard.targets, key=lambda x: -x.weight_pct)
        ]
    else:
        domains = [
            {
                "key": s.skill.key, "name": s.skill.name, "weight_pct": 0, "target_level": None,
                "level": s.estimated_level, "status": s.status,
                "confidence": s.confidence, "evidence_coverage": s.evidence_coverage,
            }
            for s in states
        ]
    gap = _weighted_gap(domains)

    skill_names = {s.id: s.skill.name for s in states}
    return {
        "workspace": {"name": ws.name, "slug": ws.slug, "target_role": ws.target_role, "target_level": ws.target_level, "is_demo": ws.is_demo},
        "stats": {
            "evidence_total": evidence_count,
            "pending_skills": pending_skills,
            "today_tasks": len(today_tasks),
            "pending_imports": len(pending_imports),
        },
        "gap": gap,
        "domains": domains,
        "due_retests": [
            {**retest, "due_date": retest["due_date"].isoformat()} for retest in due_retests
        ],
        "today_tasks": [
            {
                "id": t.id, "title": t.title, "kind": t.kind, "why": t.why,
                "est_minutes": t.est_minutes, "status": t.status,
                "skill_name": skill_names.get(t.skill_id, "") if t.skill_id else None,
            }
            for t in today_tasks
        ],
        "pending_imports": [
            {"id": i.id, "short_code": i.short_code, "filename": i.filename, "items": len(i.items), "created_at": i.created_at.isoformat()}
            for i in pending_imports
        ],
        "recent_activity": [
            {
                "kind": "exam",
                "title": e.title,
                "detail": f"得分 {e.score}",
                "score": e.score,
                "completed_at": (e.completed_at or e.created_at).isoformat(),
                "at": (e.completed_at or e.created_at).isoformat(),
            }
            for e in recent_exams
        ],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _weighted_gap(domains: list[dict]) -> float | None:
    """按 PRD 权重计算整体差距(仅对有估计的域)。"""
    total_w, total_gap = 0.0, 0.0
    for d in domains:
        w = d.get("weight_pct") or 0
        level = d.get("level")
        target = d.get("target_level")
        if level is None or target is None:
            continue
        total_w += w
        total_gap += w * max(0.0, target - level)
    if total_w == 0:
        return None
    return round(total_gap / total_w, 2)
