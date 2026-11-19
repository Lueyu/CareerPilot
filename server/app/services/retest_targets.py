"""Bind retests to reviewed behaviors, then grade only the bindings on the issued paper."""

from dataclasses import asdict, dataclass

from fastapi import HTTPException
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Exam, ExamQuestion, LearningGoal, LearningTask, Question, RetestCriterionMapping, Workspace
from app.services.assessment_versions import question_snapshot

@dataclass(frozen=True)
class RetestTarget:
    goal_id: int | None
    source_exam_question_id: int | None
    source_criterion_id: str | None
    source_criterion: str
    source_rubric_version: str | None
    source_evidence_id: int | None
    source_answer_revision_id: int | None
    primary: bool
    source_mapping_id: int | None = None
    behavior_key: str | None = None
    behavior_version: int | None = None
    source_reviewed_at: str | None = None


@dataclass
class RetestPlan:
    targets: list[RetestTarget]
    mappings: dict[int, list[RetestCriterionMapping]]

    @property
    def primary_goal_id(self) -> int | None:
        return next(target.goal_id for target in self.targets if target.primary)

    def matching(self, snapshot: dict) -> list[dict]:
        rubric = snapshot.get("rubric") or {}
        if rubric.get("schema_version") != 2:
            return []
        version = rubric["rubric_version"]
        ids = {c["criterion_id"] for c in rubric["criteria"]}
        mappings = [
            row for row in self.mappings.get(snapshot["question_id"], [])
            if row.rubric_version == version and row.criterion_id in ids
        ]
        bindings = []
        for target in self.targets:
            if target.source_mapping_id is None:
                continue
            matches = [row for row in mappings
                       if (row.behavior_key, row.behavior_version) == (target.behavior_key, target.behavior_version)]
            if len(matches) != 1:
                continue
            match = matches[0]
            bindings.append({**asdict(target), "criterion_id": match.criterion_id,
                             "rubric_version": version, "target_mapping_id": match.id,
                             "target_reviewed_at": match.reviewed_at.isoformat()})
        return bindings

    def covered_goals(self, question: Question) -> set[int | None]:
        if question.id not in self.mappings:
            return set()
        try:
            return {binding["goal_id"] for binding in self.matching(question_snapshot(question))}
        except HTTPException:
            return set()  # A stale or no longer issuable rubric earns no priority.

    def freeze(self, papers: list[dict]) -> bool:
        if not papers:
            return False
        papers[0]["retest_targets"] = [asdict(target) for target in self.targets]
        assigned = set()
        primary_covered = False
        for paper in papers:
            bindings = []
            for binding in self.matching(paper):
                if binding["goal_id"] not in assigned:
                    assigned.add(binding["goal_id"])
                    bindings.append(binding)
                    primary_covered |= binding["primary"]
            paper["retest_bindings"] = bindings
        return primary_covered


def task_origin(task: LearningTask) -> dict:
    observation = (task.content or {}).get("observation") or {}
    return {
        "exam_id": task.source_exam_id, "exam_question_id": task.source_exam_question_id,
        "criterion_id": observation.get("criterion_id"), "criterion_title": task.source_criterion,
        "rubric_version": observation.get("rubric_version"), "evidence_id": task.source_evidence_id,
        "question_id": observation.get("question_id"), "answer_revision_id": observation.get("answer_revision_id"),
    }


async def load_retest_plan(session: AsyncSession, ws: Workspace, task: LearningTask) -> RetestPlan:
    goals = list((await session.scalars(select(LearningGoal).where(
        LearningGoal.workspace_id == task.workspace_id, LearningGoal.source_task_id == task.retest_of,
    ).order_by(LearningGoal.id))).all()) if task.retest_of else []
    origin = task_origin(task)
    origins = [(goal, goal.origin_refs or {}) for goal in goals]
    primary_key = (origin["exam_question_id"], origin["criterion_id"])
    primary_index = next((i for i, (_, refs) in enumerate(origins)
                          if (refs.get("exam_question_id"), refs.get("criterion_id")) == primary_key), None)
    if primary_index is None:
        primary_index = len(origins)
        origins.append((None, origin))

    source_ids = {refs["exam_question_id"] for _, refs in origins if refs.get("exam_question_id")}
    source_rows = (await session.scalars(select(ExamQuestion).join(Exam).where(
        ExamQuestion.id.in_(source_ids), Exam.workspace_id == task.workspace_id, Exam.status == Exam.STATUS_COMPLETED,
    ))).all() if source_ids else []
    source_by_id = {eq.id: eq for eq in source_rows}

    source_refs = {}
    for index, (goal, refs) in enumerate(origins):
        eq = source_by_id.get(refs.get("exam_question_id"))
        if eq is None or eq.exam_id != refs.get("exam_id"):
            continue
        if goal and (goal.status != "ACTIVE" or goal.evidence_status in {"DISPUTED", "STALE", "WITHDRAWN", "NEEDS_REVIEW"}):
            continue
        frozen = eq.frozen_snapshot or {}
        rubric = frozen.get("rubric") or {}
        version = rubric.get("rubric_version")
        criterion_id = refs.get("criterion_id")
        if (rubric.get("schema_version") == 2 and version and version == refs.get("rubric_version")
                and (refs.get("question_id") is None or refs["question_id"] == eq.question_id)
                and (refs.get("answer_revision_id") is None or refs["answer_revision_id"] == frozen.get("answer_revision_id"))
                and any(c["criterion_id"] == criterion_id for c in rubric.get("criteria", []))):
            source_refs[index] = (eq.question_id, version, criterion_id)

    reviewed = RetestCriterionMapping
    source_mappings = (await session.scalars(select(reviewed).where(
        reviewed.workspace_id == task.workspace_id,
        tuple_(reviewed.question_id, reviewed.rubric_version, reviewed.criterion_id).in_(set(source_refs.values())),
    ))).all() if source_refs else []
    by_criterion = {(row.question_id, row.rubric_version, row.criterion_id): row for row in source_mappings}

    targets = []
    for index, (goal, refs) in enumerate(origins):
        row = by_criterion.get(source_refs.get(index))
        if row is not None:
            from app.services.learning_feedback import assert_assessed_origin, assert_goal_sources
            try:
                if goal is not None:
                    await assert_goal_sources(session, ws, goal)
                else:
                    await assert_assessed_origin(session, ws.id, {**refs, "question_id": row.question_id})
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                row = None
        title = refs.get("criterion_title") or ((goal.success_criteria or {}).get("title") if goal else None)
        targets.append(RetestTarget(
            goal_id=goal.id if goal else None,
            source_exam_question_id=refs.get("exam_question_id"),
            source_criterion_id=refs.get("criterion_id"),
            source_criterion=title or task.title,
            source_rubric_version=refs.get("rubric_version"),
            source_evidence_id=refs.get("evidence_id"),
            source_answer_revision_id=refs.get("answer_revision_id"),
            primary=index == primary_index,
            source_mapping_id=row.id if row else None,
            behavior_key=row.behavior_key if row else None,
            behavior_version=row.behavior_version if row else None,
            source_reviewed_at=row.reviewed_at.isoformat() if row else None,
        ))
    behavior_refs = {(target.behavior_key, target.behavior_version)
                     for target in targets if target.source_mapping_id is not None}
    candidate_rows = (await session.scalars(select(reviewed).where(
        reviewed.workspace_id == task.workspace_id,
        tuple_(reviewed.behavior_key, reviewed.behavior_version).in_(behavior_refs),
    ))).all() if behavior_refs else []
    candidates = {}
    for row in candidate_rows:
        candidates.setdefault(row.question_id, []).append(row)
    return RetestPlan(targets, candidates)


def goal_outcomes(exam: Exam, task: LearningTask) -> list[dict]:
    issued = [getattr(eq, "creation_snapshot", None) or {} for eq in exam.questions]
    targets = next((paper["retest_targets"] for paper in issued if "retest_targets" in paper), [])
    if not targets:
        return [{"goal_id": None, "source_criterion": task.source_criterion or task.title,
                 "primary": True, "status": "UNMAPPED", "covered": False,
                 "score": None, "independent": None, "binding": None}]
    bound = {
        binding["goal_id"]: (eq, binding)
        for eq, paper in zip(exam.questions, issued)
        for binding in paper.get("retest_bindings", [])
    }
    return [_evaluate_target(target, bound.get(target["goal_id"])) for target in targets]


def _evaluate_target(target: dict, match: tuple[ExamQuestion, dict] | None) -> dict:
    outcome = {**target, "status": "UNMAPPED", "covered": False,
               "score": None, "independent": None, "binding": None}
    if match is None:
        return outcome
    eq, binding = match
    frozen = eq.frozen_snapshot or {}
    rubric = frozen.get("rubric") or {}
    answer = eq.answer.feedback or {} if eq.answer else {}
    independence = (frozen.get("independence") or {}).get("eligible_for_independent_verification")
    outcome.update(status="UNVERIFIED", covered=True, independent=independence,
                   binding={**binding, "exam_question_id": eq.id,
                            "answer_revision_id": frozen.get("answer_revision_id")})
    rows = [row for row in answer.get("criterion_scores", []) if row.get("criterion_id") == binding["criterion_id"]]
    if (rubric.get("schema_version") != 2 or rubric.get("rubric_version") != binding["rubric_version"]
            or not any(c["criterion_id"] == binding["criterion_id"] for c in rubric["criteria"])
            or answer.get("validation_status") != "VALID" or answer.get("rubric_version") != binding["rubric_version"]
            or not frozen.get("answer_revision_id") or answer.get("answer_revision_id") != frozen["answer_revision_id"]
            or len(rows) != 1 or type(rows[0].get("score")) is not int or not 0 <= rows[0]["score"] <= 4):
        return outcome
    score = rows[0]["score"]
    outcome["score"] = score
    if independence is True:
        outcome["status"] = "PASSED" if score >= 3 else "FAILED"
    elif independence is False:
        outcome["status"] = "ASSISTED"
    return outcome
