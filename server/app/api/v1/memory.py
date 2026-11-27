"""记忆中心 API: 记忆时间线 / 确认 / 纠正 / 删除 / 对话导入提取候选。"""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.generation_i18n import with_output_language
from app.i18n import message as localized_message
from app.models import MemoryItem, Workspace
from app.services.bank_review_memory import BANK_REVIEW_FEEDBACK_TYPE, review_feedback_eligible
from app.services.llm import AgentAdapter
from app.services.private_model_billing import BillingScope, BudgetPolicyError
from app.services.project_candidates import eligible
from app.services.project_memory import lock_project_memories
from app.services.task_memory import TASK_COMPLETION_TYPE
from app.services.tracing import emit_event

router = APIRouter()


class CorrectReq(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    expected_revision: int | None = None

    @field_validator('content')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('记忆内容不能为空')
        return value.strip()


class ImportDialogReq(BaseModel):
    text: str


def _personalization_reason(memory: MemoryItem) -> str | None:
    if memory.mtype == BANK_REVIEW_FEEDBACK_TYPE:
        return "仅作为同技能题库生成的过往审核案例"
    if memory.mtype == TASK_COMPLETION_TYPE:
        return "仅记录学习进度"
    if eligible(memory):
        return None
    return "来源已变化，需要复核" if memory.freshness != "CURRENT" else "尚未确认或已被替代"


@router.get("/memory")
async def list_memory(
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(text("SELECT capture_exam_completion_memory(:workspace_id)"), {"workspace_id": ws.id})
    items = (
        await session.execute(
            select(MemoryItem)
            .where(MemoryItem.workspace_id == ws.id, MemoryItem.status != "DISMISSED")
            .order_by(MemoryItem.id.desc())
        )
    ).scalars().all()
    await session.commit()
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
                "revision": m.revision,
                "freshness": m.freshness,
                "eligible_for_personalization": eligible(m),
                "ineligible_reason": _personalization_reason(m),
                "eligible_for_bank_generation": review_feedback_eligible(m),
                "meta": m.meta,
                "superseded_by": m.superseded_by,
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
    await lock_project_memories(session, ws.id)
    m = (
        await session.execute(
            select(MemoryItem).where(MemoryItem.id == item_id, MemoryItem.workspace_id == ws.id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="memory not found")
    if m.mtype in ('ProjectUnderstanding','ProjectIssue','LearningObservation','LearningPreference'):
        raise HTTPException(409, localized_message("errors.review_project_and_learning_candidates_through_the_version_checked"))
    if m.status not in ('CANDIDATE', 'ACTIVE'):
        raise HTTPException(409, localized_message("errors.superseded_or_deleted_memories_cannot_be_reactivated"))
    if m.status == 'ACTIVE':
        return {"id": m.id, "status": m.status}
    m.status = "ACTIVE"
    m.revision += 1
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
    await lock_project_memories(session, ws.id)
    from app.services.learning_memory import LEARNING_TYPES, invalidate_memory_dependents
    memory_type = await session.scalar(select(MemoryItem.mtype).where(MemoryItem.id == item_id, MemoryItem.workspace_id == ws.id))
    if memory_type in LEARNING_TYPES:
        await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
    m = (
        await session.execute(
            select(MemoryItem).where(MemoryItem.id == item_id, MemoryItem.workspace_id == ws.id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="memory not found")
    if m.status != 'ACTIVE':
        raise HTTPException(409, localized_message("errors.only_the_active_memory_can_be_corrected_reload_the"))
    if m.mtype in LEARNING_TYPES and req.expected_revision != m.revision:
        raise HTTPException(409, localized_message("errors.this_study_memory_has_changed_reopen_it_before_making"))
    new = MemoryItem(
        workspace_id=ws.id,
        mtype=m.mtype,
        content=req.content.strip(),
        source="USER",
        source_ref=f'memory:{m.id}',
        meta=dict(m.meta or {}),
        extract_confidence=1.0,
        status="ACTIVE",
        freshness=m.freshness,
        confirmed_at=dt.datetime.now(dt.timezone.utc),
        superseded_by=None,
    )
    session.add(new)
    await session.flush()
    m.status = "SUPERSEDED"
    m.revision += 1
    m.superseded_by = new.id
    if m.mtype in LEARNING_TYPES:
        await invalidate_memory_dependents(session, ws.id, m.id, '用户纠正了所引用的学习记忆')
    await session.commit()
    return {"id": new.id, "superseded": m.id, "status": new.status}


@router.delete("/memory/{item_id}")
async def delete_memory(
    item_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    await lock_project_memories(session, ws.id)
    from app.services.learning_memory import LEARNING_TYPES, invalidate_memory_dependents
    memory_type = await session.scalar(select(MemoryItem.mtype).where(MemoryItem.id == item_id, MemoryItem.workspace_id == ws.id))
    if memory_type in LEARNING_TYPES:
        await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
    m = (
        await session.execute(
            select(MemoryItem).where(MemoryItem.id == item_id, MemoryItem.workspace_id == ws.id).with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if m is None:
        raise HTTPException(status_code=404, detail="memory not found")
    if m.status == 'DISMISSED':
        return {"id": m.id, "status": m.status}
    m.status = "DISMISSED"
    m.revision += 1
    if m.mtype in LEARNING_TYPES:
        await invalidate_memory_dependents(session, ws.id, m.id, '用户撤回了所引用的学习记忆')
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

    candidates = await _extract_memories(text, billing_scope=BillingScope(ws.id, "memory:extract"))
    created = []
    for c in candidates:
        if c["mtype"] == BANK_REVIEW_FEEDBACK_TYPE:
            continue
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


async def _extract_memories(text: str, *, billing_scope=None) -> list[dict]:
    adapter = AgentAdapter()
    if adapter.active:
        sys_prompt = (
            "你是记忆提取引擎。从用户与 AI 的对话中提取值得长期记住的记忆。"
            '返回 JSON 数组, 每项 {"mtype": one of CareerGoal/Preference/KnowledgeGapSignal/ProjectDecision/Feedback, '
            '"content": str(一句话,第三人称), "confidence": 0-1}。不要解释。'
        )
        try:
            out = await adapter.chat(with_output_language(sys_prompt), text[:8000], json_mode=True, billing_scope=billing_scope)
            if out and isinstance(out, list):
                return [x for x in out if isinstance(x, dict) and x.get("content")][:8]
        except BudgetPolicyError:
            raise
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
