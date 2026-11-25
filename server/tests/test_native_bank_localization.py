"""Native bank and workshop language contracts using synthetic records."""

import re
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.deps import get_workspace
from app.api.v1 import bank, question_generation
from app.db import get_session
from app.i18n import LanguageMiddleware
from app.models import Project, Question, Skill
from app.models.question import SOURCE_PRIVATE


def client_for(session, workspace):
    app = FastAPI()
    app.add_middleware(LanguageMiddleware)
    app.include_router(bank.router, prefix="/api/v1")
    app.include_router(question_generation.router, prefix="/api/v1")

    async def db():
        yield session

    async def ws():
        return workspace

    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_workspace] = ws
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture/api/v1")


@pytest.mark.asyncio
async def test_native_answer_without_model_uses_request_locale_and_preserves_saved_answers(
    db_session, test_workspace,
):
    skill = await db_session.scalar(select(Skill).where(Skill.key == "backend"))
    if skill is None:
        skill = Skill(key="backend", name="后端与系统设计")
        db_session.add(skill)
        await db_session.flush()

    def question(title, **fields):
        return Question(short_code="F-" + uuid4().hex[:15], title=title, qtype="open", topic="backend",
                        workspace_id=test_workspace.id, skill_id=skill.id, source_type=SOURCE_PRIVATE, **fields)

    english = question("How would you verify that delayed webhooks are delivered only once?")
    chinese = question("如何验证延迟的回调只投递一次？")
    legacy = question("现有参考答案", answer_html="<p>现有参考答案原文</p>",
                      rubric={"points": [{"title": "已保存的评分点"}]})
    db_session.add_all([english, chinese, legacy])
    await db_session.commit()

    async with client_for(db_session, test_workspace) as client:
        async def answer(row, language=None):
            headers = {"Accept-Language": language} if language else {}
            response = await client.post(f"/bank/questions/{row.short_code}/answer", headers=headers)
            assert response.status_code == 200, response.text
            return response.json()

        generated = await answer(english, "en")
        assert generated["generated"] and generated["source"] == generated["rubric"]["source"] == "rule"
        assert "Practice outline" in generated["answer_html"]
        assert "A model-generated answer is unavailable." in generated["answer_html"]
        assert "Scoring criteria" in generated["answer_html"]
        assert not re.search(r"[\u4e00-\u9fff]", generated["answer_html"])
        points = generated["rubric"]["points"]
        assert points and all(point["detail"] and point["keywords"] for point in points)
        assert all(not re.search(r"[\u4e00-\u9fff]", str(point)) for point in points)

        zh = await answer(chinese)
        assert zh["source"] == "rule" and "评分要点" in zh["answer_html"]
        assert any(re.search(r"[\u4e00-\u9fff]", point["detail"]) for point in zh["rubric"]["points"])
        saved = await answer(english, "zh-CN")
        assert not saved["generated"]
        assert (saved["answer_html"], saved["rubric"]) == (generated["answer_html"], generated["rubric"])
        saved_legacy = await answer(legacy, "en")
        assert not saved_legacy["generated"]
        assert saved_legacy["answer_html"] == "<p>现有参考答案原文</p>"
        assert saved_legacy["rubric"] == {"points": [{"title": "已保存的评分点"}]}


@pytest.mark.asyncio
async def test_workshop_suggestions_localize_generated_copy_and_keep_project_names(
    db_session, test_workspace,
):
    project = Project(workspace_id=test_workspace.id, name="合成回调服务", owner="fixture",
                      repo="webhooks", repo_url="https://example.test/fixture/webhooks")
    db_session.add(project)
    await db_session.commit()

    async with client_for(db_session, test_workspace) as client:
        async def suggestion(language):
            response = await client.get("/question-generation/suggestions", headers={"Accept-Language": language})
            assert response.status_code == 200, response.text
            assert response.headers["content-language"] == language
            assert "accept-language" in response.headers["vary"].lower()
            return next(item for item in response.json()["items"] if item["id"] == f"project-{project.id}")

        zh, en = await suggestion("zh-CN"), await suggestion("en")
        assert zh["project_ids"] == en["project_ids"] == [project.id]
        assert en["title"] == "Prepare follow-up questions for 合成回调服务"
        assert zh["title"] != en["title"] and "合成回调服务" in zh["title"]
        assert en["reason"].startswith("This project has no reviewed questions yet.")
        assert en["purpose"].startswith("Create follow-up questions")
        assert zh["reason"] != en["reason"] and zh["purpose"] != en["purpose"]
