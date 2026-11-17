"""能力画像 API。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import Evidence, Skill, SkillState, Workspace
from app.services.competency import recompute_skill_states

router = APIRouter()


@router.get("/profile")
async def profile(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    states = await recompute_skill_states(session, ws.id)
    evidence = (
        await session.execute(
            select(Evidence).where(Evidence.workspace_id == ws.id).order_by(Evidence.id.desc()).limit(20)
        )
    ).scalars().all()

    # 整体估计: 证据加权平均(展示用), 找证据最多的域做焦点
    focus = max(states, key=lambda s: s.evidence_coverage, default=None)
    total_ev = sum(s.evidence_coverage for s in states)
    estimated = [s for s in states if s.estimated_level is not None]
    estimated_coverage = sum(s.evidence_coverage for s in estimated)
    overall = (round(sum(s.estimated_level * s.evidence_coverage for s in estimated) / estimated_coverage, 1)
               if estimated_coverage else None)
    overall_conf = round(sum(s.confidence for s in states) / max(1, len(states)), 2)

    return {
        "overall": {"level": overall, "confidence": overall_conf, "evidence_total": total_ev},
        "focus": {"key": focus.skill.key, "name": focus.skill.name, "level": focus.estimated_level} if focus else None,
        "domains": [
            {
                "key": s.skill.key,
                "name": s.skill.name,
                "level": s.estimated_level,
                "status": s.status,
                "confidence": s.confidence,
                "evidence": s.evidence_coverage,
                "dimension_scores": s.dimension_scores,
                "last_assessed_at": s.last_assessed_at.isoformat() if s.last_assessed_at else None,
            }
            for s in states
        ],
        "evidence": [
            {
                "short_code": e.short_code,
                "title": e.title,
                "kind": e.kind,
                "grade": e.grade,
                "detail": e.detail,
                "source_ref": e.source_ref,
                "observation": e.meta if isinstance(e.meta, dict) else None,
                "skill": e.skill.name if e.skill else None,
                "created_at": e.created_at.isoformat(),
            }
            for e in evidence
        ],
    }


@router.get("/profile/{skill_key}")
async def skill_detail(
    skill_key: str,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    states = await recompute_skill_states(session, ws.id)
    state = next((s for s in states if s.skill.key == skill_key), None)
    if state is None:
        raise HTTPException(status_code=404, detail=f"skill '{skill_key}' not found")

    evidence = (
        await session.execute(
            select(Evidence)
            .where(Evidence.workspace_id == ws.id, Evidence.skill_id == state.skill_id)
            .order_by(Evidence.id.desc())
        )
    ).scalars().all()

    return {
        "skill": {"key": state.skill.key, "name": state.skill.name, "description": state.skill.description},
        "state": {
            "level": state.estimated_level,
            "confidence": state.confidence,
            "status": state.status,
            "evidence_coverage": state.evidence_coverage,
            "dimension_scores": state.dimension_scores,
            "model_version": state.model_version,
        },
        "evidence": [
            {
                "short_code": e.short_code,
                "title": e.title,
                "kind": e.kind,
                "grade": e.grade,
                "detail": e.detail,
                "source_ref": e.source_ref,
                "observation": e.meta if isinstance(e.meta, dict) else None,
                "created_at": e.created_at.isoformat(),
            }
            for e in evidence
        ],
    }
