"""Teaching uses the assessed frozen source, not current same-domain questions."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import deps
from app.api.v1 import learning
from app.db import get_session
from app.i18n import message
from app.main import app
from app.models import Exam, ExamQuestion, LearningTask, Question, Skill, Workspace
from app.services import teaching


class CapturingAdapter:
    active = True

    def __init__(self):
        self.prompts = []

    async def chat(self, system, user, **kwargs):
        self.prompts.append(user)
        return {"content": json.dumps({"analogy": "a", "example": "b", "principles": ["c"],
                                       "steps": ["d"], "verify": "e", "text": "source explanation"})}


async def source_task(session, workspace):
    suffix = uuid4().hex[:12]
    skill = Skill(key=f"teaching-{suffix}", name="Source skill")
    q = Question(short_code=f"source-{suffix}", workspace_id=workspace.id, skill=skill,
                 title="MUTABLE_LIVE_TITLE", qtype="open", topic=skill.key,
                 answer_html="MUTABLE_LIVE_ANSWER", rubric={"points": [{"title": "MUTABLE_RUBRIC"}]})
    eq = ExamQuestion(question=q, order_no=1, frozen_snapshot={
        "title": "FROZEN_SOURCE_TITLE", "answer_html": "<p>FROZEN_SOURCE_ANSWER</p>",
        "rubric": {"points": [{"title": "FROZEN_CRITERION", "detail": "FROZEN_STANDARD"}]},
    })
    exam = Exam(workspace_id=workspace.id, title="Source exam", kind="diagnosis",
                status="COMPLETED", questions=[eq])
    session.add(exam)
    await session.flush()
    session.add(Question(short_code=f"unrelated-{suffix}", workspace_id=workspace.id,
                         skill_id=skill.id, title="UNRELATED_SAME_DOMAIN", qtype="open",
                         topic=skill.key, difficulty=1, answer_html="UNRELATED_GRAPH_MEMORY_SPATIAL"))
    task = LearningTask(workspace_id=workspace.id, skill_id=skill.id, title="补强原评分项",
                        kind="UNDERSTAND", source_exam_id=exam.id, source_exam_question_id=eq.id,
                        source_criterion="FROZEN_CRITERION", acceptance="学习后新题验证",
                        content={"observation": {"answer_excerpt": "ORIGINAL_USER_ANSWER",
                                 "criterion": "FROZEN_CRITERION", "criterion_detail": "FROZEN_STANDARD"}})
    session.add(task)
    await session.flush()
    return task, skill


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["build", "help"])
@pytest.mark.parametrize("old_cache", [False, True])
async def test_teaching_and_help_use_only_frozen_source(db_session, test_workspace, operation, old_cache):
    task, skill = await source_task(db_session, test_workspace)
    if old_cache:
        task.content = {**task.content, "analogy": "OLD_DOMAIN_TEACHING", "source": "llm",
                        "principles": ["OLD_DOMAIN_PRINCIPLE"],
                        "help_cache": {"simpler": {"text": "OLD_DOMAIN_HELP", "source": "llm"}}}
    adapter = CapturingAdapter()
    if operation == "build":
        await teaching.build_teaching_content(db_session, adapter, task=task,
                                             skill_name=skill.name, skill_key=skill.key)
    else:
        await teaching.help_answer(db_session, adapter, task=task, skill_name=skill.name,
                                   skill_key=skill.key, mode="simpler")
    assert adapter.prompts, "Legacy source-task caches must regenerate once"
    prompt = adapter.prompts[0]
    assert all(value in prompt for value in ("FROZEN_SOURCE_TITLE", "FROZEN_SOURCE_ANSWER",
                                             "FROZEN_CRITERION", "FROZEN_STANDARD", "ORIGINAL_USER_ANSWER"))
    assert not any(value in prompt for value in ("MUTABLE_LIVE", "MUTABLE_RUBRIC", "UNRELATED_", "OLD_DOMAIN"))
    if operation == "build":
        assert task.content["source_context_version"] == 2
        await teaching.build_teaching_content(db_session, adapter, task=task,
                                             skill_name=skill.name, skill_key=skill.key)
    else:
        if old_cache:
            assert task.content["analogy"] == "OLD_DOMAIN_TEACHING"
            assert task.content["help_cache"]["simpler"]["text"] == "OLD_DOMAIN_HELP"
        await teaching.help_answer(db_session, adapter, task=task, skill_name=skill.name,
                                   skill_key=skill.key, mode="simpler")
    assert len(adapter.prompts) == 1, "The current source context remains cached"


@pytest.mark.asyncio
async def test_source_material_must_belong_to_task_workspace(db_session, test_workspace):
    task, skill = await source_task(db_session, test_workspace)
    foreign_workspace = Workspace(slug=f"foreign-{uuid4().hex}", name="Foreign workspace")
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_task = SimpleNamespace(**{name: getattr(task, name) for name in
        ("id", "source_exam_question_id", "skill_id", "title", "kind", "acceptance", "why")},
        workspace_id=foreign_workspace.id, content={})
    adapter = CapturingAdapter()
    await teaching.help_answer(db_session, adapter, task=foreign_task, skill_name=skill.name,
                               skill_key=skill.key, mode="simpler")
    assert "FROZEN_SOURCE_ANSWER" not in adapter.prompts[0]
    assert "MUTABLE_LIVE" not in adapter.prompts[0]
    assert "UNRELATED_" not in adapter.prompts[0]


@pytest.mark.asyncio
async def test_rule_teaching_keeps_source_context_and_does_not_claim_model_generation(db_session, test_workspace):
    task, skill = await source_task(db_session, test_workspace)
    content = await teaching.build_teaching_content(db_session, SimpleNamespace(active=False),
        task=task, skill_name=skill.name, skill_key=skill.key)
    assert content["source"] == "rule"
    assert content["source_context_version"] == 2
    assert "ORIGINAL_USER_ANSWER" in content["example"]
    assert "FROZEN_STANDARD" in " ".join(content["principles"])
    assert "UNRELATED_" not in json.dumps(content)


@pytest.mark.asyncio
async def test_help_retries_legacy_rule_cache_after_failed_generation(db_session, test_workspace):
    task, skill = await source_task(db_session, test_workspace)
    task.content = {**task.content, "source_context_version": 2,
                    "help_cache": {"unclear": {"text": "旧模板", "source": "rule"}}}
    adapter = CapturingAdapter()
    working_chat = adapter.chat

    async def invalid_chat(*args, **kwargs):
        await working_chat(*args, **kwargs)
        return {"content": '{"text": []}'}

    adapter.chat = invalid_chat
    kwargs = dict(task=task, skill_name=skill.name, skill_key=skill.key, mode="unclear")
    failed = await teaching.help_answer(db_session, adapter, **kwargs)
    assert failed["source"] == "none" and failed["text"] is None and failed["error"]
    assert len(adapter.prompts) == 1
    assert task.content["help_cache"]["unclear"]["text"] == "旧模板"

    adapter.chat = working_chat
    result = await teaching.help_answer(db_session, adapter, **kwargs)
    assert result == {"mode": "unclear", "text": "source explanation", "source": "llm"}
    assert "FROZEN_STANDARD" in adapter.prompts[-1]
    assert await teaching.help_answer(db_session, adapter, **kwargs) == result
    assert len(adapter.prompts) == 2


@pytest.mark.asyncio
async def test_learning_task_language_switch_preserves_lesson_and_reuses_help_by_language(
    db_session, test_workspace, monkeypatch,
):
    task, _ = await source_task(db_session, test_workspace)
    task.status = "IN_PROGRESS"
    task.content = {
        "observation": {"answer_excerpt": "我的原始回答", "criterion": "幂等", "criterion_detail": "说明去重边界"},
        "artifact_text": "我的学习笔记",
    }
    await db_session.commit()

    async def workspace():
        return test_workspace

    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def session():
        async with session_factory() as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, deps.get_workspace, workspace)
    monkeypatch.setitem(app.dependency_overrides, get_session, session)
    monkeypatch.setattr(learning, "_llm", SimpleNamespace(active=False))
    path = f"/api/v1/learning/tasks/{task.id}"
    zh, en = {"Accept-Language": "zh-CN"}, {"Accept-Language": "en-US"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        lesson = (await client.get(f"{path}/content", headers=en)).json()["content"]
        assert lesson["source"] == "rule"
        assert "pipeline" in lesson["analogy"]
        assert lesson["example"] == "Original answer: 我的原始回答\nWhat to add: 幂等"
        assert lesson["principles"][0] == "Scoring standard: 说明去重边界"
        assert lesson["steps"][0].startswith("Read ")
        assert lesson["verify"] == task.acceptance
        assert (await client.get(f"{path}/content", headers=zh)).json()["content"] == lesson

        await db_session.refresh(task)
        legacy_help = {"text": "旧的中文解答", "source": "llm"}
        task.content = {**task.content, "help_cache": {"simpler": legacy_help}}
        await db_session.commit()

        async def help_for(headers, mode="simpler"):
            return await client.post(f"{path}/help", json={"mode": mode}, headers=headers)

        invalid = await help_for(en, "invalid")
        assert invalid.status_code == 422
        assert invalid.json()["detail"] == "Unknown help mode: invalid"
        unavailable = await help_for(en)
        assert unavailable.json()["text"] is None
        assert unavailable.json()["error"].startswith("Configure a model")
        assert (await help_for(zh)).json()["text"] == "旧的中文解答"

        calls = []

        async def chat(system, user, **kwargs):
            calls.append((system, user))
            if len(calls) == 1:
                return {"content": '{"text": []}'}
            text = "Explain the deduplication boundary." if message("language", "en") in system else "解释去重边界。"
            return {"content": {"text": text}}

        monkeypatch.setattr(learning, "_llm", SimpleNamespace(active=True, chat=chat))
        assert (await help_for(en)).json()["error"] == "AI didn’t generate a usable answer. Try again."
        english = (await help_for(en)).json()
        assert english["text"] == "Explain the deduplication boundary."
        assert (await help_for(en)).json() == english
        assert (await help_for(zh)).json()["text"] == "旧的中文解答"
        chinese_reply, english_reply = await asyncio.gather(help_for(zh, "diagram"), help_for(en, "diagram"))
        chinese = chinese_reply.json()
        assert chinese["text"] == "解释去重边界。"
        assert english_reply.json()["text"] == english["text"]
        assert (await help_for(en, "diagram")).json() == english_reply.json()
        assert (await help_for(zh, "diagram")).json() == chinese
        assert len(calls) == 4
        assert all("FROZEN_SOURCE_ANSWER" in user and "我的原始回答" in user for _, user in calls)

        saved = (await client.get(path, headers=zh)).json()
        assert saved["id"] == task.id and saved["status"] == "IN_PROGRESS"
        assert saved["source_exam_question_id"] == task.source_exam_question_id
        assert saved["content"]["help_cache"]["simpler"] == legacy_help
        assert {key: saved["content"][key] for key in lesson} == lesson
