"""自适应选题(规则引擎版)。

独立诊断选题策略(可解释):
1. 目标: 最大化信息增益 — 优先挑「状态未知/待验证/低置信」能力域的题;
2. 每域最多 1 题, 保证覆盖面; 数量不足时从 BELOW_TARGET 域补;
3. 同域内按难度与目标等级接近度排序;
4. 输出 selection_reason 说明为什么选这些题。
"""
from __future__ import annotations

import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Exam, Question, SkillState
from app.services.evolution import get_active_candidate

INFO_GAIN_ORDER = {
    SkillState.STATUS_UNKNOWN: 0,          # 完全未知, 信息增益最大
    SkillState.STATUS_NEEDS_VALIDATION: 1,  # 待验证
    SkillState.STATUS_BELOW_TARGET: 2,     # 低于目标
    SkillState.STATUS_MEETS_TARGET: 3,
    SkillState.STATUS_ABOVE_TARGET: 4,
}


def covers_criterion(question, title: str | None) -> bool:
    normalize = lambda value: re.sub(r"^(?:要点|评分项)\s*[:：]\s*", "", str(value or "").strip()).casefold()
    return bool(title) and any(normalize(p.get("title") if isinstance(p, dict) else p) == normalize(title)
                               for p in ((question.rubric or {}).get("criteria") or (question.rubric or {}).get("points", [])))


async def select_questions_for_exam(
    session: AsyncSession, workspace_id: int, exam: Exam, count: int = 3,
    *, skill_key: str | None = None, exclude_question_ids: list[int] | None = None,
    criterion_title: str | None = None,
    exclude_exposed: bool = False,
) -> tuple[list[Question], str]:
    """为独立诊断选 N 道题, 返回 (题目列表, 选择理由)。"""
    states = (
        await session.execute(
            select(SkillState).where(SkillState.workspace_id == workspace_id)
        )
    ).scalars().all()

    # 进化实验室: 若该工作空间有生效中的 exam_selection 候选, 应用其配置
    cand = await get_active_candidate(session, workspace_id, "exam_selection")
    cfg: dict = (cand.body or {}).get("candidate") or {} if cand else {}

    # 技能 -> (状态优先级, 置信度)
    priority: dict[int, tuple[int, float]] = {}
    for s in states:
        if s.estimated_level is None:
            prio = INFO_GAIN_ORDER[SkillState.STATUS_UNKNOWN]
        else:
            prio = INFO_GAIN_ORDER.get(s.status, 3)
        priority[s.skill_id] = (prio, s.confidence)

    pool = (
        await session.execute(
            select(Question)
            .where(Question.workspace_id.is_(None) | (Question.workspace_id == workspace_id))
            .where(Question.qtype.in_(("mcq", "open")))
            .order_by(Question.id)
        )
    ).scalars().all()

    # Variants are the same independent question. Scope and exclusions apply
    # before picking, including when an excluded ID is itself a variant.
    by_id = {q.id: q for q in pool}
    def canonical_id(q):
        seen = set()
        while q.canonical_question_id and q.id not in seen:
            seen.add(q.id)
            parent = by_id.get(q.canonical_question_id)
            if parent is None:
                return q.canonical_question_id
            q = parent
        return q.id
    excluded = set(exclude_question_ids or [])
    if exclude_exposed:
        from app.services.learning_exposure import exposed_families
        excluded |= await exposed_families(session, workspace_id)
    excluded |= {canonical_id(by_id[qid]) for qid in list(excluded) if qid in by_id}
    unique_pool = []
    seen_canonical = set()
    for q in pool:
        if (q.generation_meta or {}).get('assessment_use') == 'PRACTICE_ONLY':
            continue
        if (q.rubric or {}).get('review_required'):
            continue
        canonical = canonical_id(q)
        if skill_key and (not q.skill or q.skill.key != skill_key):
            continue
        if q.id in excluded or canonical in excluded or canonical in seen_canonical:
            continue
        seen_canonical.add(canonical)
        unique_pool.append(q)
    pool = unique_pool
    if criterion_title:
        pool.sort(key=lambda q: not covers_criterion(q, criterion_title))

    # 按能力域分桶
    by_skill: dict[int | None, list[Question]] = {}
    for q in pool:
        by_skill.setdefault(q.skill_id, []).append(q)

    def pick_from(buckets: list[tuple[int, list[Question]]]) -> list[Question]:
        picked: list[Question] = []
        for _prio, questions in sorted(buckets, key=lambda x: x[0]):
            if len(picked) >= count:
                break
            q = questions[0]  # 桶内取第一道(种子顺序即难度近似)
            if q not in picked:
                picked.append(q)
        return picked

    # 第一轮: 按信息增益优先级取各域题
    buckets: list[tuple[int, list[Question]]] = []
    for skill_id, questions in by_skill.items():
        prio = priority.get(skill_id, (INFO_GAIN_ORDER[SkillState.STATUS_UNKNOWN], 0.0))[0]
        buckets.append((prio, questions))
    buckets.sort(key=lambda x: x[0])
    picked = pick_from(buckets)

    # 候选策略 difficulty_bias: >0 更基础(桶内索引后移), <0 更进阶(索引前移)
    bias = int(cfg.get("difficulty_bias") or 0)
    if bias != 0:
        shifted: list[Question] = []
        for q in picked:
            qs = by_skill.get(q.skill_id, [q])
            if len(qs) > 1:
                idx = qs.index(q)
                new_idx = min(max(idx + bias, 0), len(qs) - 1)
                shifted.append(qs[new_idx])
            else:
                shifted.append(q)
        picked = shifted

    # 不足则补齐(允许同域第二题)
    i = 0
    while len(picked) < count and i < len(pool):
        if pool[i] not in picked:
            picked.append(pool[i])
        i += 1

    strat = f"rule-v1+{cand.short_code}" if cand and cfg else "rule-v1"
    reason = (
        f"自适应选题({strat}): 优先覆盖状态未知/待验证/低于目标的能力域"
        + (f"; 已应用候选策略「{cand.title}」" if cand and cfg else "")
        + f", 共选 {len(picked)} 题, 覆盖 {len({q.skill_id for q in picked})} 个能力域。"
    )
    return picked[:count], reason
