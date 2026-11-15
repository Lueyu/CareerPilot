"""岗位对标 API。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import JobStandard, SkillState, Workspace
from app.services.competency import recompute_skill_states

router = APIRouter()

NEXT_STEP = {
    "UNKNOWN": "先做一次独立诊断",
    "NEEDS_VALIDATION": "补充一次专项验证",
    "BELOW_TARGET": "进入学习计划补齐差距",
    "MEETS_TARGET": "保持: 安排延迟复测",
    "ABOVE_TARGET": "维持现状或挑战更高职级",
}


@router.get("/benchmark")
async def benchmark(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    states = await recompute_skill_states(session, ws.id)
    standard = (
        await session.execute(
            select(JobStandard).where(JobStandard.is_seed.is_(True)).order_by(JobStandard.id.desc())
        )
    ).scalar_one_or_none()

    rows = []
    if standard:
        for t in sorted(standard.targets, key=lambda x: -x.weight_pct):
            state = next((s for s in states if s.skill_id == t.skill_id), None)
            level = state.estimated_level if state else None
            rows.append(
                {
                    "skill": {"key": t.skill.key, "name": t.skill.name},
                    "weight_pct": t.weight_pct,
                    "target_level": t.target_level,
                    "current_level": level,
                    "status": state.status if state else "UNKNOWN",
                    "evidence_coverage": state.evidence_coverage if state else 0,
                    "confidence": state.confidence if state else 0.0,
                    "next_step": NEXT_STEP.get(state.status if state else "UNKNOWN", ""),
                }
            )
    return {
        "standard": {
            "role": standard.role,
            "level": standard.level,
            "version": standard.version,
            "jd_sample_count": standard.jd_sample_count,
            "is_seed": standard.is_seed,
            "note": "种子假设 · 非统计结论" if standard.is_seed else "",
        },
        "rows": rows,
    }
