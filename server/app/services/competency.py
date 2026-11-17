"""Estimate only from independent assessed questions; retain UNKNOWN below three.

The estimate is a conservative, uncalibrated heuristic. Repeated attempts update
latest performance, while claims and materials remain visible without mastery credit.
"""
from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Evidence, JobStandard, JobStandardTarget, Skill, SkillState

MIN_INDEPENDENT_OBSERVATIONS = 3


def assessed_observations(evidences: list[Evidence], *, include_assisted: bool = False) -> list[Evidence]:
    """Keep the latest assessed attempt per canonical question.

    Old claims/imports/projects remain inspectable evidence, but do not establish
    mastery. The threshold and confidence below are conservative heuristics,
    not a calibrated psychometric model. Learning may inspect valid assisted
    observations with include_assisted=True; competency always uses the default.
    """
    valid: list[Evidence] = []
    parents: dict[int, int] = {}

    def root(question_id: int) -> int:
        parents.setdefault(question_id, question_id)
        while parents[question_id] != question_id:
            parents[question_id] = parents[parents[question_id]]
            question_id = parents[question_id]
        return question_id

    for ev in evidences:
        m = ev.meta if isinstance(ev.meta, dict) else {}
        if ev.kind != "EXAM_ANSWER" or not m.get("assessed"):
            continue
        if m.get('assessment_mode') == 'PRACTICE':
            continue
        assistance = m.get('assistance') or {}
        if not include_assisted and (m.get('independent') is False or any(assistance.get(k)
                for k in ('hint_used', 'reference_viewed', 'feedback_seen', 'ai_declared', 'prior_attempt'))):
            continue
        if not all(m.get(k) for k in ("question_id", "canonical_question_id", "exam_id", "exam_question_id")):
            continue
        if m.get("judge_version") not in ("judge-v1", "rule-v1", "judge-v2", "rule-v2"):
            continue
        if m.get("judge_version") in ("judge-v2", "rule-v2") and m.get("validation_status") != "VALID":
            continue
        if m.get("judge_version") in ("rule-v1", "rule-v2") and m.get("qtype") != "mcq" and not m.get("blank_answer"):
            continue
        try:
            score, maximum = float(m["score"]), float(m["max_points"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(score) or not math.isfinite(maximum) or maximum <= 0 or not 0 <= score <= maximum:
            continue
        valid.append(ev)
        # Canonical IDs can change after bank merges. Historical links are
        # equivalences, so connect both IDs before selecting independent groups.
        parents[root(m["question_id"])] = root(m["canonical_question_id"])

    latest: dict[int, Evidence] = {}
    rank = lambda e: ((e.occurred_at or e.created_at).timestamp(), e.id or 0)
    for ev in valid:
        key = root(ev.meta["question_id"])
        previous = latest.get(key)
        if previous is None or rank(ev) > rank(previous):
            latest[key] = ev
    return list(latest.values())


async def recompute_skill_states(session: AsyncSession, workspace_id: int) -> list[SkillState]:
    """重算工作空间全部技能状态; 返回最新状态列表。"""
    skills = (await session.execute(select(Skill).order_by(Skill.id))).scalars().all()
    standard = (
        await session.execute(
            select(JobStandard)
            .where(JobStandard.role == "AI Agent 应用开发工程师", JobStandard.is_seed.is_(True))
            .order_by(JobStandard.id.desc())
            .limit(1)
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
        evs = assessed_observations(by_skill.get(skill.id, []))
        state = states.get(skill.id)
        if state is None:
            # New states have no loaded relationship even though skill_id is set.
            # Retain the already-loaded Skill for async API serialization.
            state = SkillState(workspace_id=workspace_id, skill_id=skill.id, skill=skill)
            session.add(state)

        if len(evs) < MIN_INDEPENDENT_OBSERVATIONS:
            state.estimated_level = None
            state.confidence = round(0.15 * len(evs), 2)
            state.evidence_coverage = len(evs)
            state.status = SkillState.STATUS_UNKNOWN
            state.dimension_scores = None
            state.last_assessed_at = max((e.occurred_at or e.created_at for e in evs), default=None)
        else:
            level = round(5 * sum(float(e.meta["score"]) / float(e.meta["max_points"]) for e in evs) / len(evs), 2)
            conf = min(0.7, 0.15 * len(evs))
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
            state.dimension_scores = None
            state.last_assessed_at = max(e.occurred_at or e.created_at for e in evs)
        state.model_version = "observation-v2-heuristic"
        result.append(state)

    await session.flush()
    return result
