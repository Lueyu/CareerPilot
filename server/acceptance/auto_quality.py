"""Score CareerPilot's production Auto policy and project evidence offline."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from contextlib import asynccontextmanager, chdir
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

TIERS = ("flash", "strong")
K_VALUES = (1, 3, 5, 8)
APP_PREFIX = "server/app/"
ROUTER_PATH = "services/study_routing.py"
BASELINE = "70660b21f2e635f9dc80851d9d9bfb011c496429"
FIXTURE_SUITES = {
    "primary": (
        "auto_quality_v1.json",
        "e55a67f83a1b620bff605762b63a3ab5fc7ddfbc43ce26fcac99c6e95f92340d",
    ),
    "confirmation": (
        "auto_quality_confirmation_v1.json",
        "f3bc87e8135a3711d153beb8d62f55095e1c1e580f42cdcf7a5d9e0c6ac056c9",
    ),
}
WORKSPACE_ID = 42
PROJECT_ID = 17
CURRENT = "a" * 40
OLD = "b" * 40


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_fixture(path, expected_sha256):
    raw = path.read_bytes()
    if digest(raw) != expected_sha256:
        raise ValueError("Fixture SHA-256 differs from the frozen design")
    fixture = json.loads(raw)
    if fixture["format"] != 1:
        raise ValueError("Unsupported fixture format")
    ids = [case["id"] for group in ("routing", "retrieval") for case in fixture[group]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case ID")
    for case in fixture["routing"]:
        if case["expected"] not in TIERS:
            raise ValueError(f"{case['id']}: unknown expected tier")
    for case in fixture["retrieval"]:
        paths = [entry["path"] for entry in case["relevant"]]
        if len(paths) != len(set(paths)):
            raise ValueError(f"{case['id']}: duplicate relevant path")
        resolve_gold(case, fixture["projects"][case["project"]])
    return fixture


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
    ).stdout


def manifest(root):
    return {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def materialize_source(repo, root, ref):
    if ref:
        revision = git(repo, "rev-parse", f"{ref}^{{commit}}").decode().strip()
        if revision != BASELINE:
            raise ValueError("--baseline must reference the frozen dev revision")
        archive = git(repo, "archive", "--format=zip", revision, "server/app")
        with zipfile.ZipFile(io.BytesIO(archive)) as snapshot:
            snapshot.extractall(root)
        changes = []
    else:
        revision = git(repo, "rev-parse", "HEAD").decode().strip()
        paths = git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "server/app")
        changes_raw = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "server/app")
        changes = changes_raw.decode().strip("\0").split("\0") if changes_raw else []
        file_paths = {name for name in paths.decode().split("\0") if name.startswith(APP_PREFIX)}
        for name in sorted(file_paths):
            source = repo / name
            if source.is_file():
                dest = root / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
        original = {
            name.removeprefix(APP_PREFIX): digest((repo / name).read_bytes())
            for name in sorted(file_paths) if (repo / name).is_file()
        }
        if original != manifest(root / "server/app") or changes_raw != git(
            repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "server/app",
        ):
            raise RuntimeError("Candidate sources changed during capture. Rerun on a stable working tree.")
    files = manifest(root / "server/app")
    return {
        "kind": "git_archive" if ref else "working_copy",
        "revision": revision,
        "changes": changes,
        "file_count": len(files),
        "source_sha256": digest(json.dumps(files, sort_keys=True).encode()),
        "router_sha256": files[ROUTER_PATH],
        "changed_file_sha256": {
            name: files[name] for name in files
            if any(entry[3:] == APP_PREFIX + name for entry in changes)
        },
    }


class FixtureSession:
    """Supply ORM-shaped rows while production builds and consumes its own queries."""

    def __init__(self, project=None, analysis=None, jd=None, reply=None, messages=(), role=""):
        self.rows = {
            "Project": project, "ProjectAnalysis": analysis, "JdSource": jd,
        }
        self.messages = {m.id: m for m in (*messages, reply) if m is not None}
        self.role = role

    async def scalar(self, statement):
        name = statement.column_descriptions[0]["name"]
        if name != "StudyMessage":
            return self.rows[name]
        ids = [value for key, value in statement.compile().params.items() if key.startswith("id_")]
        if not ids:
            raise ValueError("Unexpected study message query")
        return self.messages.get(ids[0])

    async def get(self, entity, identifier):
        if entity.__name__ != "StudySession" or identifier != 1:
            raise ValueError("Unexpected study session lookup")
        project = self.rows["Project"]
        return SimpleNamespace(
            id=1, workspace_id=WORKSPACE_ID,
            context={"topic": "Interview coaching", "target_role": self.role,
                     "skill_ids": [], "project_id": project.id if project else None, "mode": "auto"},
        )


def previous_messages(case, project_id):
    if "prior_user" not in case:
        return []
    user = SimpleNamespace(id=1, role="user", content=case["prior_user"], action="explain", status="completed",
                           coach={"parent_message_id": None}, project_id=project_id, sources=[])
    action = case.get("prior_action", "explain")
    reply = SimpleNamespace(
        id=2, role="assistant", content=case.get("prior_reply", "We discussed the earlier request."),
        action=action, project_id=project_id, sources=[], status="completed",
        coach={"parent_message_id": 1, "trace": {"tier": case.get("prior_tier", "flash"), "action": action}},
    )
    return [user, reply]


def project_rows(storage, files):
    project = SimpleNamespace(
        id=PROJECT_ID, workspace_id=WORKSPACE_ID, generation=2, name="Synthetic project",
        last_seen_sha=CURRENT, latest_valid_analysis_id=1, archived_at=None, meta={},
    )
    if storage == "checkout":
        return project, None
    stale = storage.startswith("stale_")
    revision = OLD if stale else CURRENT
    summary = (
        "Retired draft proposed acknowledging and committing offsets before durable writes."
        if stale else "Synthetic project analysis with a captured source revision."
    )
    records = [
        {"path": path, "content": content, "source_revision": revision,
         "content_sha256": digest(content.encode())}
        for path, content in files.items()
        if not stale or path.startswith(("archive/", "legacy/"))
    ]
    if storage == "snapshot":
        # A plausible old record appears inside an otherwise current snapshot.
        records.append({"path": "archive/stale_snapshot.py", "content": "def clip_context_old(): pass",
                        "source_revision": OLD, "content_sha256": digest(b"def clip_context_old(): pass")})
    analysis = SimpleNamespace(
        id=1, workspace_id=WORKSPACE_ID, project_id=PROJECT_ID,
        source_revision=revision, generation=1 if stale else 2,
        status="SUCCESS", report_status="VALID", coverage_status="COMPLETE", snapshot={"files": records},
        report={"summary": summary, "architecture": [{"citations": [{"path": record["path"]} for record in records]}]},
    )
    return project, analysis


def bind_project(storage, root):
    binding = {f"{WORKSPACE_ID}:{PROJECT_ID}": str(root)} if storage.endswith("checkout") else {}
    return patch.dict(os.environ, {"STUDY_PROJECT_ROOTS": json.dumps(binding)})


async def run_route(chat, request_model, case, roots, projects):
    project_name = case.get("project")
    project_id = PROJECT_ID if project_name else None
    storage = case.get("storage", "checkout")
    project, analysis = project_rows(storage, projects[project_name]) if project_name else (None, None)
    root = roots / project_name if project_name else None

    request = request_model(request_id=case["id"], mode="auto", action=case.get("action", "explain"),
                            content=case["content"], project_id=project_id)
    prior = previous_messages(case, project_id)
    jd = SimpleNamespace(id=1, title="Synthetic role", raw_text=case["jd"],
                         created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)) if "jd" in case else None
    current = SimpleNamespace(
        id=3, role="user", content=request.content, action=request.action, project_id=project_id, status="completed",
        coach={"parent_message_id": prior[-1].id if prior else None}, sources=[],
    )
    sources = []
    if case.get("materials"):
        sources = [
            {"kind": "material", "id": index, "label": path, "path": path, "text": "Synthetic attachment",
             "provenance": "user_statement"}
            for index, path in enumerate(("src/a.ts", "src/b.ts"), start=1)
        ]
    reply = SimpleNamespace(
        id=4, session_id=1, role="assistant", action=request.action, project_id=project_id,
        sources=sources, status="generating",
        coach={"mode": request.mode, "parent_message_id": current.id, "trace": {"calls": []}},
    )
    rows = FixtureSession(project=project, analysis=analysis, jd=jd, reply=reply,
                          messages=[*prior, current], role=case["role"])
    observation = {}

    @asynccontextmanager
    async def fake_session():
        yield rows

    async def publish(_message_id, _attempt, **fields):
        trace = fields.get("coach", {}).get("trace", {})
        if trace.get("error_kind"):
            observation["error"] = trace["error_kind"]
        return True

    async def capture_generation(turn, _settings, _deadline, trace, _save, _fail):
        observation.update(
            predicted=trace.get("preferred_tier", trace.get("tier")), action=trace.get("action"),
            policy_version=trace.get("policy_version"), evidence_count=len(turn["sources"]), reached_generation=True,
        )

    with (bind_project(storage, root), patch.object(chat, "SessionLocal", fake_session),
          patch.object(chat, "publish", publish), patch.object(chat, "_generate", capture_generation)):
        await chat.generate_reply(reply.id, 1)
    if not observation.get("reached_generation"):
        observation.setdefault("error", "generation_boundary_not_reached")

    return {
        "id": case["id"], "stratum": case["stratum"], "role": case["role"],
        "expected": case["expected"], "predicted": None, **observation,
    }


def resolve_gold(case, files):
    gold = {}
    for item in case["relevant"]:
        path, anchor = item["path"], item["anchor"]
        hits = [line for line, text in enumerate(files[path].splitlines(), start=1) if anchor in text]
        if len(hits) != 1:
            raise ValueError(f"{case['id']}: evidence anchor must match one line")
        gold[path] = hits[0]
    return gold


async def run_retrieval(chat, evidence, case, roots, projects):
    files = projects[case["project"]]
    storage = case["storage"]
    project, analysis = project_rows(storage, files)
    rows = FixtureSession(project=project, analysis=analysis, role=case["role"])

    async def record_stage(_stage):
        pass

    with bind_project(storage, roots / case["project"]):
        if "prior_user" not in case:
            sources = await evidence.collect_project_evidence(rows, WORKSPACE_ID, [], PROJECT_ID, case["content"])
        else:
            row = await rows.get(SimpleNamespace(__name__="StudySession"), 1)
            prior = previous_messages(case, PROJECT_ID)
            turn = {
                "question": case["content"], "action": "project", "project_id": PROJECT_ID,
                "row": row, "reply": None, "history": [None, *reversed(prior)],
                "prior": prior[-1], "sources": [], "coach": {"mode": "auto"},
            }
            await chat._gather_sources(rows, turn, 30, record_stage)
            sources = turn["sources"]
    return score_retrieval(case, files, sources)


def excerpt_matches(text, expected):
    if not isinstance(text, str):
        return False
    actual = text.split("\n")
    if len(actual) != len(expected):
        return False
    return all(
        line == original or (
            "[REDACTED]" in line and original.startswith(line.split("[REDACTED]", 1)[0].rstrip('\"'))
        )
        for line, original in zip(actual, expected)
    )


def score_retrieval(case, files, sources, error=None):
    gold = resolve_gold(case, files)
    ranked = []
    for source in sources:
        path = source.get("path")
        provenance = source.get("provenance")
        if not path and provenance != "repository_observation":
            continue
        start, end = source.get("line_start"), source.get("line_end")
        anchor = gold.get(path)
        checkout = case["storage"].endswith("checkout")
        revision = "sha256:" + digest(files[path].encode()) if checkout and path in files else CURRENT
        span_ok = (
            isinstance(start, int) and isinstance(end, int) and path in files
            and 1 <= start <= end <= len(files[path].splitlines())
        )
        verified = (
            span_ok and provenance == "repository_observation"
            and source.get("revision") == revision and case["storage"] != "stale_snapshot"
            and str(source.get("id", "")).startswith(f"P{PROJECT_ID}:")
            and excerpt_matches(source.get("text"), files[path].splitlines()[start - 1:end])
        )
        hit = bool(
            verified and anchor is not None and start <= anchor <= end
            and source["text"].split("\n")[anchor - start] == files[path].splitlines()[anchor - 1]
        )
        ranked.append({
            "id": source.get("id"), "path": path, "line_start": start, "line_end": end,
            "kind": source.get("kind"), "verified": bool(verified), "hit": hit,
        })
    first = next((position for position, item in enumerate(ranked, start=1) if item["hit"]), None)
    hits = {
        str(k): len({item["path"] for item in ranked[:k] if item["hit"]}) for k in K_VALUES
    }
    return {
        "id": case["id"], "stratum": case["stratum"], "role": case["role"],
        "project": case["project"], "storage": case["storage"], "gold": gold,
        "relevant_count": len(gold), "ranked": ranked, "hits": hits,
        "first_hit_rank": first, "reciprocal_rank": 1 / first if first else 0,
        "abstained": not ranked and error is None, "error": error,
        "metadata": [{"kind": source.get("kind"), "id": source.get("id")} for source in sources
                     if not source.get("path") and source.get("provenance") != "repository_observation"],
    }


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def routing_metrics(rows):
    confusion = {tier: dict.fromkeys((*TIERS, "abstain_or_invalid", "error"), 0) for tier in TIERS}
    for row in rows:
        value = "error" if row.get("error") else row["predicted"]
        confusion[row["expected"]][value if value in (*TIERS, "error") else "abstain_or_invalid"] += 1
    classes = {}
    for tier in TIERS:
        tp = confusion[tier][tier]
        fp = sum(confusion[other][tier] for other in TIERS if other != tier)
        fn = sum(confusion[tier].values()) - tp
        classes[tier] = {"tp": tp, "fp": fp, "fn": fn, "support": tp + fn, "f1": ratio(2 * tp, 2 * tp + fp + fn)}
    correct = sum(classes[tier]["tp"] for tier in TIERS)
    scored_classes = [c["f1"] for c in classes.values() if c["f1"] is not None]
    return {
        "count": len(rows), "correct": correct, "accuracy": ratio(correct, len(rows)),
        "macro_f1": ratio(sum(scored_classes), len(scored_classes)),
        "confusion": confusion, "classes": classes,
        "errors": sum(c["error"] for c in confusion.values()),
        "abstain_or_invalid": sum(c["abstain_or_invalid"] for c in confusion.values()),
    }


def retrieval_metrics(rows):
    positives = [row for row in rows if row["relevant_count"]]
    negatives = [row for row in rows if not row["relevant_count"]]
    relevant_total = sum(row["relevant_count"] for row in positives)
    recall = {}
    for k in K_VALUES:
        hits = sum(row["hits"][str(k)] for row in positives)
        recall[str(k)] = {
            "macro": ratio(sum(row["hits"][str(k)] / row["relevant_count"] for row in positives), len(positives)),
            "pooled_hits": hits, "pooled_relevant": relevant_total, "pooled": ratio(hits, relevant_total),
        }
    rr = sum(row["reciprocal_rank"] for row in positives)
    rr8 = sum(row["reciprocal_rank"] for row in positives if row["first_hit_rank"] and row["first_hit_rank"] <= 8)
    negative_empty = sum(row["abstained"] for row in negatives)
    return {
        "count": len(rows), "positives": len(positives), "negatives": len(negatives),
        "recall_at": recall, "mrr": ratio(rr, len(positives)), "mrr_at_8": ratio(rr8, len(positives)),
        "reciprocal_rank_sum": rr,
        "positive_empty": sum(row["abstained"] for row in positives), "negative_empty": negative_empty,
        "negative_abstention": ratio(negative_empty, len(negatives)),
        "negative_answered": sum(bool(row["ranked"]) for row in negatives),
        "errors": sum(row["error"] is not None for row in rows),
        "unverified_ranked_items": sum(not item["verified"] for row in rows for item in row["ranked"]),
    }


def strata(rows, summarize):
    counts = sorted({row["stratum"] for row in rows})
    return {name: summarize([row for row in rows if row["stratum"] == name]) for name in counts}


async def run(fixture, roots):
    chat = import_module("app.services.study_chat")
    evidence = import_module("app.services.study_project_evidence")
    request_model = import_module("app.api.v1.study_chat").NewMessage
    routing = []
    for case in fixture["routing"]:
        routing.append(await run_route(chat, request_model, case, roots, fixture["projects"]))
    retrieval = []
    for case in fixture["retrieval"]:
        try:
            retrieval.append(await run_retrieval(chat, evidence, case, roots, fixture["projects"]))
        except Exception as exc:
            files = fixture["projects"][case["project"]]
            retrieval.append(score_retrieval(case, files, [], error=type(exc).__name__))
    return {
        "routing": {"metrics": routing_metrics(routing), "strata": strata(routing, routing_metrics), "cases": routing},
        "retrieval": {"metrics": retrieval_metrics(retrieval), "strata": strata(retrieval, retrieval_metrics), "cases": retrieval},
    }


def offline_environment():
    operating_system = {"systemroot", "windir", "path", "pathext", "temp", "tmp"}
    return {
        **{key: value for key, value in os.environ.items() if key.lower() in operating_system},
        "DATABASE_URL": "postgresql+asyncpg://offline@127.0.0.1:1/offline",
        "API_TOKEN": "offline-fixture", "STUDY_TIMEOUT_SECONDS": "30",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def reject_network(*_args, **_kwargs):
    raise RuntimeError("Offline benchmark does not allow connections")


async def run_without_network(fixture, roots):
    # Windows uses a local self-pipe when asyncio creates its event loop.
    loop = asyncio.get_running_loop()
    with (patch.object(socket.socket, "connect", reject_network),
          patch.object(socket.socket, "connect_ex", reject_network),
          patch.object(loop, "sock_connect", reject_network)):
        return await run(fixture, roots)


def verify_scorer():
    case = {"id": "control", "stratum": "control", "role": "control", "project": "control",
            "storage": "checkout", "relevant": [{"path": "a.py", "anchor": "first"},
                                                  {"path": "b.py", "anchor": "second"}]}
    files = {"a.py": "first\nother\n", "b.py": "second\n", "distractor.py": "irrelevant\n"}

    def source(path, start=1, end=1, **overrides):
        return {
            "id": f"P{PROJECT_ID}:control:{path}:{start}", "path": path, "kind": "project_code",
            "text": "\n".join(files[path].splitlines()[start - 1:end]), "provenance": "repository_observation",
            "revision": "sha256:" + digest(files[path].encode()), "line_start": start, "line_end": end,
            **overrides,
        }

    ranked = [
        source("a.py", revision="stale"), source("distractor.py"), source("a.py", 2, 2),
        source("a.py"), source("a.py"), source("b.py"),
    ]
    result = score_retrieval(case, files, ranked)
    if result["hits"] != {"1": 0, "3": 0, "5": 1, "8": 2} or result["reciprocal_rank"] != 0.25:
        raise AssertionError("Ranked controls must reject stale, irrelevant, wrong-line and repeated excerpts")
    if score_retrieval(case, files, [source("a.py", text="[REDACTED]")])["hits"]["1"] != 0:
        raise AssertionError("A redacted gold line must earn no evidence credit")
    if score_retrieval(case, files, [source("a.py", provenance="repository_documentation")])["hits"]["1"] != 0:
        raise AssertionError("Documentation must earn no code evidence credit")
    if not score_retrieval(case, files, [])["abstained"] or score_retrieval(
        case, files, [], error="RuntimeError",
    )["abstained"]:
        raise AssertionError("Retrieval errors must remain distinct from abstentions")
    route = routing_metrics([
        {"expected": "flash", "predicted": "flash"}, {"expected": "strong", "predicted": "flash"},
        {"expected": "strong", "predicted": None}, {"expected": "strong", "predicted": None, "error": "control"},
    ])
    if (route["correct"], route["macro_f1"], route["abstain_or_invalid"], route["errors"]) != (1, 1 / 3, 1, 1):
        raise AssertionError("Routing controls must count swaps, abstentions and errors in the denominators")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=FIXTURE_SUITES, default="primary")
    parser.add_argument("--fixtures", type=Path, help="Override the location of the selected frozen fixture")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline", metavar="REF", help="Archive the frozen dev commit instead of the working copy")
    parser.add_argument("--expect-policy-version", help="Require this version in every production routing trace")
    parser.add_argument("--expect-router-sha256", help="Require this digest for the captured router source")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    verify_scorer()
    fixture_name, fixture_sha256 = FIXTURE_SUITES[args.suite]
    fixture_path = args.fixtures or Path(__file__).with_name(fixture_name)
    fixture = read_fixture(fixture_path.resolve(), fixture_sha256)
    output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix="auto-offline-", dir=output.parent) as directory:
        runtime = Path(directory)
        code = runtime / "source"
        identity = materialize_source(args.repo.resolve(), code, args.baseline)
        if args.expect_router_sha256 and identity["router_sha256"] != args.expect_router_sha256:
            raise ValueError("Captured router bytes differ from the expected frozen digest")
        sys.path.insert(0, str(code / "server"))
        roots = runtime / "projects"
        for project, files in fixture["projects"].items():
            for name, body in files.items():
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Fixture paths must remain inside the synthetic checkout")
                path = roots / project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body.encode())

        sys.dont_write_bytecode = True
        with chdir(runtime), patch.dict(os.environ, offline_environment(), clear=True):
            result = asyncio.run(run_without_network(fixture, roots))

    route_observations = result["routing"]["cases"]
    traced_versions = Counter(row.get("policy_version") or "unversioned" for row in route_observations)
    expected_version = args.expect_policy_version
    count = len(route_observations)
    policy_matches = not expected_version or bool(count) and traced_versions[expected_version] == count
    result["routing"]["policy_identity"] = {
        "expected_version": expected_version, "trace_versions": dict(sorted(traced_versions.items())),
        "matches_expectation": policy_matches if expected_version else None,
    }

    report = {
        "suite": args.suite, "fixture_sha256": fixture_sha256, "runner_sha256": digest(Path(__file__).read_bytes()),
        "python_version": sys.version.split()[0], "source": identity, **result,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for section in ("routing", "retrieval"):
        print(section, json.dumps(result[section]["metrics"], ensure_ascii=True))
    print("routing_policy", json.dumps(result["routing"]["policy_identity"]))
    return int(not policy_matches or any(result[section]["metrics"]["errors"] for section in ("routing", "retrieval")))


if __name__ == "__main__":
    raise SystemExit(main())
