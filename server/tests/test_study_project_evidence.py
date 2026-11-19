import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.study_project_evidence import collect_project_evidence


def project(**overrides):
    return SimpleNamespace(**{
        "id": 3, "meta": {"kind": "gitcode"}, "latest_valid_analysis_id": 9,
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
