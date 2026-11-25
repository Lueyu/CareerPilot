"""Review criterion mappings without changing questions or frozen exams."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import message as localized_message
from app.api.deps import get_workspace
from app.db import get_session
from app.models import Question, RetestCriterionMapping, Workspace
from app.services.assessment_versions import question_snapshot

router = APIRouter(prefix="/retest-mappings", tags=["retest-mappings"])


class MappingReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rubric_version: str = Field(min_length=1, max_length=100)
    behavior_key: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    behavior_version: int = Field(ge=1, le=2147483647)
    confirm: bool


async def _question(session, workspace_id, question_id, *, for_update=False):
    query = select(Question).where(
        Question.id == question_id,
        Question.workspace_id.is_(None) | (Question.workspace_id == workspace_id),
        Question.generation_meta["assessment_use"].as_string().is_distinct_from("PRACTICE_ONLY"),
    ).execution_options(populate_existing=True)
    if for_update:
        await session.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())
        query = query.with_for_update(of=Question)
    question = await session.scalar(query)
    if question is None:
        raise HTTPException(404, localized_message("errors.could_not_find_the_question"))
    return question


def _rubric(question):
    if question.qtype != "open":
        raise HTTPException(409, localized_message("errors.retest_behavior_mapping_is_available_only_for_open_ended"))
    if not isinstance(question.rubric, dict):
        raise HTTPException(409, localized_message("errors.this_question_requires_a_valid_scoring_rubric"))
    return question_snapshot(question)["rubric"]


def _question_mappings(workspace_id, question_id):
    return select(RetestCriterionMapping).where(
        RetestCriterionMapping.workspace_id == workspace_id,
        RetestCriterionMapping.question_id == question_id,
    )


def _mappings(workspace_id, question_id, rubric_version):
    return _question_mappings(workspace_id, question_id).where(
        RetestCriterionMapping.rubric_version == rubric_version,
    )


def _criterion_view(criterion, mapping=None):
    return {
        "criterion_id": criterion["criterion_id"],
        "title": criterion["title"],
        "behavior_key": mapping.behavior_key if mapping else None,
        "behavior_version": mapping.behavior_version if mapping else None,
        "reviewed_at": mapping.reviewed_at if mapping else None,
    }


@router.get("/{question_id}")
async def get_review(
    question_id: int,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    question = await _question(session, ws.id, question_id)
    reason = None
    try:
        rubric = _rubric(question)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        rubric = None
        reason = exc.detail
    version = rubric["rubric_version"] if rubric else None
    rows = (await session.scalars(_question_mappings(ws.id, question_id).order_by(
        RetestCriterionMapping.reviewed_at.desc(), RetestCriterionMapping.id.desc(),
    ))).all()
    mappings = {row.criterion_id: row for row in rows if row.rubric_version == version}
    historical_mappings = [
        {"criterion_id": row.criterion_id, "rubric_version": row.rubric_version,
         "behavior_key": row.behavior_key, "behavior_version": row.behavior_version, "reviewed_at": row.reviewed_at}
        for row in rows if row.rubric_version != version
    ]
    return {
        "question_id": question_id, "rubric_version": version, "reviewable": rubric is not None,
        "criteria": [_criterion_view(criterion, mappings.get(criterion["criterion_id"]))
                     for criterion in rubric["criteria"]] if rubric else [],
        "historical_mappings": historical_mappings,
        **({"reason": reason} if reason is not None else {}),
    }


async def _mapping(session, workspace_id, question_id, rubric_version, criterion_id):
    return await session.scalar(_mappings(workspace_id, question_id, rubric_version).where(
        RetestCriterionMapping.criterion_id == criterion_id,
    ))


@router.put("/{question_id}/{criterion_id}")
async def approve_review(
    question_id: int,
    criterion_id: str,
    review: MappingReview,
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    if not review.confirm:
        raise HTTPException(422, localized_message("errors.confirm_that_you_reviewed_the_current_scoring_criteria"))
    question = await _question(session, ws.id, question_id, for_update=True)
    rubric = _rubric(question)
    if rubric["rubric_version"] != review.rubric_version:
        raise HTTPException(409, localized_message("errors.the_rubric_has_changed_read_and_review_it_again"))
    criterion = next((row for row in rubric["criteria"] if row["criterion_id"] == criterion_id), None)
    if criterion is None:
        raise HTTPException(422, localized_message("errors.this_criterion_belongs_to_a_different_rubric"))
    mapping = await _mapping(session, ws.id, question_id, review.rubric_version, criterion_id)
    conflicting = await session.scalar(_mappings(ws.id, question_id, review.rubric_version).where(
        RetestCriterionMapping.behavior_key == review.behavior_key,
        RetestCriterionMapping.behavior_version == review.behavior_version,
        RetestCriterionMapping.criterion_id != criterion_id,
    ))
    if conflicting is not None:
        raise HTTPException(409, f"同一行为版本已绑定评分项 {conflicting.criterion_id}，请先撤销该映射")
    if mapping is None:
        mapping = RetestCriterionMapping(
            workspace_id=ws.id, question_id=question_id, rubric_version=review.rubric_version,
            criterion_id=criterion_id, behavior_key=review.behavior_key, behavior_version=review.behavior_version,
        )
        session.add(mapping)
    elif (mapping.behavior_key, mapping.behavior_version) != (review.behavior_key, review.behavior_version):
        mapping.behavior_key = review.behavior_key
        mapping.behavior_version = review.behavior_version
        mapping.reviewed_at = datetime.now(timezone.utc)
    await session.commit()
    return _criterion_view(criterion, mapping)


@router.delete("/{question_id}/{criterion_id}", status_code=204)
async def revoke_review(
    question_id: int,
    criterion_id: str,
    rubric_version: Annotated[str, Query(min_length=1, max_length=100)],
    ws: Workspace = Depends(get_workspace),
    session: AsyncSession = Depends(get_session),
):
    await _question(session, ws.id, question_id, for_update=True)
    mapping = await _mapping(session, ws.id, question_id, rubric_version, criterion_id)
    if mapping:
        await session.delete(mapping)
    await session.commit()
    return Response(status_code=204)
