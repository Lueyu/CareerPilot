"""Learning Planner(规则引擎版)。

从能力差距生成今日任务 + 1/3/7 天复测链:
- BELOW_TARGET/NEEDS_VALIDATION 域按 weight×gap 优先;
- 每域生成: 1 个理解任务或实践任务 + 7 天内复测轮次;
- 任务带因果说明(为什么学: 引用证据/差距)。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobStandard, JobStandardTarget, LearningTask, SkillState

TASK_TEMPLATES: dict[str, dict] = {
    "UNDERSTAND": {
        "title": "吃透 {skill}:核心概念与权衡",
        "why": "{skill} 证据不足或低于目标(当前 {level}/5, 目标 {target}/5)",
        "steps": [
            "阅读材料并用自己的话写出 3 个核心概念",
            "针对每个概念写一个反例(什么时候不该这么做)",
            "向 AI 讲解一遍, 直到它能挑不出错",
        ],
        "acceptance": "能脱稿向他人讲清 3 个核心概念及适用边界",
        "est_minutes": 25,
    },
    "PRACTICE": {
        "title": "实践: {skill} 的最小可用实现",
        "why": "{skill} 缺少工程实践证据(当前 {level}/5, 目标 {target}/5)",
        "steps": [
            "完成一个最小可运行的实现或代码练习",
            "记录 1 个踩坑与解决过程",
            "把代码/笔记链接登记为证据",
        ],
        "acceptance": "有可运行的产出物, 并登记为一条新证据",
        "est_minutes": 45,
    },
    "RETEST": {
        "title": "复测: {skill} 概念保持检查",
        "why": "间隔复测: 巩固 {skill} 的记忆保持(艾宾浩斯 1/3/7 天)",
        "steps": [
            "不看材料, 默写 {skill} 的核心概念",
            "与原任务笔记对照, 标出遗漏",
            "遗漏部分重新学习一遍",
        ],
        "acceptance": "默写覆盖 80% 以上要点",
        "est_minutes": 15,
    },
}

RETEST_OFFSETS = {1: "1 天后", 3: "3 天后", 7: "7 天后"}


async def plan_tasks_for_workspace(session: AsyncSession, workspace_id: int, limit: int = 3) -> list[LearningTask]:
    """为工作空间生成今日学习任务(幂等: 已有 PENDING/IN_PROGRESS 的域不重复生成)。"""
    standard = (
        await session.execute(
            select(JobStandard).where(JobStandard.is_seed.is_(True)).order_by(JobStandard.id.desc())
        )
    ).scalar_one_or_none()
    targets: dict[int, tuple[float, int]] = {}  # skill_id -> (target_level, weight)
    if standard:
        rows = (
            await session.execute(
                select(JobStandardTarget).where(JobStandardTarget.standard_id == standard.id)
            )
        ).scalars().all()
        targets = {t.skill_id: (t.target_level, t.weight_pct) for t in rows}

    states = (
        await session.execute(select(SkillState).where(SkillState.workspace_id == workspace_id))
    ).scalars().all()

    existing = (
        await session.execute(
            select(LearningTask)
            .where(
                LearningTask.workspace_id == workspace_id,
                LearningTask.status.in_(("PENDING", "IN_PROGRESS")),
                LearningTask.retest_round == 0,
            )
        )
    ).scalars().all()
    busy_skills = {t.skill_id for t in existing}

    # 差距优先级: weight × gap
    gaps: list[tuple[float, SkillState]] = []
    for s in states:
        if s.skill_id in busy_skills:
            continue
        target, weight = targets.get(s.skill_id, (3.0, 10))
        level = s.estimated_level if s.estimated_level is not None else 1.0
        if s.estimated_level is None or s.status in (SkillState.STATUS_BELOW_TARGET, SkillState.STATUS_NEEDS_VALIDATION):
            gaps.append((weight * max(0.5, target - level), s))
    gaps.sort(key=lambda x: -x[0])

    today = dt.date.today()
    created: list[LearningTask] = []
    for _prio, state in gaps[:limit]:
        target, _w = targets.get(state.skill_id, (3.0, 10))
        level = state.estimated_level if state.estimated_level is not None else 0.0
        skill_name = state.skill.name if state.skill else "目标技能"
        kind = "PRACTICE" if level >= 2.0 else "UNDERSTAND"
        tpl = TASK_TEMPLATES[kind]
        task = LearningTask(
            workspace_id=workspace_id,
            skill_id=state.skill_id,
            title=tpl["title"].format(skill=skill_name),
            kind=kind,
            why=tpl["why"].format(skill=skill_name, level=level or "未知", target=target),
            why_ref=f"EV-{state.evidence_coverage}",
            content={"steps": [s.format(skill=skill_name) for s in tpl["steps"]], "principles": [], "verify": tpl["acceptance"]},
            est_minutes=tpl["est_minutes"],
            acceptance=tpl["acceptance"],
            status=LearningTask.STATUS_PENDING,
        )
        session.add(task)
        created.append(task)

        # 复测链 1/3/7 天
        for round_no, offset in enumerate((1, 3, 7), start=1):
            rtpl = TASK_TEMPLATES["RETEST"]
            session.add(
                LearningTask(
                    workspace_id=workspace_id,
                    skill_id=state.skill_id,
                    title=rtpl["title"].format(skill=skill_name),
                    kind="RETEST",
                    why=rtpl["why"].format(skill=skill_name),
                    content={"steps": [s.format(skill=skill_name) for s in rtpl["steps"]]},
                    est_minutes=rtpl["est_minutes"],
                    acceptance=rtpl["acceptance"],
                    due_date=today + dt.timedelta(days=offset),
                    retest_of=None,  # flush 后回填
                    retest_round=round_no,
                    status=LearningTask.STATUS_PENDING,
                )
            )

    await session.flush()
    # 回填复测链的 retest_of
    all_tasks = (
        await session.execute(
            select(LearningTask).where(
                LearningTask.workspace_id == workspace_id,
                LearningTask.retest_round > 0,
                LearningTask.retest_of.is_(None),
            )
        )
    ).scalars().all()
    main_by_skill = {t.skill_id: t for t in created}
    for t in all_tasks:
        if t.skill_id in main_by_skill:
            t.retest_of = main_by_skill[t.skill_id].id
    await session.flush()
    return created
