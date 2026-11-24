"""Workspace-scoped excerpts frozen when a study session or project turn is created."""
import json
import re

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import lazyload

from app.i18n import message as localized_message
from app.models import Exam, ExamQuestion, LearningGoal, LearningTask, Question, Skill, SkillState
from app.models.project import Project
from app.services.learning_exposure import record_reference_exposure
from app.services.project_context import PROFILE_LABELS, project_profile
from app.services.project_memory import active_project_memories, memory_fact


def excerpt(kind, identifier, label, value, provenance):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {"kind": kind, "id": identifier, "label": label, "text": text[:4000], "provenance": provenance}


async def visible_question(session, workspace_id, question_id):
    return await session.scalar(select(Question).options(lazyload("*")).where(
        Question.id == question_id, or_(Question.workspace_id == workspace_id, Question.workspace_id.is_(None))))


async def topic_skills(session, topic):
    skills = (await session.execute(select(Skill.id, Skill.key, Skill.name))).all()
    return [row.id for row in skills if row.name in topic or re.search(
            r"(?<![a-z0-9])" + re.escape(row.key) + r"(?![a-z0-9])", topic, re.I)]


async def current_skill_sources(session, workspace_id, skill_ids):
    if not skill_ids:
        return []
    rows = (await session.scalars(select(SkillState).where(
        SkillState.workspace_id == workspace_id, SkillState.skill_id.in_(skill_ids))
        .order_by(SkillState.skill_id))).all()
    return [excerpt("skill_state", row.id, f"当前画像 · {row.skill.name}", {
        "知识点 ID": row.skill_id, "证据状态": row.status, "证据数": row.evidence_coverage,
        "最近评估": row.last_assessed_at.isoformat() if row.last_assessed_at else None,
        "读取的版本": row.updated_at.isoformat(),
    }, "learning_record") for row in rows]


def completed_questions(workspace_id):
    return select(ExamQuestion).join(Exam, Exam.id == ExamQuestion.exam_id).where(
        Exam.workspace_id == workspace_id, Exam.status == Exam.STATUS_COMPLETED)


def assessment_sources(item):
    sources = []
    answer = item.answer
    snapshot = item.frozen_snapshot or item.creation_snapshot or {}
    submitted = snapshot.get("submitted_answer") or {}
    text = submitted.get("text_answer") or submitted.get("transcript")
    choice = submitted.get("choice_index")
    if answer and not submitted:
        text = answer.text_answer or answer.transcript
        choice = answer.choice_index
    if type(choice) is int:
        options = snapshot.get("options") or []
        text = options[choice] if 0 <= choice < len(options) else f"选项索引 {choice}"
    if text:
        sources.append(excerpt("user_answer", item.id, "当时提交的原答", text, "user_statement"))
    if answer and (answer.feedback or answer.points is not None):
        sources.append(excerpt("assessment", item.id, "该次作答的评价", {
            "得分": answer.points, "满分": answer.max_points, "结论": answer.verdict,
            "反馈": answer.feedback, "评价版本": answer.judge_version,
        }, "assessment"))
    return sources


async def entry_context(session, ws, body):
    context = {"source_type": body.source_type, "source_id": body.source_id, "topic": body.topic or "",
               "skill_ids": [], "sources": [], "target_role": ws.target_role}
    sources = context["sources"]
    question = None
    exam_item = None
    if body.source_type == "free":
        if body.source_id is not None:
            raise HTTPException(422, localized_message("errors.a_free_form_session_has_no_source_id"))
        if body.skill_id is not None:
            skill = await session.get(Skill, body.skill_id)
            if skill is None:
                raise HTTPException(404, localized_message("errors.knowledge_point_not_found"))
            context["skill_ids"] = [skill.id]
        elif body.topic:
            context["skill_ids"] = await topic_skills(session, body.topic)
    elif body.source_type == "task":
        task = await session.scalar(select(LearningTask).where(
            LearningTask.id == body.source_id, LearningTask.workspace_id == ws.id))
        if task is None:
            raise HTTPException(404, localized_message("errors.learning_task_not_found"))
        context["topic"] = task.title
        context["skill_ids"] = [task.skill_id] if task.skill_id else []
        sources.append(excerpt("task", task.id, task.title, {
            "知识点": task.skill.name if task.skill else None, "学习原因": task.why,
            "目标": task.source_criterion, "验收标准": task.acceptance}, "learning_record"))
        goals = (await session.scalars(select(LearningGoal).where(
            LearningGoal.workspace_id == ws.id, LearningGoal.source_task_id == task.id
        ).order_by(LearningGoal.id).limit(3))).all()
        for goal in goals:
            sources.append(excerpt("goal", goal.id, "学习目标", {
                "目标": goal.target_behavior, "成功标准": goal.success_criteria,
                "当前证据状态": goal.evidence_status}, "learning_record"))
            context["skill_ids"] = list(dict.fromkeys([*context["skill_ids"], *goal.skill_ids]))
        if task.source_exam_question_id:
            exam_item = await session.scalar(completed_questions(ws.id).where(
                ExamQuestion.id == task.source_exam_question_id))
    elif body.source_type == "exam_question":
        exam_item = await session.scalar(completed_questions(ws.id).where(ExamQuestion.id == body.source_id))
        if exam_item is None:
            raise HTTPException(404, localized_message("errors.graded_exam_question_not_found"))
    elif body.source_type == "question":
        question = await visible_question(session, ws.id, body.source_id)
        if question is None:
            raise HTTPException(404, localized_message("errors.question_not_found_362"))
        exam_item = await session.scalar(completed_questions(ws.id).where(
            ExamQuestion.question_id == question.id).order_by(Exam.completed_at.desc(), ExamQuestion.id.desc()).limit(1))
    if exam_item and question is None:
        question = await visible_question(session, ws.id, exam_item.question_id)
        if question is None:
            raise HTTPException(404, localized_message("errors.question_not_found_362"))
    if question:
        frozen = (exam_item.frozen_snapshot or exam_item.creation_snapshot or {}) if exam_item else {}
        title = frozen.get("title") or question.title
        context["question_id"] = question.id
        skill_id = frozen.get("skill_id") or question.skill_id
        if not context["skill_ids"] and skill_id:
            context["skill_ids"] = [skill_id]
        if not context["topic"]:
            context["topic"] = title
        sources.append(excerpt("question", question.id, "原题", {
            "题目": title, "选项": frozen.get("options", question.options)}, "question_material"))
        if exam_item:
            sources.extend(assessment_sources(exam_item))
            context["exam_question_id"] = exam_item.id
            if any(item["kind"] == "assessment" for item in sources):
                await record_reference_exposure(session, ws, question, source="study_chat_feedback")
    return context


async def project_sources(session, workspace_id, skill_ids, project_id):
    query = select(Project).options(lazyload("*")).where(
        Project.workspace_id == workspace_id, Project.archived_at.is_(None))
    if project_id is not None:
        query = query.where(Project.id == project_id)
    projects = (await session.scalars(query.order_by(Project.id.desc()).limit(2))).all()
    if project_id is not None and not projects:
        raise HTTPException(404, localized_message("errors.project_not_found"))
    if len(projects) != 1:
        return []
    project = projects[0]
    sources = []
    profile = project_profile(project)
    for key in ("role", "responsibilities", "stack", "decisions"):
        if profile[key]:
            sources.append(excerpt(f"project_{key}", project.id, f"{project.name} · {PROFILE_LABELS[key]}",
                                   profile[key], "user_statement"))
    for memory in reversed((await active_project_memories(session, workspace_id, project.id))[-2:]):
        fact = memory_fact(memory)
        sources.append(excerpt("project_memory", memory.id, project.name + " · 已确认的报告摘录", {
            "记录": fact["text"][:1300], "版本": fact["source_revision"],
            "材料": [{k: str(item.get(k) or "")[:650] for k in ("path", "start_line", "end_line", "excerpt")}
                   for item in fact["excerpts"][:2]],
        }, "user_statement" if fact["user_corrected"] else "project_report"))
    return sources
