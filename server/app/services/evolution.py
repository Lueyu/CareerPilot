"""进化实验室(最小实现): 策略候选生成 + 确定性评估 + 版本管理。

候选来源全部是真实 DB 信号(确定性规则, 不用 LLM 编故事):
- 复测完成率 / 任务完成率 / 历史考试得分率;
- 评估基于真实历史考试指标 + 当前技能状态与题库的确定性投影;
- 样本不足 → INSUFFICIENT_EVIDENCE(诚实标注, 不给假结论)。

状态机: PROPOSED → EVALUATED →(人工批准)→ ACTIVE →(回滚)→ ROLLED_BACK; 也可 REJECTED。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EvolutionCandidate,
    EvolutionRun,
    Exam,
    LearningTask,
    Question,
    SkillState,
)
from app.services.tracing import emit_event

STATUS_PROPOSED = "PROPOSED"
STATUS_EVALUATED = "EVALUATED"
STATUS_ACTIVE = "ACTIVE"
STATUS_REJECTED = "REJECTED"
STATUS_ROLLED_BACK = "ROLLED_BACK"

OPEN_STATUSES = (STATUS_PROPOSED, "TESTING", STATUS_EVALUATED, STATUS_ACTIVE)

_MIN_EXAMS_FOR_PROPOSE = 3
_MIN_EXAMS_FOR_VERDICT = 5


async def _next_evo_code(session: AsyncSession) -> str:
    n = (
        await session.execute(select(func.count(EvolutionCandidate.id)))
    ).scalar_one()
    return f"EVO-{n + 1:04d}"


# ---------------------------------------------------------------- 候选生成
async def collect_signals(session: AsyncSession, workspace_id: int) -> dict[str, Any]:
    """从真实 DB 收集策略信号(确定性)。"""
    exams = (
        await session.execute(
            select(Exam)
            .where(Exam.workspace_id == workspace_id)
            .where(Exam.status == Exam.STATUS_COMPLETED, Exam.kind != 'practice')
            .order_by(Exam.id)
        )
    ).scalars().all()
    ratios = []
    for e in exams:
        if e.total_points and e.total_points > 0 and e.gained_points is not None:
            ratios.append(e.gained_points / e.total_points)
        elif e.score is not None:
            ratios.append(e.score / 100.0)
    avg_ratio = sum(ratios) / len(ratios) if ratios else None

    tasks = (
        await session.execute(
            select(LearningTask.kind, LearningTask.status).where(
                LearningTask.workspace_id == workspace_id
            )
        )
    ).all()
    retest_total = sum(1 for k, _s in tasks if k == "RETEST")
    retest_done = sum(1 for k, s in tasks if k == "RETEST" and s == LearningTask.STATUS_DONE)
    task_total = len(tasks)
    task_done = sum(1 for _k, s in tasks if s == LearningTask.STATUS_DONE)

    return {
        "completed_exams": len(exams),
        "avg_score_ratio": avg_ratio,
        "retest_total": retest_total,
        "retest_done": retest_done,
        "retest_rate": (retest_done / retest_total) if retest_total else None,
        "task_total": task_total,
        "task_done": task_done,
        "task_rate": (task_done / task_total) if task_total else None,
    }


async def propose_candidate(session: AsyncSession, workspace_id: int) -> dict[str, Any]:
    """按真实信号确定性生成一个候选; 无信号或已有开放候选时诚实返回不造。"""
    signals = await collect_signals(session, workspace_id)

    existing = (
        await session.execute(
            select(EvolutionCandidate).where(
                EvolutionCandidate.workspace_id == workspace_id,
                EvolutionCandidate.status.in_(OPEN_STATUSES),
            )
        )
    ).scalars().first()
    if existing is not None:
        return {
            "proposed": False,
            "reason": f"已有进行中的候选 {existing.short_code}（{existing.status}），先处理它再生成新候选。",
            "candidate": existing,
            "signals": signals,
        }

    if signals["completed_exams"] < _MIN_EXAMS_FOR_PROPOSE:
        return {
            "proposed": False,
            "reason": (
                f"样本不足：已完成考试 {signals['completed_exams']} 次（需要 ≥{_MIN_EXAMS_FOR_PROPOSE}）。"
                "先在考试中心多完成几次独立诊断，系统才会基于真实数据生成策略候选。"
            ),
            "candidate": None,
            "signals": signals,
        }

    avg = signals["avg_score_ratio"]
    retest_rate = signals["retest_rate"]
    kind: str | None = None
    title = ""
    body: dict[str, Any] = {}
    rationale = ""

    if avg is not None and avg < 0.6:
        kind = "exam_selection"
        title = "考试选题策略 v3 · 降难度优先"
        body = {
            "baseline": {"rule": "rule-v1", "difficulty_bias": 0},
            "candidate": {"rule": "rule-v1", "difficulty_bias": 1, "prefer_unknown": True},
        }
        rationale = (
            f"近 {signals['completed_exams']} 次考试平均得分率 {avg:.0%} 偏低（<60%）。"
            "候选策略在每域内选择更基础的一题并优先未知域，先建立覆盖再加深。"
        )
    elif avg is not None and avg >= 0.85:
        kind = "exam_selection"
        title = "考试选题策略 v3 · 提难度上限"
        body = {
            "baseline": {"rule": "rule-v1", "difficulty_bias": 0},
            "candidate": {"rule": "rule-v1", "difficulty_bias": -1},
        }
        rationale = (
            f"近 {signals['completed_exams']} 次考试平均得分率 {avg:.0%} 已很高（≥85%）。"
            "候选策略在每域内选择更进阶的一题，把考试预算花在更接近目标等级的题目上。"
        )
    elif retest_rate is not None and retest_rate < 0.5 and signals["retest_total"] >= 3:
        kind = "retest_cadence"
        title = "复测节奏 v2 · 缩短间隔"
        body = {
            "baseline": {"interval_days": [1, 3, 7]},
            "candidate": {"interval_days": [1, 2, 4]},
        }
        rationale = (
            f"复测任务完成率 {retest_rate:.0%} 偏低（{signals['retest_done']}/{signals['retest_total']}）。"
            "候选策略把 3/7 天复测缩短为 2/4 天，降低遗忘窗口。"
        )
    else:
        return {
            "proposed": False,
            "reason": "当前策略表现正常（得分率与复测完成率均未触发阈值），暂无新候选。这是诚实结论，不硬造实验。",
            "candidate": None,
            "signals": signals,
        }

    cand = EvolutionCandidate(
        workspace_id=workspace_id,
        short_code=await _next_evo_code(session),
        title=title,
        kind=kind,
        status=STATUS_PROPOSED,
        body=body,
        rationale=rationale,
        source_data={"signals": {k: v for k, v in signals.items() if v is not None}},
    )
    session.add(cand)
    await session.flush()
    await emit_event(
        session, workspace_id, "evolution.proposed",
        payload={"short_code": cand.short_code, "kind": kind, "title": title},
        ref_type="evolution_candidate", ref_id=cand.id,
    )
    return {"proposed": True, "reason": rationale, "candidate": cand, "signals": signals}


# ---------------------------------------------------------------- 评估
async def _exam_history_metrics(session: AsyncSession, workspace_id: int) -> dict[str, Any]:
    """历史考试真实指标: 平均得分率 / 平均覆盖域数。"""
    exams = (
        await session.execute(
            select(Exam).where(
                Exam.workspace_id == workspace_id, Exam.status == Exam.STATUS_COMPLETED, Exam.kind != 'practice'
            )
        )
    ).scalars().all()
    ratios = [
        e.gained_points / e.total_points
        for e in exams
        if e.total_points and e.gained_points is not None
    ]
    avg_ratio = sum(ratios) / len(ratios) if ratios else None
    return {"sample_size": len(exams), "avg_score_ratio": avg_ratio}


async def _project_selection_metrics(
    session: AsyncSession, workspace_id: int, cfg: dict[str, Any], count: int = 3
) -> dict[str, Any]:
    """用当前技能状态+真实题库对某策略配置做确定性投影。

    返回: 弱域命中数(选中的题里覆盖 UNKNOWN/BELOW_TARGET 域的数量)与覆盖域数。
    这是投影(projection), 不是线上实验结果 — 前端会明确标注。
    """
    states = (
        await session.execute(
            select(SkillState).where(SkillState.workspace_id == workspace_id)
        )
    ).scalars().all()
    priority: dict[int | None, int] = {}
    for s in states:
        if s.estimated_level is None:
            priority[s.skill_id] = 0
        elif s.status in (SkillState.STATUS_NEEDS_VALIDATION, SkillState.STATUS_BELOW_TARGET):
            priority[s.skill_id] = 1
        else:
            priority[s.skill_id] = 2

    pool = (
        await session.execute(
            select(Question)
            .where(Question.workspace_id.is_(None) | (Question.workspace_id == workspace_id))
            .where(Question.qtype.in_(("mcq", "open")))
            .order_by(Question.id)
        )
    ).scalars().all()
    by_skill: dict[int | None, list[Question]] = {}
    for q in pool:
        if (q.generation_meta or {}).get('assessment_use') == 'PRACTICE_ONLY' or (q.generation_meta or {}).get('review_required'):
            continue
        by_skill.setdefault(q.skill_id, []).append(q)

    bias = int(cfg.get("difficulty_bias") or 0)  # +1=更基础(索引后移), -1=更进阶
    rows: list[tuple[int, int | None, list[Question]]] = []
    for skill_id, qs in by_skill.items():
        prio = priority.get(skill_id, 0)
        if cfg.get("prefer_unknown"):
            prio = 0 if priority.get(skill_id, 0) == 0 else prio
        rows.append((prio, skill_id, qs))
    rows.sort(key=lambda x: x[0])

    picked: list[Question] = []
    for _prio, _sid, qs in rows:
        if len(picked) >= count:
            break
        idx = min(max(bias, 0), len(qs) - 1) if bias > 0 else min(max(len(qs) + bias, 0), len(qs) - 1)
        picked.append(qs[idx])

    weak_hits = sum(1 for q in picked if priority.get(q.skill_id, 2) <= 1)
    return {"weak_domain_hits": weak_hits, "coverage": len({q.skill_id for q in picked})}


async def evaluate_candidate(
    session: AsyncSession, workspace_id: int, cand: EvolutionCandidate
) -> EvolutionRun:
    """对候选做确定性评估, 写 EvolutionRun; 样本不足 → INSUFFICIENT_EVIDENCE。"""
    if cand.kind == 'teaching' and (cand.body or {}).get('policy_version') == 'learning-feedback-v1':
        from app.services.learning_memory import evaluate_teaching_candidate
        return await evaluate_teaching_candidate(session, workspace_id, cand)
    hist = await _exam_history_metrics(session, workspace_id)
    sample_size = hist["sample_size"]
    body = cand.body or {}
    base_cfg = body.get("baseline") or {}
    cand_cfg = body.get("candidate") or {}

    base_proj = await _project_selection_metrics(session, workspace_id, base_cfg)
    cand_proj = await _project_selection_metrics(session, workspace_id, cand_cfg)

    metrics: dict[str, Any] = {
        "弱域命中率": {
            "baseline": base_proj["weak_domain_hits"],
            "candidate": cand_proj["weak_domain_hits"],
            "delta": round(cand_proj["weak_domain_hits"] - base_proj["weak_domain_hits"], 2),
            "note": "当前技能状态+题库的确定性投影",
        },
        "覆盖域数": {
            "baseline": base_proj["coverage"],
            "candidate": cand_proj["coverage"],
            "delta": round(cand_proj["coverage"] - base_proj["coverage"], 2),
        },
    }
    if hist["avg_score_ratio"] is not None:
        metrics["历史平均得分率"] = {
            "baseline": round(hist["avg_score_ratio"], 3),
            "candidate": None,
            "delta": None,
            "note": "历史真实值; 候选为投影策略, 无线上得分",
        }

    if sample_size < _MIN_EXAMS_FOR_VERDICT:
        verdict = "INSUFFICIENT_EVIDENCE"
        regression: dict[str, float] | None = None
        cov_delta = metrics["覆盖域数"]["delta"]
    else:
        weak_delta = metrics["弱域命中率"]["delta"]
        cov_delta = metrics["覆盖域数"]["delta"]
        regression = {"覆盖域数": cov_delta} if cov_delta < 0 else None
        if regression is not None:
            verdict = "NEGATIVE"
        elif weak_delta > 0:
            verdict = "POSITIVE"
        else:
            verdict = "INSUFFICIENT_EVIDENCE"

    run = EvolutionRun(
        workspace_id=workspace_id,
        candidate_id=cand.id,
        metrics=metrics,
        sample_size=sample_size,
        regression=regression,
        verdict=verdict,
        evaluated_at=func.now(),
    )
    session.add(run)
    if cand.status == STATUS_PROPOSED:
        cand.status = STATUS_EVALUATED
    await session.flush()
    await emit_event(
        session, workspace_id, "evolution.evaluated",
        payload={"short_code": cand.short_code, "verdict": verdict, "sample_size": sample_size},
        ref_type="evolution_candidate", ref_id=cand.id,
    )
    return run


# ---------------------------------------------------------------- 状态流转
async def approve_candidate(session: AsyncSession, workspace_id: int, cand: EvolutionCandidate) -> None:
    raise PermissionError("evolution_activation_disabled: 本轮仅保留候选与评估记录")


async def reject_candidate(session: AsyncSession, workspace_id: int, cand: EvolutionCandidate) -> None:
    cand.status = STATUS_REJECTED
    await session.flush()
    await emit_event(
        session, workspace_id, "evolution.rejected",
        payload={"short_code": cand.short_code}, ref_type="evolution_candidate", ref_id=cand.id,
    )


async def rollback_candidate(session: AsyncSession, workspace_id: int, cand: EvolutionCandidate) -> None:
    cand.status = STATUS_ROLLED_BACK
    await session.flush()
    await emit_event(
        session, workspace_id, "evolution.rolled_back",
        payload={"short_code": cand.short_code}, ref_type="evolution_candidate", ref_id=cand.id,
    )


async def get_active_candidate(
    session: AsyncSession, workspace_id: int, kind: str
) -> EvolutionCandidate | None:
    """本轮不消费策略, 包括数据库中历史 ACTIVE 版本; 原记录保持完整。"""
    return None
