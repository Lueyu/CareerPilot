"""Locale preferences belong to a request or an admitted turn, never to shared process state."""
import asyncio
import json
import secrets
from pathlib import Path

import httpx
import pytest

from app.i18n import message, negotiated_language, request_language, using_language
from app.models.public_coach import CoachThread, CoachTurn
from app.public_main import app as public_app
from app.services.public_coach import TurnContext
from app.services.study_chat import teaching_action
from app.services.study_notes import Citation, NoteOutput, NotePoint, render_note


@pytest.mark.parametrize(("header", "expected"), [
    ("", "zh-CN"), ("de", "zh-CN"), ("en-GB", "en"), ("en;q=0,zh-Hans", "zh-CN"),
    ("zh;q=.5, en-US;q=.9", "en"), ("en;q=oops, zh-TW", "zh-CN"),
])
def test_language_negotiation(header, expected):
    assert negotiated_language(header) == expected


def test_backend_catalogs_have_the_same_keys():
    root = Path(__file__).resolve().parents[1] / "app" / "locales"
    read = lambda name: json.loads((root / name).read_text(encoding="utf-8"))
    assert read("en.json").keys() == read("zh-CN.json").keys()


@pytest.mark.asyncio
async def test_concurrent_registered_auth_errors_use_request_language(monkeypatch):
    monkeypatch.setenv("SEED_PUBLIC_ORIGIN", "https://seed.test")
    monkeypatch.setenv("SEED_SESSION_SECRET", secrets.token_urlsafe(48))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=public_app), base_url="https://seed.test") as client:
        async def rejected(language):
            return await client.post("/api/v1/auth/login", json={}, headers={"Accept-Language": language})

        en, zh = await asyncio.gather(rejected("en-US"), rejected("zh-CN"))
    assert en.status_code == zh.status_code == 403
    for response, locale in ((en, "en"), (zh, "zh-CN")):
        assert response.json() == {"detail": message("auth.origin", locale)}
        assert response.headers["content-language"] == locale
        assert "accept-language" in response.headers["vary"].lower()
    assert request_language() == "zh-CN"


def test_registered_interviewer_uses_admitted_language_and_preserves_quotes():
    original = "我实现了任务去重。"
    thread = CoachThread(mode="interview", role="工程师", direction="", question_title="哪一部分由你负责？")
    turn = CoachTurn(action="answer", user_text=original, response_language="en")
    with using_language("zh-CN"):
        system, transcript = TurnContext(thread, turn, [], [], []).messages()
    assert message("language", "en") in system["content"]
    assert json.loads(transcript["content"])["当前用户原文"] == original


def test_saved_note_language_applies_to_headings_and_keeps_citations():
    quote = "这是我原来提交的回答。"
    snapshot = [{"output_language": "en", "session_id": 3, "id": 8, "role": "user", "text": quote, "materials": []}]
    note = NoteOutput(title="Revision", points=[
        NotePoint(kind="explanation", text="The learner described their implementation.",
                  citations=[Citation(message_id=8, quote=quote)])
    ])
    body, sources = render_note(note, snapshot)
    assert "## Core explanation" in body and "[Message 8]" in body
    assert sources[0]["quote"] == quote


@pytest.mark.parametrize(("text", "action"), [
    ("Please interview me", "interview"), ("Review my answer", "critique"), ("Show a sequence diagram", "diagram"),
])
def test_english_learning_requests_select_the_matching_action(text, action):
    assert teaching_action("explain", text) == action
