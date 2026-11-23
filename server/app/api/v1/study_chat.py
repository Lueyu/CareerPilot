"""Workspace-owned tutoring sessions. A committed turn survives a lost browser connection."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.db import get_session
from app.models import Workspace
from app.models.project import Project
from app.models.study_chat import StudyMessage, StudySession
from app.services.answer_guides import example_for, for_question as teaching_guide_for
from app.services.learning_exposure import record_reference_exposure
from app.services.study_chat import expire_generations, generate_reply, lease_deadline, teaching_action
from app.services.study_chat_context import current_skill_sources, entry_context, excerpt, project_sources, topic_skills, visible_question
from app.services.study_interview_questions import InterviewQuestionResult, generate_question, saved_result


router = APIRouter(prefix="/study/sessions", tags=["学习聊天"])
Db = Annotated[AsyncSession, Depends(get_session)]
Owner = Annotated[Workspace, Depends(get_workspace)]
PositiveId = Annotated[int, Field(gt=0)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Mode = Literal["auto", "flash", "strong"]


class StartSession(Input):
    project_id: PositiveId | None = None
    mode: Mode = "auto"
    source_type: Literal["free", "task", "question", "exam_question"] = "free"
    source_id: PositiveId | None = None
    topic: str = Field(default="", max_length=8000)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    skill_id: PositiveId | None = None


class EditSession(Input):
    project_id: PositiveId | None = None
    mode: Mode | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["active", "ended"] | None = None


class InterviewQuestionRequest(Input):
    pass


class NewMessage(Input):
    mode: Mode | None = None
    parent_message_id: PositiveId | None = None
    revision_of: PositiveId | None = None
    request_id: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=8000)
    action: Literal["explain", "simplify", "example", "project", "mechanism", "why", "diagram", "demo", "interview", "critique", "jd", "improve"] = "explain"
    project_id: PositiveId | None = None


def session_item(row):
    return dict(id=row.id, title=row.title, status=row.status, context=row.context, mode=row.context.get("mode", "auto"),
                project_id=row.context.get("project_id"),
                created_at=row.created_at, updated_at=row.updated_at)


def message_item(row):
    item = {name: getattr(row, name) for name in (
        "id", "request_id", "role", "content", "status", "action", "sources",
        "created_at", "attempt", "error", "model", "project_id")}
    item.update({k: v for k, v in (row.coach or {}).items() if k in (
        "blocks", "trace", "revision_of", "parent_message_id", "mode")})
    return item


async def owned(session, workspace_id, session_id, *, lock=False):
    query = select(StudySession).where(StudySession.id == session_id, StudySession.workspace_id == workspace_id)
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None or row.hidden_at:
        raise HTTPException(404, "学习会话不存在")
    return row


async def workspace_lock(session, ws):
    await session.execute(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())


async def check_project(session, workspace_id, project_id):
    if project_id is not None and not await session.scalar(select(Project.id).where(
        Project.id == project_id, Project.workspace_id == workspace_id, Project.archived_at.is_(None)
    )):
        raise HTTPException(404, "项目不存在")


@router.post("", status_code=201)
async def create(body: StartSession, session: Db, ws: Owner):
    if body.source_type != "free" and body.source_id is None:
        raise HTTPException(422, "请指定学习来源")
    await workspace_lock(session, ws)
    context = await entry_context(session, ws, body)
    await check_project(session, ws.id, body.project_id)
    context.update(mode=body.mode, project_id=body.project_id)
    row = StudySession(workspace_id=ws.id, title=body.title or context["topic"][:80] or "新的学习会话",
                       context=context)
    session.add(row)
    await session.commit()
    return {**session_item(row), "messages": []}


@router.get("")
async def history(session: Db, ws: Owner, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), q: str = ""):
    query = select(StudySession).where(StudySession.workspace_id == ws.id, StudySession.hidden_at.is_(None))
    if q.strip():
        query = query.where(StudySession.title.icontains(q.strip()[:160], autoescape=True))
    rows = (await session.scalars(query
            .order_by(StudySession.updated_at.desc(), StudySession.id.desc()).offset(offset).limit(limit))).all()
    return {"items": [{key: value for key, value in session_item(row).items() if key != "context"} for row in rows]}


@router.get("/{session_id}")
async def detail(session_id: int, session: Db, ws: Owner):
    row = await owned(session, ws.id, session_id)
    expired = await expire_generations(session, session_id)
    if expired.rowcount:
        await session.commit()
    messages = (await session.scalars(select(StudyMessage).where(StudyMessage.session_id == row.id)
                            .order_by(StudyMessage.id).execution_options(populate_existing=True))).all()
    return {**session_item(row), "messages": [message_item(m) for m in messages]}


@router.patch("/{session_id}")
async def edit(session_id: int, body: EditSession, session: Db, ws: Owner):
    row = await owned(session, ws.id, session_id, lock=True)
    if "project_id" in body.model_fields_set:
        await check_project(session, ws.id, body.project_id)
        row.context = {**row.context, "project_id": body.project_id}
    if body.mode:
        row.context = {**row.context, "mode": body.mode}
    if body.title is not None:
        row.title = body.title
    if body.status is not None:
        row.status = body.status
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return session_item(row)


@router.get("/{session_id}/interview-question")
async def get_interview_question(session_id: int, session: Db, ws: Owner) -> InterviewQuestionResult | None:
    await owned(session, ws.id, session_id)
    return await saved_result(session, ws.id, session_id)


@router.post("/{session_id}/interview-question")
async def create_interview_question(
    session_id: int, session: Db, ws: Owner, body: InterviewQuestionRequest | None = None,
) -> InterviewQuestionResult:
    await owned(session, ws.id, session_id)
    return await generate_question(session, ws.id, session_id)


@router.delete("/{session_id}")
async def archive(session_id: int, session: Db, ws: Owner):
    row = await owned(session, ws.id, session_id, lock=True)
    row.hidden_at = datetime.now(timezone.utc)
    row.status = "ended"
    await session.execute(update(StudyMessage).where(StudyMessage.session_id == row.id,
        StudyMessage.status == "generating").values(status="interrupted", expires_at=None, error="会话已移除"))
    await session.commit()
    return {"id": row.id}


def ticket(user_id, reply):
    return {"user_message_id": user_id, "assistant_message_id": reply.id, "status": reply.status}


async def mark_help(session, ws, row, question_text):
    from app.services.study_practice import mark_chat_help
    await mark_chat_help(session, ws.id, row.id, question_text=question_text)
    if row.context.get("question_id"):
        question = await visible_question(session, ws.id, row.context["question_id"])
        if question:
            await record_reference_exposure(session, ws, question, source="study_chat_explanation")
            guide = teaching_guide_for(question)
            example = example_for(question)
            if guide:
                return excerpt("teaching_guide", question.id, "本题导读与教学示例", {
                    "白话": guide.plain, "流程": guide.flow, "例子": guide.example,
                    "比方": example or guide.analogy,
                    "提议的项目练习": guide.project.model_dump(),
                }, "teaching_material")
            if example:
                return excerpt("teaching_guide", question.id, "本题的入门例子", example, "teaching_material")


@router.post("/{session_id}/messages", status_code=202)
async def send(session_id: int, body: NewMessage, tasks: BackgroundTasks, session: Db, ws: Owner):
    await workspace_lock(session, ws)
    row = await owned(session, ws.id, session_id, lock=True)
    await expire_generations(session, row.id)
    messages = select(StudyMessage).where(StudyMessage.session_id == row.id)
    existing = (await session.scalars(messages.where(StudyMessage.request_id == body.request_id)
                                      .order_by(StudyMessage.id))).all()
    if existing:
        user, reply = existing
        signature = (user.coach or {}).get("request")
        same = signature == body.model_dump(exclude_none=True) if signature else (
            (user.content, user.action, user.project_id) == (body.content, body.action, body.project_id))
        if not same:
            raise HTTPException(409, "该请求编号已用于另一条消息")
        await session.commit()
        return ticket(user.id, reply)
    if row.status != "active":
        raise HTTPException(409, "该会话已结束，请先继续学习")
    if await session.scalar(messages.where(StudyMessage.status == "generating").limit(1)):
        raise HTTPException(409, "上一条回复正在生成，可以中断或等待完成")

    skills = row.context["skill_ids"]
    if not row.context["topic"] and not skills:
        skills = await topic_skills(session, body.content)
    sources = [*row.context["sources"], *await current_skill_sources(session, ws.id, skills)]
    project_id = body.project_id if body.project_id is not None else row.context.get("project_id")
    if not project_id and teaching_action(body.action, body.content) in ("project", "improve"):
        available = (await session.execute(select(Project.id, Project.name).where(
            Project.workspace_id == ws.id, Project.archived_at.is_(None)))).all()
        named = [p.id for p in available if p.name.casefold() in body.content.casefold()]
        project_id = named[0] if len(named) == 1 else available[0].id if len(available) == 1 else None
        if project_id:
            row.context = {**row.context, "project_id": project_id}
    await check_project(session, ws.id, project_id)
    parent = await session.scalar(messages.where(StudyMessage.status == "completed",
        StudyMessage.project_id.is_not_distinct_from(project_id)).order_by(StudyMessage.id.desc()).limit(1))
    for identifier in (body.parent_message_id, body.revision_of):
        if identifier:
            parent = await session.scalar(messages.where(StudyMessage.id == identifier))
            if parent is None or parent.project_id != project_id or parent.status != "completed":
                raise HTTPException(404, "消息不属于当前会话与项目")
    if body.revision_of:
        original_user = await session.scalar(messages.where(StudyMessage.request_id == parent.request_id,
            StudyMessage.role == "user"))
        if not original_user:
            raise HTTPException(409, "缺少此版本的原始提问")
        anchor = (original_user.coach or {}).get("parent_message_id")
        if body.parent_message_id and body.parent_message_id not in (anchor, original_user.id):
            raise HTTPException(409, "所选父消息与修订版本不一致")
        if "parent_message_id" not in (original_user.coach or {}):
            anchor = await session.scalar(select(StudyMessage.id).where(StudyMessage.session_id == row.id,
                StudyMessage.id < original_user.id, StudyMessage.project_id.is_not_distinct_from(project_id),
                StudyMessage.status == "completed").order_by(StudyMessage.id.desc()).limit(1))
    else:
        anchor = parent.id if parent else None
    mode = body.mode or row.context.get("mode", "auto")
    guide_source = await mark_help(session, ws, row, body.content)
    if guide_source:
        sources.append(guide_source)
    if not row.context["topic"]:
        row.context = {**row.context, "topic": body.content, "skill_ids": skills}
        if row.title == "新的学习会话":
            row.title = body.content[:80]
    row.updated_at = datetime.now(timezone.utc)
    user = StudyMessage(session_id=row.id, role="user", action=body.action, content=body.content,
                        project_id=project_id, request_id=body.request_id, status="completed",
                        coach={"mode": mode, "revision_of": body.revision_of, "parent_message_id": anchor,
                               "request": body.model_dump(exclude_none=True)})
    session.add(user)
    await session.flush()
    reply = StudyMessage(session_id=row.id, role="assistant", action=body.action, content="", sources=sources,
                         project_id=project_id, request_id=body.request_id, status="generating", expires_at=lease_deadline(),
                         coach={"mode": mode, "parent_message_id": user.id, "revision_of": body.revision_of,
                                "trace": {"stage": "queued", "calls": []}})
    session.add(reply)
    await session.commit()
    tasks.add_task(generate_reply, reply.id, reply.attempt)
    return ticket(user.id, reply)


async def owned_reply(session, row, message_id):
    reply = await session.scalar(select(StudyMessage).where(StudyMessage.id == message_id,
        StudyMessage.session_id == row.id, StudyMessage.role == "assistant").with_for_update())
    if reply is None:
        raise HTTPException(404, "回复不存在")
    user_id = await session.scalar(select(StudyMessage.id).where(StudyMessage.session_id == row.id,
        StudyMessage.role == "user", StudyMessage.request_id == reply.request_id))
    return user_id, reply


@router.post("/{session_id}/messages/{message_id}/interrupt")
async def interrupt(session_id: int, message_id: int, session: Db, ws: Owner):
    row = await owned(session, ws.id, session_id, lock=True)
    user_id, reply = await owned_reply(session, row, message_id)
    if reply.status == "generating":
        reply.coach = {**reply.coach, "trace": {**reply.coach.get("trace", {}), "stage": "interrupted", "error_kind": "cancelled"}}
        reply.status, reply.expires_at, reply.error = "interrupted", None, "已暂停生成，可重试"
    await session.commit()
    return ticket(user_id, reply)


@router.post("/{session_id}/messages/{message_id}/retry", status_code=202)
async def retry(session_id: int, message_id: int, tasks: BackgroundTasks, session: Db, ws: Owner):
    await workspace_lock(session, ws)
    row = await owned(session, ws.id, session_id, lock=True)
    await expire_generations(session, row.id)
    user_id, reply = await owned_reply(session, row, message_id)
    if reply.status in ("completed", "generating"):
        await session.commit()
        return ticket(user_id, reply)
    if row.status != "active":
        raise HTTPException(409, "该会话已结束，请先继续学习")
    if await session.scalar(select(StudyMessage.id).where(
            StudyMessage.session_id == row.id, StudyMessage.id > reply.id).limit(1)):
        raise HTTPException(409, "已有后续对话，请另发一条消息")
    # A practice attempt could have started since the interrupted generation.
    question_text = await session.scalar(select(StudyMessage.content).where(StudyMessage.id == user_id))
    await mark_help(session, ws, row, question_text)
    previous_trace = dict(reply.coach.get("trace", {}))
    calls = list(previous_trace.get("calls", []))
    if calls and reply.content:
        calls[-1] = {**calls[-1], "saved_output": reply.content[:16000], "error": calls[-1].get("error") or reply.error}
    reply.coach = {**reply.coach, "trace": {**previous_trace, "calls": calls, "stage": "queued", "error_kind": None}}
    reply.attempt += 1
    reply.status, reply.content, reply.error, reply.model = "generating", "", None, None
    reply.expires_at = lease_deadline()
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    tasks.add_task(generate_reply, reply.id, reply.attempt)
    return ticket(user_id, reply)
