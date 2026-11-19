"""Static repository scans describe inspection coverage, never competence."""
import pytest

from app.services import project_analyzer as analyzer


def snapshot(paths):
    return {"tree": [{"path": path, "type": "blob"} for path in paths]}


@pytest.mark.asyncio
async def test_many_unread_paths_do_not_produce_a_maximum_ability_score():
    result = await analyzer.analyze_snapshot(snapshot([f"server/empty_{i}.py" for i in range(9)]))
    assert "matrix" not in result, "file counts must not masquerade as ability scores"
    coverage = result["coverage"]
    assert coverage["files_total"] == 9
    assert coverage["files_read"] == coverage["files_sampled"] == 0
    backend = next(row for row in coverage["domains"] if row["key"] == "backend")
    assert backend == {"key": "backend", "path_matches": 9, "content_matches": 0, "readme_claims": 0}
    assert all("level" not in row and "score" not in row for row in coverage["domains"])


@pytest.mark.asyncio
async def test_coverage_distinguishes_attempted_read_failed_and_truncated_files():
    reads = []
    async def reader(path):
        reads.append(path)
        if path.endswith("0.py"):
            raise RuntimeError("not readable")
        if path.endswith("1.py"):
            return None
        return "# session\n" + ("x" * 13000 if path.endswith("2.py") else "pass\n")
    data = snapshot([f"server/example_{i}.py" for i in range(10)] + ["server/subdirectory/"])
    data["tree"].append({"path": "__TRUNCATED__", "type": "meta"})
    result = await analyzer.analyze_snapshot(data, file_reader=reader)
    coverage = result["coverage"]
    assert coverage["files_total"] == 10
    assert coverage["files_sampled"] == 8
    assert coverage["files_read"] == 6
    assert coverage["files_failed"] == 2
    assert coverage["files_truncated"] == 1
    assert coverage["tree_truncated"] is True
    assert not any(path.endswith("/") for path in reads)
    assert "正确性" in " ".join(coverage["limitations"])
    assert "关键词" in next(f["detail"] for f in result["findings"] if f["level"] == "VERIFIED")


@pytest.mark.asyncio
async def test_readme_claims_and_language_metadata_are_not_source_content_matches():
    result = await analyzer.analyze_snapshot({"tree": [], "readme": "RAG 知识库", "languages": {"Python": 100}})
    coverage = result["coverage"]
    assert coverage["files_total"] == coverage["files_read"] == 0
    rag = next(row for row in coverage["domains"] if row["key"] == "rag")
    assert rag["readme_claims"] == 1
    assert sum(row["content_matches"] for row in coverage["domains"]) == 0


def test_legacy_scans_hide_old_scores_and_do_not_invent_historical_read_counts():
    normalize = getattr(analyzer, "coverage_for_scan", None)
    assert callable(normalize), "legacy scans need an honest coverage view"
    legacy = {"matrix": [{"key": "backend", "level": 5, "findings": 9}], "stats": {"files": 9, "truncated": True}}
    coverage = normalize(legacy)
    assert coverage["history_unknown"] is True
    assert coverage["files_total"] == 9
    assert coverage["files_read"] is None
    assert coverage["files_sampled"] is None
    assert coverage["domains"] == []
    assert "matrix" not in coverage


def test_scan_api_view_does_not_serialize_legacy_ability_matrix():
    from app.api.v1.projects import _scan_view
    from app.models.project import ProjectScan
    scan = ProjectScan(id=1, stats={"matrix": [{"level": 5}], "stats": {"files": 9}})
    view = _scan_view(scan)
    assert "matrix" not in view["stats"]
    assert view["stats"]["coverage"]["files_read"] is None


def test_asset_draft_does_not_republish_legacy_ability_scores():
    from datetime import datetime, timezone
    from app.models.project import Project
    from app.models.project_analysis import ProjectAnalysis
    from app.services.asset_pipeline import _render_markdown, repository_observations
    project = Project(name="Repository", owner="example", repo="repo", repo_url="https://github.com/example/repo")
    report = ProjectAnalysis(report_published_at=datetime.now(timezone.utc), report={
        "matrix": [{"key": "backend", "level": 4.9, "findings": 9}],
        "claims": [{"kind": "CODE_OBSERVED", "title": "入口", "text": "调用路由",
                    "citations": [{"path": "main.py", "start_line": 4, "end_line": 5}]}]})
    text = _render_markdown(project, report, repository_observations(report), "CURRENT")
    assert "main.py:4-5" in text
    assert "4.9" not in text
    assert "能力矩阵" not in text
    assert "个人职责和结果需要本人单独确认" in text
