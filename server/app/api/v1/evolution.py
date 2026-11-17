"""进化实验室 API(最小实现, 全部真实数据)。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import EvolutionCandidate, EvolutionRun, Workspace
from app.services import evolution as evo
from app.services.tracing import finish_trace, start_trace, add_span

router = APIRouter(prefix="/evolution", tags=["evolution"])


def _cand_view(c: EvolutionCandidate, run: EvolutionRun | None) -> dict:
    return {
        "id": c.id,
        "short_code": c.short_code,
        "title": c.title,
        "kind": c.kind,
        "status": c.status,
        "body": c.body,
        "rationale": c.rationale,
        "source_data": c.source_data,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "approved_at": c.approved_at.isoformat() if getattr(c, "approved_at", None) else None,
        "run": {
            "metrics": run.metrics,
            "sample_size": run.sample_size,
            "regression": run.regression,
            "verdict": run.verdict,
        } if run else None,
    }


async def _latest_run(session: AsyncSession, candidate_id: int) -> EvolutionRun | None:
    return (
        await session.execute(
            select(EvolutionRun)
            .where(EvolutionRun.candidate_id == candidate_id)
            .order_by(EvolutionRun.id.desc())
        )
    ).scalars().first()


async def _get_candidate(session: AsyncSession, ws: Workspace, cid: int) -> EvolutionCandidate:
    c = await session.get(EvolutionCandidate, cid)
    if c is None or c.workspace_id != ws.id:
        raise HTTPException(404, "candidate not found")
    return c


@router.get("/candidates")
async def list_candidates(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    cands = (
        await session.execute(
            select(EvolutionCandidate)
            .where(EvolutionCandidate.workspace_id == ws.id)
            .order_by(EvolutionCandidate.id.desc())
        )
    ).scalars().all()
    items = []
    for c in cands:
        run = await _latest_run(session, c.id)
        items.append(_cand_view(c, run))
    return {"items": items}


@router.post("/candidates")
async def create_candidate(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """按真实信号确定性生成候选; 不满足条件时诚实返回 proposed=false。"""
    tr = await start_trace(session, ws.id, "evolution", "生成策略候选", trigger="manual")
    try:
        t0 = time.monotonic()
        result = await evo.propose_candidate(session, ws.id)
        add_span(
            session, tr, "propose_candidate", int((time.monotonic() - t0) * 1000),
            detail={"proposed": result["proposed"], "reason": result["reason"]},
        )
        await session.commit()
        await finish_trace(session, tr, "SUCCESS")
        await session.commit()
        c = result["candidate"]
        run = await _latest_run(session, c.id) if c else None
        return {
            "proposed": result["proposed"],
            "reason": result["reason"],
            "candidate": _cand_view(c, run) if c else None,
            "signals": result["signals"],
        }
    except Exception as e:
        await session.rollback()
        tr.status = "FAILED"
        tr.fail_reason = str(e)[:500]
        await session.commit()
        raise HTTPException(500, f"生成候选失败: {e}")


@router.post("/candidates/{cid}/evaluate")
async def evaluate(
    cid: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    c = await _get_candidate(session, ws, cid)
    if c.status in (evo.STATUS_ROLLED_BACK, evo.STATUS_REJECTED):
        raise HTTPException(409, f"候选已终态({c.status}), 不能评估")
    tr = await start_trace(session, ws.id, "evolution", f"评估 {c.short_code}", trigger="manual")
    try:
        t0 = time.monotonic()
        run = await evo.evaluate_candidate(session, ws.id, c)
        add_span(session, tr, "evaluate_candidate", int((time.monotonic() - t0) * 1000),
                       detail={"verdict": run.verdict, "sample_size": run.sample_size})
        await session.commit()
        await finish_trace(session, tr, "SUCCESS")
        await session.commit()
        await session.refresh(c)  # func.now() 列需异步刷新, 否则视图访问触发 MissingGreenlet
        return _cand_view(c, run)
    except Exception as e:
        await session.rollback()
        tr.status = "FAILED"
        tr.fail_reason = str(e)[:500]
        await session.commit()
        raise HTTPException(500, f"评估失败: {e}")


@router.post("/candidates/{cid}/approve")
async def approve(
    cid: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    raise HTTPException(403, "evolution_activation_disabled: 本轮仅保留候选与评估记录")


@router.post("/candidates/{cid}/reject")
async def reject(
    cid: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    c = await _get_candidate(session, ws, cid)
    if c.status in (evo.STATUS_ROLLED_BACK, evo.STATUS_REJECTED):
        raise HTTPException(409, f"候选已终态({c.status})")
    await evo.reject_candidate(session, ws.id, c)
    await session.commit()
    await session.refresh(c)  # func.now() 列需异步刷新, 否则视图访问触发 MissingGreenlet
    return _cand_view(c, await _latest_run(session, c.id))


@router.post("/candidates/{cid}/rollback")
async def rollback(
    cid: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    c = await _get_candidate(session, ws, cid)
    if c.status != evo.STATUS_ACTIVE:
        raise HTTPException(409, "只有生效中的候选可以回滚")
    await evo.rollback_candidate(session, ws.id, c)
    await session.commit()
    await session.refresh(c)  # func.now() 列需异步刷新, 否则视图访问触发 MissingGreenlet
    return _cand_view(c, await _latest_run(session, c.id))
