"""自适应选题(规则引擎版)。

独立诊断选题策略(可解释):
1. 目标: 最大化信息增益 — 优先挑「状态未知/待验证/低置信」能力域的题;
2. 每域最多 1 题, 保证覆盖面; 数量不足时从 BELOW_TARGET 域补;
3. 同域内按难度与目标等级接近度排序;
4. 输出 selection_reason 说明为什么选这些题。
"""
from __future__ import annotations

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


async def select_questions_for_exam(
    session: AsyncSession, workspace_id: int, exam: Exam, count: int = 3
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
