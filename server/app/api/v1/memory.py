"""记忆中心 API: 记忆时间线 / 确认 / 纠正 / 删除 / 对话导入提取候选。"""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import MemoryItem, Workspace
from app.services.llm import AgentAdapter
from app.services.tracing import emit_event

router = APIRouter()


class CorrectReq(BaseModel):
    content: str


class ImportDialogReq(BaseModel):
    text: str


@router.get("/memory")
async def list_memory(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    items = (
        await session.execute(
            select(MemoryItem)
            .where(MemoryItem.workspace_id == ws.id, MemoryItem.status != "DISMISSED")
            .order_by(MemoryItem.id.desc())
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": m.id,
                "mtype": m.mtype,
                "content": m.content,
                "source": m.source,
                "source_ref": m.source_ref,
                "extract_confidence": m.extract_confidence,
                "status": m.status,
                "created_at": m.created_at.isoformat(),
                "confirmed_at": m.confirmed_at.isoformat() if m.confirmed_at else None,
            }
            for m in items
        ]
    }


@router.post("/memory/{item_id}/confirm")
async def confirm_memory(
    item_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    m = (
        await session.execute(
            select(MemoryItem).where(MemoryItem.id == item_id, MemoryItem.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="memory not found")
    m.status = "ACTIVE"
    m.confirmed_at = dt.datetime.now(dt.timezone.utc)
    await session.commit()
    return {"id": m.id, "status": m.status}


@router.post("/memory/{item_id}/correct")
async def correct_memory(
    item_id: int,
    req: CorrectReq,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """纠正: 旧条目 SUPERSEDED, 新条目 ACTIVE。"""
    m = (
        await session.execute(
            select(MemoryItem).where(MemoryItem.id == item_id, MemoryItem.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="memory not found")
    new = MemoryItem(
        workspace_id=ws.id,
        mtype=m.mtype,
        content=req.content.strip(),
        source="USER",
        source_ref=m.short_code,
        extract_confidence=1.0,
        status="ACTIVE",
        confirmed_at=dt.datetime.now(dt.timezone.utc),
        superseded_by=None,
    )
    session.add(new)
    await session.flush()
    m.status = "SUPERSEDED"
    m.superseded_by = new.id
    await session.commit()
    return {"id": new.id, "superseded": m.id, "status": new.status}


@router.delete("/memory/{item_id}")
async def delete_memory(
    item_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    m = (
        await session.execute(
            select(MemoryItem).where(MemoryItem.id == item_id, MemoryItem.workspace_id == ws.id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="memory not found")
    m.status = "DISMISSED"
    await session.commit()
    return {"id": m.id, "status": m.status}


@router.post("/memory/import-dialog")
async def import_dialog(
    req: ImportDialogReq,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    """导入对话 → 提取候选记忆(存为 CANDIDATE, 用户确认后转 ACTIVE)。"""
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text required")

    candidates = await _extract_memories(text)
    created = []
    for c in candidates:
        dup = (
            await session.execute(
                select(MemoryItem).where(
                    MemoryItem.workspace_id == ws.id,
                    MemoryItem.status == "CANDIDATE",
                    MemoryItem.mtype == c["mtype"],
                    MemoryItem.content == c["content"],
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            continue  # 同内容候选已存在, 不重复创建
        m = MemoryItem(
            workspace_id=ws.id,
            mtype=c["mtype"],
            content=c["content"],
            source="DIALOG_IMPORT",
            extract_confidence=c["confidence"],
            status="CANDIDATE",
        )
        session.add(m)
        await session.flush()
        created.append(m.id)
    await emit_event(session, ws.id, "memory.candidates", {"count": len(created)}, None, None)
    await session.commit()

    items = (
        await session.execute(
            select(MemoryItem).where(
                MemoryItem.workspace_id == ws.id, MemoryItem.status == "CANDIDATE"
            ).order_by(MemoryItem.id.desc())
        )
    ).scalars().all()
    return {
        "candidates": [
            {
                "id": m.id,
                "mtype": m.mtype,
                "content": m.content,
                "extract_confidence": m.extract_confidence,
                "status": m.status,
            }
            for m in items
        ]
    }


MTYPE_RULES = [
    ("CareerGoal", [r"想(要|成为|去)", r"目标", r"希望.{0,8}(进入|拿到|成为)", r"求职"]),
    ("Preference", [r"喜欢", r"偏好", r"不想", r"习惯"]),
    ("KnowledgeGapSignal", [r"不(太)?(会|懂|熟)", r"没.{0,6}(学|用)过", r"薄弱", r"不了解"]),
    ("ProjectDecision", [r"我们(选|用|决定)", r"选择了", r"架构", r"实现"]),
    ("Feedback", [r"面试官", r"反馈", r"建议我"]),
]


async def _extract_memories(text: str) -> list[dict]:
    adapter = AgentAdapter()
    if adapter.active:
        sys_prompt = (
            "你是记忆提取引擎。从用户与 AI 的对话中提取值得长期记住的记忆。"
            '返回 JSON 数组, 每项 {"mtype": one of CareerGoal/Preference/KnowledgeGapSignal/ProjectDecision/Feedback, '
            '"content": str(一句话,第三人称), "confidence": 0-1}。不要解释。'
        )
        try:
            out = await adapter.chat(sys_prompt, text[:8000], json_mode=True)
            if out and isinstance(out, list):
                return [x for x in out if isinstance(x, dict) and x.get("content")][:8]
        except Exception:
            pass

    # 规则降级: 按句拆 + 关键词分类
    sentences = re.split(r"[。！？\n]", text)
    results = []
    for s in sentences:
        s = s.strip()
        if len(s) < 6:
            continue
        for mtype, patterns in MTYPE_RULES:
            if any(re.search(p, s) for p in patterns):
                results.append({"mtype": mtype, "content": s[:80], "confidence": 0.5})
                break
    return results[:8]
