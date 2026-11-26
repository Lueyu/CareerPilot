"""Workspace-scoped excerpts frozen when a study session or project turn is created."""
import json
import re

from fastapi import HTTPException
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import lazyload

from app.i18n import message as localized_message
from app.models import Exam, ExamQuestion, ImportSession, LearningGoal, LearningTask, Question, Skill, SkillState, StudyNote, StudyNoteVersion
from app.models.project import Project
from app.services.bank_practice import bank_list_question_clause
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


# 用户明确指向资料库（题库/面经/学习资料）时，聊天才能读取库内材料；避免普通讲解回合被无关条目污染。
LIBRARY_REFERENCE = re.compile(
    r"资料库|题库|面经|素材库|材料库|学习资料|复习资料|错题本|interview sets?|question bank|my library|the library",
    re.I)
_LATIN_STOP = frozenset({
    "the", "an", "and", "or", "of", "for", "in", "on", "with", "what", "which", "who", "how", "why",
    "when", "where", "is", "are", "any", "all", "my", "me", "to", "have", "has", "related", "about",
    "interview", "question", "questions", "bank", "library", "set", "sets", "notes", "show", "list",
    "see", "look", "check", "please", "want", "need", "find", "there",
})
_CJK_STOP = (
    "面试题", "面经", "题库", "资料库", "素材库", "材料库", "学习资料", "复习资料", "错题本", "题目",
    "面试", "相关", "哪些", "什么", "有没有", "所有", "看看", "看下", "我的", "里面", "库里",
    "知识点", "问题", "帮我", "请教", "一下", "都有", "里的", "相关的",
)


def library_terms(question: str, skill_rows, skill_ids) -> list[str]:
    """检索词：命中的技能 key，拉丁词元，以及剔除指引词后的中文残片。"""
    terms = [row.key for row in skill_rows if row.id in skill_ids]
    latin = [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9+#._-]{1,24}", question)
             if len(token) >= 2 and token.lower() not in _LATIN_STOP]
    cleaned = LIBRARY_REFERENCE.sub(" ", question)
    for word in _CJK_STOP:
        cleaned = cleaned.replace(word, " ")
    cjk = re.findall(r"[\u4e00-\u9fff]{2,6}", cleaned)
    return list(dict.fromkeys([*terms, *latin, *cjk]))[:8]


def _question_match_filters(terms, skill_ids):
    match = []
    if terms:
        match.append(or_(*[filter for term in terms for filter in (
            Question.title.icontains(term, autoescape=True),
            Question.topic.icontains(term, autoescape=True),
            cast(Question.keywords, Text).icontains(term, autoescape=True),
            cast(Question.tags, Text).icontains(term, autoescape=True),
        )]))
    if skill_ids:
        match.append(Question.skill_id.in_(skill_ids))
    return or_(*match) if match else None


def _set_titles(entry) -> list[str]:
    return [str(question.get("title") or "") for question in entry.get("questions") or []]


def _set_hit(entry, terms) -> bool:
    if not terms:
        return True
    haystack = " ".join([str(entry.get("title") or ""), str(entry.get("project_name") or ""), *_set_titles(entry)]).casefold()
    return any(term.casefold() in haystack for term in terms)


async def library_sources(session, workspace_id, question):
    """把资料库中与问题相关的材料作为聊天来源返回；未命中时也返回空结果摘要。"""
    if not LIBRARY_REFERENCE.search(question):
        return []
    skill_rows = (await session.execute(select(Skill.id, Skill.key, Skill.name))).all()
    skill_ids = {row.id for row in skill_rows if row.name in question or re.search(
        r"(?<![a-z0-9])" + re.escape(row.key) + r"(?![a-z0-9])", question, re.I)}
    terms = library_terms(question, skill_rows, skill_ids)
    sources: list[dict] = []

    clause = bank_list_question_clause(workspace_id)
    bank_query = select(Question).where(clause)
    match = _question_match_filters(terms, skill_ids)
    if match is not None:
        bank_query = bank_query.where(match)
    bank_total = (await session.execute(
        select(func.count()).select_from(bank_query.subquery()))).scalar() or 0
    if terms:
        rows = (await session.scalars(bank_query.options(lazyload("*")).order_by(Question.id).limit(12))).all()
    else:
        rows = (await session.scalars(bank_query.options(lazyload("*")).order_by(Question.id.desc()).limit(6))).all()
    for row in rows:
        sources.append(excerpt("library_question", row.id, "资料库题目", {
            "题目": row.title, "题型": row.qtype, "难度": row.difficulty, "公司": row.company,
            "主题": row.topic, "标签": (row.tags or [])[:6], "来源": row.source_type,
        }, "question_material"))

    set_imports = (await session.scalars(select(ImportSession).where(
        ImportSession.workspace_id == workspace_id,
        ImportSession.meta["interview_sets"].as_string().is_not(None),
    ).order_by(ImportSession.id.desc()))).all()
    entries = [entry for imported in set_imports
               for entry in reversed((imported.meta or {}).get("interview_sets", []))]
    set_hits = [entry for entry in entries if _set_hit(entry, terms)]
    for entry in set_hits[:6]:
        titles = _set_titles(entry)
        matched = [title for title in titles
                   if not terms or any(term.casefold() in title.casefold() for term in terms)]
        sources.append(excerpt("library_interview_set", entry.get("id") or entry.get("import_id"),
                               f"面经套题 · {entry.get('title') or ''}", {
                                   "套题": entry.get("title"), "项目": entry.get("project_name"),
                                   "题目数": len(titles), "命中题目": (matched or titles)[:8],
                               }, "imported_interview_notes"))

    note_rows = (await session.execute(
        select(StudyNote, StudyNoteVersion).join(
            StudyNoteVersion, StudyNoteVersion.id == StudyNote.current_version_id
        ).where(StudyNote.workspace_id == workspace_id).order_by(StudyNote.updated_at.desc())
    )).all()
    note_hits = [(note, version) for note, version in note_rows
                 if not terms or any(term.casefold() in f"{note.title}\n{version.body}".casefold()
                                     for term in terms)]
    for note, version in note_hits[:3]:
        sources.append(excerpt("library_note", note.id, f"学习资料 · {note.title}", {
            "笔记": note.title, "摘录": version.body[:240], "更新于": note.updated_at.isoformat(),
        }, "learning_record"))

    summary = {"检索词": terms, "题库命中": bank_total, "面经套题命中": len(set_hits),
               "学习资料命中": len(note_hits)}
    if not rows and not set_hits and not note_hits:
        summary["提示"] = "资料库中未找到与检索词匹配的条目。可以导入面经，或在题库添加相关题目后再问。"
    sources.insert(0, excerpt("library_summary", 0, "资料库检索结果", summary, "scope"))
    return sources


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
