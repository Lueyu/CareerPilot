"""HTTP acceptance using an isolated PostgreSQL schema and synthetic model replies."""

import asyncio
import io
import json
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import delete, select

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import public_library as library
from app.db import get_session
from app.models import Question, Workspace


@pytest_asyncio.fixture
async def browser(db_session, test_workspace, monkeypatch):
    other = Workspace(slug=f"library-other-{uuid4().hex}", name="Other workspace", target_role="")
    legacy = Question(short_code=f"legacy-{uuid4().hex[:20]}", title="旧版公共题", topic="legacy")
    db_session.add_all([other, legacy])
    await db_session.commit()
    owner_id, other_id, legacy_id = test_workspace.id, other.id, legacy.id
    calls = []

    async def fake_model(workspace_id, request_key, messages, *, max_tokens):
        assert workspace_id == owner_id and max_tokens <= 1600
        purpose = request_key.split(":")[1]
        payload = json.loads(messages[1]["content"])
        calls.append(purpose)
        if purpose == "directions":
            if payload["jd"] == "限流":
                raise HTTPException(429, "今日额度已用完")
            assert "实验" in payload["jd"]
            return '[{"name":"活动归因","example":"怎样排除同期促销对拉新的影响？"}]'
        if purpose == "starter":
            assert payload["role"] == "产品运营"
            fence = chr(96) * 3
            return fence + 'json\n[{"title":"预算减半时如何验证转化？","answer":"先做小流量实验。"}]\n' + fence
        assert purpose == "import"
        return json.dumps(
            [
                {"title": "原文含有哪些维度？", "source_answer": "客群分层与促活", "answer": ""},
                {"title": "如何选对照组？", "source_answer": "业绩翻倍", "answer": "<script>检查样本偏差</script>"},
            ],
            ensure_ascii=False,
        )

    app = FastAPI()
    app.include_router(library.router, prefix="/api/v1")

    async def current_workspace(x_test_space: int = Header(), db=Depends(get_session)):
        if x_test_space not in {owner_id, other_id}:
            raise HTTPException(401)
        return await db.get(Workspace, x_test_space)

    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = database
    app.dependency_overrides[library.current_workspace] = current_workspace
    monkeypatch.setattr(library, "complete_text", fake_model)
    transport = httpx.ASGITransport(app=app)
    url = "http://test/api/v1/library/"
    try:
        async with (
            httpx.AsyncClient(transport=transport, base_url=url, headers={"x-test-space": str(owner_id)}) as owner,
            httpx.AsyncClient(transport=transport, base_url=url, headers={"x-test-space": str(other_id)}) as peer,
        ):
            yield owner, peer, calls, owner_id, other_id
    finally:
        await db_session.execute(delete(Question).where(Question.id == legacy_id))
        await db_session.execute(delete(Workspace).where(Workspace.id == other_id))
        await db_session.commit()


async def _role(client, role="产品运营", topic="用户增长", **body):
    result = await client.put("role", json={"role": role, **body})
    assert result.status_code == 200, result.text
    result = await client.put("direction", json={"role": role, "topic": topic})
    assert result.status_code == 200, result.text
    return {"role": role, "topic": topic}


async def _upload(client, selection, filename, raw):
    return await client.post("imports", data=selection, files={"file": (filename, raw)})


def _pdf(text_lines):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=500)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    ),
                }
            ),
        }
    )
    if text_lines:
        stream = DecodedStreamObject()
        lines = b" 0 -20 Td ".join(f"({line}) Tj".encode() for line in text_lines)
        stream.set_data(b"BT /F1 12 Tf 30 400 Td " + lines + b" ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _docx(text_lines):
    from docx import Document

    document = Document()
    for line in text_lines:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _markdown(text_lines):
    return "\n".join(text_lines).encode()


@pytest.mark.asyncio
async def test_roles_gateway_fallback_starter_and_reload(browser):
    owner, peer, calls, _, _ = browser
    assert (await owner.get("roles")).json() == {"selected_role": None, "roles": []}
    assert (await owner.get("questions")).json()["questions"] == []

    direction = await _role(owner, jd="限流")
    assert (await owner.post("directions", json={"role": "产品运营"})).status_code == 429
    view = (await owner.get("roles")).json()["roles"][0]
    assert view["selected_topic"] == direction["topic"] and view["directions"][0]["origin"] == "preset"
    example = (await owner.post("example", json=direction)).json()
    assert (await owner.post("example", json=direction)).json()["id"] == example["id"]

    await _role(owner, jd="要求做归因实验")
    view = (await owner.post("directions", json={"role": "产品运营"})).json()
    assert any(entry["name"] == "活动归因" and entry["origin"] == "model" for entry in view["directions"])
    starter = (await owner.post("starter", json=direction)).json()
    assert starter["questions"][0]["title"] == "预算减半时如何验证转化？"
    assert (await owner.post("starter", json=direction)).json() == starter
    assert calls.count("starter") == 1

    await _role(owner, "后端开发", "接口与数据一致性")
    assert (await owner.get("roles")).json()["selected_role"] == "后端开发"
    assert (await owner.get("questions", params={"role": "后端开发"})).json()["questions"] == []
    assert len((await owner.get("questions", params=direction)).json()["questions"]) == 2
    await owner.put("role", json={"role": "产品运营"})
    view = next(row for row in (await owner.get("roles")).json()["roles"] if row["role"] == "产品运营")
    assert view["jd"] == "要求做归因实验" and view["selected_topic"] == "用户增长"

    await _role(peer)
    assert (await peer.get("questions", params=direction)).json()["questions"] == []
    assert (await peer.get("roles")).json()["roles"][0]["jd"] is None


@pytest.mark.parametrize(("filename", "make_bytes"), [("试.md", _markdown), ("试.docx", _docx), ("试.pdf", _pdf)])
@pytest.mark.asyncio
async def test_upload_edit_reopen_approve_and_retry(browser, filename, make_bytes):
    owner, peer, calls, _, _ = browser
    selection = await _role(owner)
    raw = make_bytes(
        [
            "Q: How would you verify growth?",
            "A: Source: cohort retention.",
            "Q: How do you pick a control group?",
            "A: Random assignment.",
        ]
    )
    response = await _upload(owner, selection, filename, raw)
    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch["status"] == "REVIEW", batch
    assert [item["source_answer"] for item in batch["items"]] == ["Source: cohort retention.", "Random assignment."]
    assert not calls
    assert (await _upload(owner, selection, filename, raw)).json()["id"] == batch["id"]
    endpoint = f"imports/{batch['id']}"
    await _role(peer)
    assert (await peer.get("imports")).json()["imports"] == []
    assert (await peer.get(endpoint)).status_code == 404

    edits = {
        "items": [
            {"id": item["id"], "title": item["title"], "answer": "Edited before approval", "selected": index == 0}
            for index, item in enumerate(batch["items"])
        ]
    }
    assert (await owner.put(endpoint + "/draft", json=edits)).status_code == 200
    reopened = (await owner.get(endpoint)).json()
    assert reopened["items"][0]["answer"] == "Edited before approval"
    assert reopened["items"][1]["selected"] is False
    assert reopened["items"][0]["source_answer"] == "Source: cohort retention."
    assert (await peer.post(endpoint + "/approve", json=edits)).status_code == 404

    first, second = await asyncio.gather(
        owner.post(endpoint + "/approve", json=edits),
        owner.post(endpoint + "/approve", json=edits),
    )
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert first.json() == second.json()
    items = first.json()["items"]
    assert items[0]["question_id"] and items[1]["status"] == "DRAFT"
    rows = (await owner.get("questions", params=selection)).json()["questions"]
    assert len(rows) == 1 and rows[0]["answer"] == "Edited before approval"
    assert rows[0]["role"] == selection["role"] and rows[0]["topic"] == selection["topic"]
    assert (await peer.get("questions")).json()["questions"] == []


@pytest.mark.asyncio
async def test_model_provenance_export_and_parse_failure(browser, db_session):
    owner, peer, calls, owner_id, other_id = browser
    selection = await _role(owner)
    failed = (await _upload(owner, selection, "扫描版.pdf", _pdf([]))).json()
    assert failed["status"] == "FAILED" and "可选中文字" in failed["error"]
    assert (await owner.get(f"imports/{failed['id']}")).json() == failed
    batch = (await _upload(owner, selection, "杂记.markdown", "客群分层与促活是这周讨论的重点。".encode())).json()
    assert batch["status"] == "REVIEW" and batch["items"] == []
    path = f"imports/{batch['id']}/suggest"
    assert (await peer.post(path)).status_code == 404
    suggested = (await owner.post(path)).json()
    assert (await owner.post(path)).json() == suggested
    assert calls == ["import"]
    shown = suggested["items"]
    assert shown[0]["source_answer"] == "客群分层与促活" and shown[0]["ai_title"]
    assert shown[1]["source_answer"] == "" and shown[1]["ai_answer"].startswith("<script>")

    edit = {key: shown[1][key] for key in ("id", "title", "answer", "selected")}
    response = await owner.post(f"imports/{batch['id']}/approve", json={"items": [edit]})
    assert response.status_code == 200, response.text
    exported = await library.export_library(db_session, owner_id)
    assert exported["roles"][0]["selected_topic"] == "用户增长"
    assert exported["imports"][1]["source_text"] == "客群分层与促活是这周讨论的重点。"
    assert exported["imports"][1]["items"][1]["question_id"] == exported["questions"][0]["id"]
    assert "&lt;script&gt;" in exported["questions"][0]["answer_html"]
    json.dumps(exported)
    assert await library.export_library(db_session, other_id) == {
        "selected_role": None,
        "roles": [],
        "imports": [],
        "questions": [],
    }
    row = await db_session.scalar(select(Question).where(Question.id == exported["questions"][0]["id"]))
    assert row.skill_id is None and row.generation_meta["assessment_use"] == "PRACTICE_ONLY"


@pytest.mark.asyncio
async def test_upload_caps_and_wrong_format(browser):
    owner, _, calls, _, _ = browser
    selection = await _role(owner)
    assert (await _upload(owner, selection, "bad.txt", b"plaintext")).status_code == 415
    assert (await _upload(owner, selection, "big.md", b"x" * (library.MAX_UPLOAD_BYTES + 20_000))).status_code == 413

    zipped = io.BytesIO()
    with ZipFile(zipped, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"x" * (17 * 1024 * 1024))
    blocked = (await _upload(owner, selection, "large.docx", zipped.getvalue())).json()
    assert blocked["status"] == "FAILED" and "解包体积" in blocked["error"]
    over_limit = (await _upload(owner, selection, "large.md", b"x" * 24_001)).json()
    assert over_limit["status"] == "FAILED" and "24,000" in over_limit["error"]
    assert calls == []
