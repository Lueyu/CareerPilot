import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from credential_fixtures import enroll_token
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import study_chat as api
from app.db import get_session
from app.models import Project, ProjectAnalysis, Workspace
from app.services import study_chat as worker
from app.services import study_project_evidence as evidence
from app.services.study_project_evidence import collect_project_evidence, snapshot_excerpts


def project(**overrides):
    return SimpleNamespace(**{
        "id": 3, "workspace_id": 5, "meta": {"kind": "gitcode"}, "latest_valid_analysis_id": 9,
        "last_seen_sha": "b" * 40, "generation": 2, **overrides,
    })


@pytest.mark.asyncio
async def test_bound_checkout_cites_current_code_tests_and_callers(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    body = b'def save_item(value):\n    password = "fixture-sensitive"\n    return value\n'
    (root / "storage.py").write_bytes(body)
    (root / "routes.py").write_text("from storage import save_item\n\ndef submit(value):\n    return save_item(value)\n")
    (root / "test_storage.py").write_text("def test_save_item():\n    assert save_item(1) == 1\n")
    (root / ".env.py").write_text("save_item = 'hidden-fixture'")
    monkeypatch.setenv("STUDY_PROJECT_ROOTS", json.dumps({"5:3": str(root)}))
    analysis = SimpleNamespace(id=9, source_revision="a" * 40, generation=2,
                               coverage_status="PARTIAL", snapshot={"coverage": {"unread_files": ["queued.py"]}},
                               report={"summary": "Obsolete implementation.", "unknowns": ["Runtime coverage unavailable."]})
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[project(), analysis]))
    sources = await collect_project_evidence(session, 5, [], 3, "Explain save_item")
    by_path = {row["path"]: row for row in sources if row["provenance"] == "repository_observation"}
    assert set(by_path) == {"storage.py", "routes.py", "test_storage.py"}
    assert by_path["storage.py"]["revision"] == "sha256:" + hashlib.sha256(body).hexdigest()
    assert by_path["storage.py"]["symbol"] == "save_item"
    assert by_path["storage.py"]["line_start"] == 1
    assert by_path["storage.py"]["line_end"] == 3
    assert "fixture-sensitive" not in str(sources)
    assert "hidden-fixture" not in str(sources)
    assert any(row["kind"] == "scope_gap" and "stale" in row["text"].lower() for row in sources)
    assert any(row["kind"] == "scope_gap" and "queued.py" in row["text"] for row in sources)
    assert any("Runtime coverage unavailable." in row["text"] for row in sources)
    assert not any(row["kind"] == "project_analysis" for row in sources)


@pytest.mark.asyncio
async def test_current_content_revision_changes_and_links_are_excluded(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    source = root / "storage.py"
    source.write_text("def save_item(): return 1\n")
    outside = tmp_path / "outside.py"
    outside.write_text("def save_item(): return 'outside fixture'\n")
    os.link(outside, root / "linked.py")
    monkeypatch.setenv("STUDY_PROJECT_ROOTS", json.dumps({"5:3": str(root)}))

    async def collect():
        session = SimpleNamespace(scalar=AsyncMock(side_effect=[project(), None]))
        return await collect_project_evidence(session, 5, [], 3, "save_item")

    first = await collect()
    source.write_text("def save_item(): return 2\n")
    second = await collect()
    assert first[0]["path"] == second[0]["path"] == "storage.py"
    assert second[0]["revision"] == "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    assert first[0]["revision"] != second[0]["revision"]
    assert "outside fixture" not in str(second)
    assert any(row["id"].endswith(":project_test") for row in second)
    assert any(row["id"].endswith(":project_caller") for row in second)


@pytest.mark.asyncio
async def test_foreign_project_is_rejected_before_evidence_lookup(monkeypatch):
    monkeypatch.setenv("STUDY_PROJECT_ROOTS", '{"5:3":"ignored"}')
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with pytest.raises(HTTPException, match="404"):
        await collect_project_evidence(session, 5, [], 3, "save_item")
    assert session.scalar.await_count == 1
    params = session.scalar.call_args.args[0].compile().params
    assert set(params.values()) == {3, 5}


@pytest.mark.asyncio
async def test_missing_exact_binding_does_not_read_project_metadata_path(tmp_path, monkeypatch):
    (tmp_path / "storage.py").write_text("def save_item(): pass")
    monkeypatch.setenv("STUDY_PROJECT_ROOTS", json.dumps({"6:3": str(tmp_path)}))
    session = SimpleNamespace(scalar=AsyncMock(side_effect=[project(
        latest_valid_analysis_id=None, meta={"kind": "github", "local_path": str(tmp_path)}), None]))
    sources = await collect_project_evidence(session, 5, [], 3, "save_item")
    assert sources and all(row["kind"] == "scope_gap" for row in sources)


REVISION = "b" * 40


def source_file(path, content, **overrides):
    return {"path": path, "content": content, "source_revision": REVISION,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(), **overrides}


def scanned(files, **overrides):
    return SimpleNamespace(**{"id": 9, "source_revision": REVISION, "generation": 2,
                             "coverage_status": "PARTIAL", "snapshot": {"files": files},
                             "report": {}, **overrides})


@pytest.mark.parametrize(("question", "path", "symbol"), [
    ("为什么写入前需要雪花时钟回拨检查？", "timing.py", "clock_guard"),
    ("结合项目解释任务如何重试", "policy.ts", "retryPolicy"),
    ("解释 `retry_policy`", "policy.ts", "retryPolicy"),
    ("项目中 RAG 怎么实现", "rag.py", "getRagContext"),
    ("查看 docs/worker.md", "docs/worker.md", None),
    ("保存项目中的不存在对象 `missing_store` 怎么重试", None, None),
    ("`retry` 如何处理？", None, None),
    ("解释另一个完全无关的领域", None, None),
])
def test_snapshot_search_matches_question_without_fabricating_support(question, path, symbol):
    files = [
        source_file("timing.py", "def clock_guard():  # 雪花时钟回拨检查\n    return True"),
        source_file("policy.ts", "export function retryPolicy() { return 2; }"),
        source_file("rag.py", "def getRagContext():\n    return []"),
        source_file("noise.py", "def coverage_report():\n    return 'a fixture'"),
        source_file("docs/worker.md", "# Worker study material\nThe worker processes a job."),
    ]
    found = snapshot_excerpts(scanned(files), project(), [question])
    if path is None:
        assert not found
    else:
        assert found[0]["path"] == path
        assert found[0]["symbol"] == symbol
        assert found[0]["revision"] == REVISION
        assert "noise.py" not in {row["path"] for row in found}
        if path.endswith(".md"):
            assert found[0]["provenance"] == "repository_documentation"


def test_legacy_snapshot_reads_pinned_files_even_without_record_revision():
    content = "def recover_batch():\n    return 3"
    snapshot = {"schema_version": 1, "source_revision": REVISION, "files": [
        {"path": "worker.py", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest(),
         "truncated": False, "line_count": 2},
    ]}
    run = scanned([], snapshot=snapshot)
    [result] = snapshot_excerpts(run, project(last_seen_sha=None), ["recover_batch"])
    assert result["text"] == content
    assert (result["revision"], result["line_start"], result["line_end"]) == (REVISION, 1, 2)
    assert not snapshot_excerpts(run, project(last_seen_sha="a" * 40), ["recover_batch"])
    snapshot["source_revision"] = "a" * 40
    assert not snapshot_excerpts(run, project(last_seen_sha=None), ["recover_batch"])


@pytest.mark.parametrize("changes", [
    {"path": "../outside.py"},
    {"path": "src/.env.py"},
    {"path": "src/api_tokens.py"},
    {"source_revision": "c" * 40},
    {"project_id": 4},
    {"workspace_id": 6},
    {"truncated": True},
    {"content_sha256": "c" * 64},
])
def test_snapshot_rejects_unsafe_source_records(changes):
    row = source_file("worker.py", "def recover_batch(): return 3")
    assert snapshot_excerpts(scanned([row]), project(), ["recover_batch"])
    assert not snapshot_excerpts(scanned([{**row, **changes}]), project(), ["recover_batch"])


def test_snapshot_rejects_registry_disagreement_and_duplicate_paths():
    row = source_file("worker.py", "def recover_batch(): return 3", source_id="fixture", project_id=3, workspace_id=5)
    run = scanned([], snapshot={"files": [row], "source_registry": {"fixture": row.copy()}})
    assert snapshot_excerpts(run, project(), ["recover_batch"])
    run.snapshot["source_registry"]["fixture"]["path"] = "elsewhere.py"
    assert not snapshot_excerpts(run, project(), ["recover_batch"])
    assert not snapshot_excerpts(scanned([row, row]), project(), ["recover_batch"])


def test_snapshot_counts_utf8_bytes_per_file_and_across_files(monkeypatch):
    content = "def recover_batch():\n    return '一个例子'"
    first = source_file("a.py", content)
    second = source_file("b.py", content)
    monkeypatch.setattr(evidence, "MAX_FILE_BYTES", len(content))
    assert not snapshot_excerpts(scanned([first]), project(), ["recover_batch"])
    monkeypatch.setattr(evidence, "MAX_FILE_BYTES", len(content.encode()))
    monkeypatch.setattr(evidence, "MAX_TOTAL_BYTES", len(content.encode()) + len(content))
    found = snapshot_excerpts(scanned([first, second]), project(), ["recover_batch"])
    assert [row["path"] for row in found] == ["a.py"]


def test_snapshot_search_can_find_content_beyond_the_first_24_paths():
    files = [source_file(f"pkg/part_{i:02}.py", "def placeholder(): return None") for i in range(40)]
    files.append(source_file("worker.py", "def recover_batch(): return 3"))
    [result] = snapshot_excerpts(scanned(files), project(), ["recover_batch"])
    assert result["path"] == "worker.py"


def test_snapshot_skips_overlong_content_and_preserves_later_line_numbers():
    long_line = "x" * 2500
    visible = source_file("worker.py", f"{long_line}\ndef recover_batch():\n    return 3")
    [result] = snapshot_excerpts(scanned([visible]), project(), ["recover_batch"])
    assert (result["line_start"], result["line_end"]) == (2, 3)
    sensitive = source_file("worker.py", f"fixture_token = '{long_line}'\ndef recover_batch(): return 3")
    assert not snapshot_excerpts(scanned([sensitive]), project(), ["recover_batch"])


def test_snapshot_redacts_sensitive_assignments_and_multiline_values():
    content = "def recover_batch():\n    fixture_token =\n        'synthetic-value'\n    return 3"
    [result] = snapshot_excerpts(scanned([source_file("worker.py", content)]), project(), ["recover_batch"])
    assert "synthetic-value" not in result["text"]
    assert result["line_end"] == 4
    wrapped = "def recover_batch():\n    fixture_token = (\n        'synthetic-value'\n    )\n    return 3"
    assert not snapshot_excerpts(scanned([source_file("worker.py", wrapped)]), project(), ["recover_batch"])


@pytest.mark.asyncio
async def test_unsupported_query_keeps_workspace_profile_and_explicit_code_gap(monkeypatch):
    monkeypatch.setenv("STUDY_PROJECT_ROOTS", "{}")
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[project(meta={"profile": {"role": "我负责队列接入"}}),
                                      scanned([source_file("worker.py", "def run_queue(): return 3")])]),
        execute=AsyncMock(return_value=[("queue", "队列")]),
    )
    found = await collect_project_evidence(session, 5, [2], 3, "请问这个项目的源码怎么处理")
    assert any(row["id"] == "P3:no-search-terms" for row in found)
    assert any(row["provenance"] == "user_statement" and "我负责队列接入" in row["text"] for row in found)
    assert not any(row["provenance"] == "repository_observation" for row in found)


@pytest.mark.asyncio
async def test_workspace_token_study_turn_uses_own_current_scan(db_session, test_workspace, monkeypatch):
    db, ws = db_session, test_workspace
    foreign = Workspace(slug=uuid4().hex, name="Other fixture workspace")
    owned = Project(workspace_id=ws.id, name="Fixture worker", owner="fixture", repo="worker", repo_url="",
                    branch="main", generation=2, last_seen_sha=REVISION, meta={"profile": {"role": "我负责队列接入"}})
    db.add_all([owned, foreign])
    await db.flush()
    foreign_id = foreign.id
    foreign_project = Project(workspace_id=foreign.id, name=owned.name, owner=owned.owner, repo=owned.repo, repo_url="")
    db.add(foreign_project)
    await db.flush()
    current = source_file("retry_policy.py", "def retry_attempt():\n    return 3",
                          source_id="owned-fixture", workspace_id=ws.id, project_id=owned.id)
    stale = ProjectAnalysis(workspace_id=ws.id, project_id=owned.id, request_key="stale-fixture",
                            source_revision="a" * 40, generation=2, status="SUCCESS",
                            snapshot={"files": [source_file("stale.py", "def retry_attempt(): return 7")]})
    db.add_all([
        stale,
        ProjectAnalysis(workspace_id=ws.id, project_id=owned.id, request_key="current-fixture", status="BLOCKED",
                        source_revision=REVISION, generation=2, pipeline_version="fixture-v2",
                        report_status="PARTIAL", coverage_status="PARTIAL",
                        snapshot={"files": [current], "source_registry": {current["source_id"]: current},
                                  "provenance": {"workspace_id": ws.id, "project_id": owned.id}}),
        ProjectAnalysis(workspace_id=foreign.id, project_id=owned.id, request_key="wrong-workspace-fixture",
                        source_revision=REVISION, generation=2, status="SUCCESS",
                        snapshot={"files": [source_file("foreign.py", "def retry_attempt(): return 9")]}),
    ])
    await db.flush()
    owned.latest_valid_analysis_id = stale.id
    await db.commit()

    monkeypatch.setenv("STUDY_PROJECT_ROOTS", "{}")
    token = uuid4().hex
    await enroll_token(db, token, ws.slug)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)

    async def fixture_session():
        async with factory() as session:
            yield session

    async def fixture_generate(turn, settings, deadline, trace, save, fail, language):
        assert language == "zh-CN"
        await save("completed", status="completed", content="Synthetic response", expires_at=None)

    monkeypatch.setattr(worker, "_generate", fixture_generate)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_session] = fixture_session
    headers = {"X-Api-Token": token, "X-Workspace": ws.slug}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            inaccessible = await client.post("/study/sessions", headers=headers, json={"project_id": foreign_project.id})
            assert inaccessible.status_code == 404
            start = await client.post("/study/sessions", headers=headers, json={"project_id": owned.id})
            assert start.is_success
            url = f"/study/sessions/{start.json()['id']}"
            sent = await client.post(url + "/messages", headers=headers, json={
                "content": "结合项目讲解任务如何重试", "action": "project", "request_id": "retry-fixture",
            })
            assert sent.is_success
            detail = (await client.get(url, headers=headers)).json()
        reply = detail["messages"][-1]
        assert reply["status"] == "completed"
        code = [item for item in reply["sources"] if item["provenance"] == "repository_observation"]
        assert [(item["path"], item["revision"], item["symbol"]) for item in code] == [
            ("retry_policy.py", REVISION, "retry_attempt"),
        ]
        assert any(item["provenance"] == "user_statement" and "我负责队列接入" in item["text"]
                   for item in reply["sources"])
        assert any(item["kind"] == "scope_gap" and "覆盖范围" in item["text"] for item in reply["sources"])
    finally:
        await db.rollback()
        await db.execute(delete(Workspace).where(Workspace.id == foreign_id))
        await db.commit()
