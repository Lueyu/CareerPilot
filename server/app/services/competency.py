"""Competency Engine(规则引擎版, LLM 可替换)。

从证据计算 SkillState:
- estimated_level = clamp(0, 5, 基线 + Σ 证据支撑)
- confidence = min(1, 0.2 + 0.08 × 高等级证据数 + 0.04 × 其余证据数)
- status 按 (estimated_level vs target) + 证据覆盖判定:
    * level is None -> UNKNOWN
    * 证据覆盖 < 2 且 confidence < 0.4 -> NEEDS_VALIDATION
    * level + 0.25 <= target -> BELOW_TARGET
    * level - 0.25 > target -> ABOVE_TARGET
    * 否则 -> MEETS_TARGET
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Evidence, JobStandard, JobStandardTarget, Skill, SkillState

GRADE_WEIGHT = {
    "VERIFIED": 1.0,
    "HIGH": 0.8,
    "MEDIUM": 0.5,
    "LOW": 0.3,
    "UNVERIFIED_CLAIM": 0.15,
}

BASELINE = 2.5  # 无证据时的中性基线


async def recompute_skill_states(session: AsyncSession, workspace_id: int) -> list[SkillState]:
    """重算工作空间全部技能状态; 返回最新状态列表。"""
    skills = (await session.execute(select(Skill).order_by(Skill.id))).scalars().all()
    standard = (
        await session.execute(
            select(JobStandard)
            .where(JobStandard.role == "AI Agent 应用开发工程师", JobStandard.is_seed.is_(True))
            .order_by(JobStandard.id.desc())
        )
    ).scalar_one_or_none()

    targets: dict[int, float] = {}
    if standard:
        rows = (
            await session.execute(select(JobStandardTarget).where(JobStandardTarget.standard_id == standard.id))
        ).scalars().all()
        targets = {t.skill_id: t.target_level for t in rows}

    evidences = (
        await session.execute(
            select(Evidence).where(Evidence.workspace_id == workspace_id, Evidence.skill_id.is_not(None))
        )
    ).scalars().all()

    by_skill: dict[int, list[Evidence]] = {}
    for ev in evidences:
        by_skill.setdefault(ev.skill_id, []).append(ev)

    states: dict[int, SkillState] = {
        s.skill_id: s
        for s in (
            await session.execute(select(SkillState).where(SkillState.workspace_id == workspace_id))
        ).scalars().all()
    }

    import datetime as _dt

    result: list[SkillState] = []
    for skill in skills:
        evs = by_skill.get(skill.id, [])
        state = states.get(skill.id)
        if state is None:
            state = SkillState(workspace_id=workspace_id, skill_id=skill.id)
            session.add(state)

        if not evs:
            state.estimated_level = None
            state.confidence = 0.0
            state.evidence_coverage = 0
            state.status = SkillState.STATUS_UNKNOWN
            state.dimension_scores = None
            state.last_assessed_at = None
        else:
            level = BASELINE + sum(e.support * GRADE_WEIGHT.get(e.grade, 0.5) for e in evs)
            level = max(0.0, min(5.0, round(level, 2)))
            high = sum(1 for e in evs if e.grade in ("VERIFIED", "HIGH"))
            conf = min(1.0, 0.2 + 0.08 * high + 0.04 * (len(evs) - high))
            target = targets.get(skill.id, 3.0)
            state.estimated_level = level
            state.confidence = round(conf, 2)
            state.evidence_coverage = len(evs)
            if len(evs) < 2 and conf < 0.4:
                state.status = SkillState.STATUS_NEEDS_VALIDATION
            elif level + 0.25 <= target:
                state.status = SkillState.STATUS_BELOW_TARGET
            elif level - 0.25 > target:
                state.status = SkillState.STATUS_ABOVE_TARGET
            else:
                state.status = SkillState.STATUS_MEETS_TARGET
            state.dimension_scores = _dimension_scores(level, evs)
            state.last_assessed_at = max(e.created_at for e in evs)
        state.model_version = "rule-v1"
        result.append(state)

    await session.flush()
    return result


def _dimension_scores(level: float, evs: list[Evidence]) -> dict[str, float]:
    """维度分解: 理论理解/工程实践/问题诊断/表达沟通/AI协作(规则近似)。"""
    kinds = {e.kind for e in evs}
    prac = 1 if any(k in ("PROJECT_COMMIT", "PRACTICE_TASK") for k in kinds) else 0
    diag = 1 if any(k == "EXAM_ANSWER" for k in kinds) else 0
    ai = 1 if any(k == "MEMORY_NOTE" for k in kinds) else 0
    return {
        "理论理解": round(max(0.0, min(5.0, level)), 2),
        "工程实践": round(max(0.0, min(5.0, level - 0.5 + prac * 0.6)), 2),
        "问题诊断": round(max(0.0, min(5.0, level - 0.3 + diag * 0.4)), 2),
        "表达沟通": round(max(0.0, min(5.0, level - 0.4)), 2),
        "AI协作": round(max(0.0, min(5.0, level - 0.6 + ai * 0.5)), 2),
    }
