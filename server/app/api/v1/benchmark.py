"""岗位对标 API: 种子标准 / JD 导入与对标版本生成。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import JdSource, JobStandard, Skill, Workspace
from app.services.benchmark_jd import build_targets, parse_jd
from app.services.competency import recompute_skill_states
from app.services.tenant_standard import tenant_standard, uses_tenant_standard
from app.services.tracing import add_span, emit_event, finish_trace, start_trace

router = APIRouter()

NEXT_STEP = {
    "UNKNOWN": "先做一次独立诊断",
    "NEEDS_VALIDATION": "补充一次专项验证",
    "BELOW_TARGET": "进入学习计划补齐差距",
    "MEETS_TARGET": "保持: 安排延迟复测",
    "ABOVE_TARGET": "维持现状或挑战更高职级",
}


def _standard_note(standard: JobStandard, tenant: bool) -> str:
    if standard.is_seed:
        return "种子假设 · 非统计结论"
    if tenant:
        return f"参考 {standard.jd_sample_count} 份 JD · 权重仍为种子假设"
    return f"基于 {standard.jd_sample_count} 份 JD 提炼 · 可回退种子"


@router.get("/benchmark")
async def benchmark(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    tenant = uses_tenant_standard(ws)
    standard = await tenant_standard(session, ws, persist_seed=True) if tenant else (
        await session.execute(
            select(JobStandard).order_by(JobStandard.is_seed.asc(), JobStandard.id.desc()).limit(1)
        )
    ).scalar_one_or_none()

    if standard is None:
        return {"standard": None, "rows": []}

    states = await recompute_skill_states(session, ws.id, standard=standard if tenant else None)
    rows = []
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
            "note": _standard_note(standard, tenant),
        },
        "rows": rows,
    }


# ---------------- JD 导入与对标版本 ----------------

class JdIn(BaseModel):
    text: str = Field(min_length=30, max_length=20000)
    title: str | None = None
    company: str | None = None
    role: str | None = None
    level: str | None = None
    apply: bool = True  # 是否生成并启用对标版本


@router.post("/benchmark/jd")
async def import_jd(
    body: JdIn,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    text = body.text.strip()
    if len(text) < 30:
        raise HTTPException(status_code=422, detail="JD 原文太短（至少 30 字）")

    trace = await start_trace(session, ws.id, "benchmark_jd", "导入 JD 并生成对标版本", trigger="manual")
    try:
        tenant = uses_tenant_standard(ws)
        base = await tenant_standard(session, ws) if tenant else None
        skills = [target.skill for target in base.targets] if base else (
            (await session.execute(select(Skill).order_by(Skill.id))).scalars().all()
        )
        parsed = parse_jd(text, [{"key": s.key, "name": s.name} for s in skills])

        jd = JdSource(
            workspace_id=ws.id,
            title=body.title or (body.company or "未命名 JD"),
            company=body.company,
            role=base.role if tenant else body.role,
            level=base.level if tenant else body.level,
            source_type="paste",
            raw_text=text,
            parsed=parsed,
            status="PARSED",
        )
        session.add(jd)
        await session.flush()
        add_span(session, trace, "解析 JD", duration_ms=0, detail={"skill_hits": parsed["skill_hits"], "requirements": len(parsed["requirements"])})

        applied = {"changes": [], "version": None}
        if body.apply:
            base = base or (
                await session.execute(
                    select(JobStandard).order_by(JobStandard.is_seed.asc(), JobStandard.id.desc()).limit(1)
                )
            ).scalar_one_or_none()
            if base is None:
                raise HTTPException(status_code=409, detail="没有可用的基准标准（种子标准缺失）")
            base_targets = [
                {
                    "skill_id": t.skill_id,
                    "skill_key": t.skill.key,
                    "skill_name": t.skill.name,
                    "weight_pct": t.weight_pct,
                    "target_level": t.target_level,
                }
                for t in base.targets
            ]
            new_targets, changes = build_targets(base_targets, parsed)
            version = f"jd-{jd.id}"
            exists = (
                await session.execute(
                    select(JobStandard).where(JobStandard.role == base.role, JobStandard.level == base.level, JobStandard.version == version)
                )
            ).scalar_one_or_none()
            if exists is None:
                std = JobStandard(
                    role=base.role if tenant else (body.role or base.role),
                    level=base.level if tenant else (body.level or base.level),
                    version=version,
                    jd_sample_count=base.jd_sample_count + 1,
                    is_seed=False,
                )
                session.add(std)
                await session.flush()
                from app.models import JobStandardTarget

                for t in new_targets:
                    session.add(JobStandardTarget(standard_id=std.id, skill_id=t["skill_id"], weight_pct=t["weight_pct"], target_level=t["target_level"]))
                await session.flush()
                applied = {"changes": changes, "version": version, "standard_id": std.id}
            else:
                applied = {"changes": changes, "version": version, "standard_id": exists.id, "note": "该 JD 已生成过对标版本"}

            jd.status = "APPLIED"

        await session.commit()
        add_span(session, trace, "生成对标版本", duration_ms=0, detail=applied)
        await finish_trace(session, trace, "SUCCESS")
        await emit_event(session, ws.id, "benchmark_updated", {"jd_id": jd.id, "version": applied.get("version"), "changes": applied.get("changes", [])}, ref_type="jd", ref_id=jd.id)
        await session.commit()
        return {
            "id": jd.id,
            "title": jd.title,
            "status": jd.status,
            "parsed": parsed,
            "applied": applied,
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        await session.rollback()
        await finish_trace(session, trace, "FAILED", fail_reason=str(e)[:300])
        await session.commit()
        raise HTTPException(status_code=500, detail=f"JD 解析失败: {e}") from e


@router.get("/benchmark/jds")
async def list_jds(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (await session.execute(
            select(JdSource).where(JdSource.workspace_id == ws.id).order_by(JdSource.id.desc()).limit(20)
        )).scalars().all()
    )
    return {
        "items": [
            {
                "id": j.id,
                "title": j.title,
                "company": j.company,
                "role": j.role,
                "level": j.level,
                "status": j.status,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "skill_hits": (j.parsed or {}).get("skill_hits", []),
            }
            for j in rows
        ]
    }
