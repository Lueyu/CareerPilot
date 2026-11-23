"""Read-only tutoring evidence from one workspace's checkout or recorded snapshot."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import lazyload

from app.config import get_settings
from app.models.project import Project
from app.models.project_analysis import ProjectAnalysis
from app.models.skill import Skill
from app.services.project_context import PROFILE_LABELS, project_profile
from app.services.project_sources import is_safe_source_path, redact_source
from app.services.study_evidence_search import Search, term_hit as term_hit

MAX_ENTRIES = 8000
MAX_FILE_BYTES = 192_000
MAX_TOTAL_BYTES = 12_000_000
MAX_EXCERPTS = 8
MAX_EXCERPT_BYTES = 2400
MAX_SNAPSHOT_RECORDS = 1000
MAX_SNAPSHOT_FILES = 256
_DEFINITION = re.compile(
    r"^\s*(?:(?:(?:export|default|public|private|static|pub|async)\s+)*"
    r"(?:def|class|function|fn|func|struct|interface|type|const)\s+)([a-zA-Z_]\w*)"
)
_CALL = re.compile(r"\b([a-zA-Z_]\w*)\s*\(")
_TEST = re.compile(r"(^|[/_.-])(?:tests?|specs?|__tests__)(?=[/_.-]|$)", re.I)
_REVISION = re.compile(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}")
_SENSITIVE_PART = re.compile(r"(^|[._-])(secrets?|credentials?|tokens?|passwords?|private[._-]?keys?)([._-]|$)", re.I)
_EXTRA_SECRET = re.compile(
    r"(?i)\b(?:Bearer\s+[a-z0-9._~+/=-]{12,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"eyJ[a-z0-9_-]+\.eyJ[a-z0-9_-]+\.[a-z0-9_-]+)\b"
)
# A superset of the input markers used by redact_source and _EXTRA_SECRET.
_SECRET_HINT = re.compile(
    r"://|-----BEGIN|api[_-]?key|access[_-]?key|client[_-]?secret|secret[_-]?key|"
    r"password|passwd|token|authorization|sk-|gh[pousr]_|github_pat_|akia|bearer\s|xox[baprs]-|eyj", re.I
)
_LONG_TOKEN = re.compile(r"[\w-]{256,}")
_BINARY_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTILINE_CREDENTIAL = re.compile(
    r"""(?:api[_-]?key|access[_-]?key|client[_-]?secret|secret[_-]?key|password|passwd|token|authorization)"""
    r"""[\w-]*["']?[^\S\n]*(?::[^=\n]{0,30})?[:=]\s*(?:[\[({][^\S\n]*(?:#[^\n]*)?\n|["']{3})""",
    re.I,
)


def _redact(text):
    return _EXTRA_SECRET.sub("[REDACTED]", redact_source(text))


def _source_lines(text, limits):
    if _BINARY_CONTROL.search(text) or "-----BEGIN" in text:
        limits.add("存在二进制或敏感源码")
        return []
    # Keep physical line positions even when a minified or oversized line is omitted.
    lines = text.splitlines()
    kept = [line if len(line.encode("utf-8")) < MAX_EXCERPT_BYTES else "" for line in lines]
    oversized = kept != lines
    if oversized:
        limits.add("省略超长源码行")
    if _SECRET_HINT.search(text):
        # Redact across physical lines. Omitting a long assignment key first
        # could leave its value exposed on a following line.
        joined = "\n".join(kept)
        if oversized or _LONG_TOKEN.search(text) or _MULTILINE_CREDENTIAL.search(joined):
            limits.add("存在无法安全脱敏的源码")
            return []
        return _redact(joined).split("\n")
    return kept


def _source(kind, identifier, label, text, provenance, *, revision=None, path=None,
            line_start=None, line_end=None, symbol=None):
    return dict(kind=kind, id=identifier, label=label, text=text, provenance=provenance,
                revision=revision, path=path, line_start=line_start, line_end=line_end, symbol=symbol)


def _gap(project_id, key, text, revision=None):
    return _source("scope_gap", f"P{project_id}:{key}", "Project evidence scope", text,
                   "scope_limitation", revision=revision)


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _allowed_path(path):
    return is_safe_source_path(path) and not any(
        part.startswith((".", "pytest-of-", "pytest-")) or part in {"output", "coverage", "acceptance"}
        or part.endswith((" ", "."))
        for part in path.casefold().split("/")
    ) and _redact(path) == path and not any(
        part.startswith(".env") or _SENSITIVE_PART.search(part) for part in path.casefold().split("/")
    )


def _checkout(workspace_id, project_id):
    try:
        raw_config = os.environ.get("STUDY_PROJECT_ROOTS")
        roots = json.loads(raw_config) if raw_config is not None else get_settings().study_project_roots
        raw = roots.get(f"{workspace_id}:{project_id}") if isinstance(roots, dict) else None
        if not isinstance(raw, str) or not raw or raw.startswith(("//", "\\\\")):
            return None
        root = Path(raw)
        if (not root.is_absolute() or ".." in root.parts or root == root.parent
                or any(":" in part for part in root.parts[1:])):
            return None
        if os.name == "nt":
            from ctypes import windll

            if windll.kernel32.GetDriveTypeW(str(root.anchor)) not in (2, 3, 6):
                return None
        for part in (*reversed(root.parents), root):
            info = part.lstat()
            if _linked(info) or not stat.S_ISDIR(info.st_mode):
                return None
        return root
    except (OSError, ValueError, RecursionError):
        return None


@contextmanager
def _open_directory(path):
    if os.name == "nt":
        if any(_linked(parent.lstat()) for parent in (path, *path.parents)):
            raise OSError("linked checkout directory")
        yield path
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def _windows_file_matches(stream, path):
    from ctypes import create_unicode_buffer, windll
    from ctypes.wintypes import HANDLE
    from msvcrt import get_osfhandle

    buffer = create_unicode_buffer(32768)
    count = windll.kernel32.GetFinalPathNameByHandleW(
        HANDLE(get_osfhandle(stream.fileno())), buffer, len(buffer), 0,
    )
    return 0 < count < len(buffer) and Path(buffer.value.removeprefix("\\\\?\\")) == path


def _read_file(path, directory_handle, info, limit):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = (os.open(path, flags) if os.name == "nt"
                  else os.open(path.name, flags, dir_fd=directory_handle))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not os.path.samestat(info, opened)):
            return None
        if os.name == "nt" and not _windows_file_matches(stream, path):
            return None
        raw = stream.read(limit + 1)
        ended = os.fstat(stream.fileno())
    if len(raw) > limit or (opened.st_size, opened.st_mtime_ns) != (ended.st_size, ended.st_mtime_ns):
        return None
    return raw


def _read_checkout(root, search, hints):
    # Enumerate paths, then open only a bounded question-specific working set.
    files, candidates, limitations = {}, [], set()
    entries = total = 0
    pending = deque([root])
    while pending and entries < MAX_ENTRIES:
        directory = pending.popleft()
        try:
            with _open_directory(directory) as handle, os.scandir(handle) as children:
                for child in children:
                    entries += 1
                    if entries >= MAX_ENTRIES:
                        limitations.add("directory entry limit")
                        break
                    path = directory / child.name
                    relative = path.relative_to(root).as_posix()
                    if not (_allowed_path(relative) or _allowed_path(relative + "/source.py")):
                        continue
                    info = child.stat(follow_symlinks=False)
                    if _linked(info):
                        limitations.add("linked paths")
                    elif stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                    elif (_allowed_path(relative) and stat.S_ISREG(info.st_mode)
                          and (not search.paths or search.named_path(relative))):
                        candidates.append((-search.path_rank(relative), relative not in hints, relative, path))
        except OSError:
            limitations.add("unreadable checkout entries")
    candidates.sort()
    if len(candidates) > 100:
        limitations.add("file shortlist limited to 100 paths")
    if pending:
        limitations.add("directory entry limit")
    for _, _, relative, path in candidates[:100]:
        try:
            info = path.lstat()
            limit = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total)
            if _linked(info) or info.st_size > limit or info.st_nlink != 1:
                limitations.add("oversized or linked files")
                continue
            with _open_directory(path.parent) as handle:
                raw = _read_file(path, handle, info, limit)
            if raw is None:
                limitations.add("unreadable or changing files")
                continue
            total += len(raw)
            value = raw.decode("utf-8")
            if not search.might_match(value):
                continue
            lines = _source_lines(value, limitations)
            if lines:
                files[relative] = (f"sha256:{hashlib.sha256(raw).hexdigest()}", lines)
        except (OSError, UnicodeError):
            limitations.add("unreadable source files")
    return files, limitations


@dataclass(frozen=True)
class Match:
    score: int
    index: int
    role: str
    symbol: str | None


def _file_matches(path, lines, search):
    folded = path.casefold()
    name = folded.rsplit("/", 1)[-1]
    documentation = name.endswith((".md", ".rst")) or name == "readme" or folded.startswith(("docs/", "portal/content/"))
    test = bool(_TEST.search(path))
    best = {}
    for index, line in enumerate(lines):
        score = search.score(line)
        if not score:
            continue
        definition = _DEFINITION.match(line)
        symbol = definition[1] if definition else None
        if documentation:
            role, symbol = "documentation", None
        elif test:
            role = "test"
        elif symbol:
            role, score = "definition", score + (7 if search.score(symbol) else 2)
        else:
            symbol = next((name for name in _CALL.findall(line) if search.score(name)), None)
            role = "caller" if symbol and not definition else "reference"
            score += 4 * (role == "caller")
        current = best.get(role)
        if current is None or score > current.score:
            if len(line.encode("utf-8")) < MAX_EXCERPT_BYTES:
                best[role] = Match(score, index, role, symbol)
    if not best and search.named_path(path) and not search.has_terms():
        first = next((index for index, line in enumerate(lines)
                      if line.strip() and len(line.encode("utf-8")) < MAX_EXCERPT_BYTES), None)
        if first is not None:
            role = "documentation" if documentation else "test" if test else "reference"
            return [Match(1, first, role, None)]
    return list(best.values())


def _window(lines, match):
    index = match.index
    start = index if match.role == "definition" else max(0, index - 3)
    size = len(lines[index].encode("utf-8"))
    while start < index:
        before = sum(len(line.encode("utf-8")) + 1 for line in lines[start:index])
        if size + before <= MAX_EXCERPT_BYTES:
            size += before
            break
        start += 1
    end = index + 1
    for line in lines[end:index + (24 if match.role == "definition" else 9)]:
        size += len(line.encode("utf-8")) + 1
        if size > MAX_EXCERPT_BYTES:
            break
        end += 1
    return start, end


def _select_sources(project_id, files, search, analysis_id=None):
    candidates = []
    for path, (revision, lines) in files.items():
        path_rank = min(10, search.path_rank(path))
        for match in _file_matches(path, lines, search):
            start, end = _window(lines, match)
            candidates.append((-(match.score + path_rank), path, start, end, match, revision, lines))
    candidates.sort(key=lambda row: row[:4])
    distinct = []
    distinct_windows = {}
    for candidate in candidates:
        _, path, start, end, *_ = candidate
        windows = distinct_windows.setdefault(path, [])
        if any(start < prior_end and prior_start < end for prior_start, prior_end in windows):
            continue
        distinct.append(candidate)
        windows.append((start, end))
    selected = distinct[:1]
    for role in ("definition", "caller", "test"):
        support = next((row for row in distinct if row[4].role == role and row[0] <= distinct[0][0] + 14), None)
        if support is not None and support not in selected:
            selected.append(support)
    selected.extend(row for row in distinct[:MAX_EXCERPTS] if row not in selected)
    sources = []
    for _, path, start, end, match, revision, lines in selected[:MAX_EXCERPTS]:
        namespace = f"A{analysis_id}" if analysis_id is not None else revision.removeprefix("sha256:")
        kind = {"test": "project_test", "caller": "project_caller",
                "documentation": "project_documentation"}.get(match.role, "project_code")
        provenance = "repository_documentation" if match.role == "documentation" else "repository_observation"
        sources.append(_source(kind, f"P{project_id}:{namespace}:{path}:{start + 1}",
                               f"{path}:{start + 1}-{end}", "\n".join(lines[start:end]), provenance,
                               revision=revision, path=path, line_start=start + 1, line_end=end,
                               symbol=match.symbol))
    return sources, len(distinct) > MAX_EXCERPTS


def _search_gaps(project_id, sources, limits, scope, revision=None):
    gaps = []
    has_code = any(item["provenance"] == "repository_observation" for item in sources)
    if not has_code:
        gaps.append(_gap(project_id, "no-matches", f"{scope}中未找到足以支持本题的源码摘录。", revision))
    if limits:
        gaps.append(_gap(project_id, "search-limits", scope + "检索范围有限（" + "、".join(sorted(limits)) + "）。",
                         revision))
    if has_code:
        for kind, label in (("project_test", "测试"), ("project_caller", "上游调用")):
            if not any(item["kind"] == kind for item in sources):
                gaps.append(_gap(project_id, kind, f"{scope}未找到与本题匹配的{label}摘录。", revision))
        gaps.append(_gap(project_id, "static-search", f"{scope}仅作静态检索，间接调用和运行结果未验证。", revision))
    return gaps


def _local_sources(root, project_id, search, hints):
    if not search:
        return [_gap(project_id, "no-search-terms", "提问缺少可用于定位源码的术语、标识符或路径。")]
    files, limits = _read_checkout(root, search, hints)
    sources, exceeds_limit = _select_sources(project_id, files, search)
    if exceeds_limit:
        limits.add("达到摘录上限")
    return sources + _search_gaps(project_id, sources, limits, "绑定目录")


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _revision(value):
    return value.lower() if isinstance(value, str) and _REVISION.fullmatch(value) else None


def _snapshot_matches(analysis, project, snapshot, revision):
    versions = (snapshot.get("source_revision"), getattr(analysis, "target_sha", None))
    provenance = _mapping(snapshot.get("provenance"))
    return (all(value is None or (revision is not None and _revision(value) == revision) for value in versions)
            and _same_project(provenance, project))


def _same_project(record, project):
    expected = {"workspace_id": getattr(project, "workspace_id", None), "project_id": project.id}
    return all(key not in record or (type(record[key]) is int and record[key] == identifier)
               for key, identifier in expected.items())


def _analysis_context(project, analysis, has_checkout, search):
    if analysis is None:
        return [_gap(project.id, "no-analysis", "没有已完成并保存的项目分析。")], set()
    revision = _revision(analysis.source_revision)
    baseline = getattr(project, "last_seen_sha", None)
    stale = analysis.generation != project.generation or (
        baseline and revision and _revision(baseline) != revision
    )
    sources = []
    if stale:
        sources.append(_gap(project.id, "stale-analysis", "Stored analysis is stale relative to the recorded project version.",
                            revision))
    elif not revision or not baseline:
        sources.append(_gap(project.id, "unknown-analysis-version", "无法核实已保存分析与当前仓库版本的一致性。", revision))
    if has_checkout and not stale and revision:
        sources.append(_gap(project.id, "checkout-version", "已保存分析使用仓库提交号；绑定目录使用文件哈希，两者的一致性未验证。",
                            revision))

    report = _mapping(analysis.report)
    snapshot = _mapping(analysis.snapshot)
    if not _snapshot_matches(analysis, project, snapshot, revision):
        sources.append(_gap(project.id, "analysis-mismatch", "已保存分析的来源版本或项目身份不一致。", revision))
        return sources, set()
    coverage = _mapping(snapshot.get("coverage")) or _mapping(report.get("coverage"))
    if analysis.coverage_status != "COMPLETE" or coverage.get("tree_truncated"):
        sources.append(_gap(project.id, "analysis-coverage", "已保存分析的覆盖范围不完整或未经核实。", revision))
    missing = []
    for field in ("unread_files", "truncated_files", "failed_files", "unread_paths",
                  "truncated_paths", "failed_paths", "deferred_paths"):
        values = coverage.get(field)
        if isinstance(values, list) and values:
            paths = [path for path in values[:20] if _allowed_path(path)][:3]
            detail = f" ({', '.join(paths)})" if paths else ""
            missing.append(f"{len(values)} {field.replace('_', ' ')}{detail}")
    if missing:
        sources.append(_gap(project.id, "analysis-omissions", "Stored analysis lists " + " · ".join(missing) + ".", revision))

    hints = set()
    for field in ("architecture", "issues"):
        entries = report.get(field)
        for entry in entries[:20] if isinstance(entries, list) else []:
            citations = _mapping(entry).get("citations")
            for cite in citations[:10] if isinstance(citations, list) else []:
                path = _mapping(cite).get("path")
                if _allowed_path(path):
                    hints.add(path)
    unknowns = report.get("unknowns")
    if isinstance(unknowns, list):
        notes = [_redact(note[:500]) for note in unknowns[:4] if isinstance(note, str) and note[:500].strip()]
        if notes:
            sources.append(_gap(project.id, "analysis-unknowns", "已保存分析的限制：\n" + "\n".join(notes), revision))
    summary = report.get("summary")
    if revision and not stale and isinstance(summary, str) and search.score(summary[:1200]):
        sources.append(_source("project_analysis", analysis.id, "已保存的项目分析",
                               _redact(summary[:1200]), "project_report", revision=revision))
    return sources, hints if not stale and revision else set()


def _snapshot_files(analysis, project, search):
    limits = set()
    if not analysis or analysis.generation != project.generation:
        return {}, limits
    revision = _revision(analysis.source_revision)
    baseline = getattr(project, "last_seen_sha", None)
    if not revision or (baseline and _revision(baseline) != revision):
        return {}, limits
    snapshot = _mapping(analysis.snapshot)
    if not _snapshot_matches(analysis, project, snapshot, revision):
        return {}, {"快照与分析版本或项目身份不一致"}
    records = snapshot.get("files")
    if not isinstance(records, list):
        return {}, limits
    registry = snapshot.get("source_registry")
    legacy = (
        snapshot.get("schema_version") == 1 and _revision(snapshot.get("source_revision")) == revision
        and getattr(analysis, "pipeline_version", "legacy") == "legacy" and registry is None
    )

    chosen = []
    for record in records[:MAX_SNAPSHOT_RECORDS]:
        record = _mapping(record)
        path = record.get("path")
        if _allowed_path(path) and (not search.paths or search.named_path(path)):
            chosen.append((-search.path_rank(path), path, record))
    if len(records) > MAX_SNAPSHOT_RECORDS or len(chosen) > MAX_SNAPSHOT_FILES:
        limits.add("达到文件候选上限")
    counts = Counter(path for _, path, _ in chosen)

    files = {}
    remaining = MAX_TOTAL_BYTES
    for _, path, record in sorted(chosen, key=lambda item: item[:2])[:MAX_SNAPSHOT_FILES]:
        expected_hash = record.get("content_sha256")
        record_revision = _revision(record.get("source_revision"))
        if legacy and "source_revision" not in record and "content_sha256" not in record:
            expected_hash, record_revision = record.get("sha256"), revision
        value = record.get("content")
        if (counts[path] != 1 or record.get("truncated", False) is not False or record_revision != revision
                or not isinstance(expected_hash, str) or len(expected_hash) != 64
                or not isinstance(value, str) or len(value) > MAX_FILE_BYTES
                or not _same_project(record, project)):
            limits.add("存在未完整保存或不可验证的记录")
            continue
        try:
            raw = value.encode("utf-8")
        except UnicodeError:
            limits.add("存在无效文本记录")
            continue
        if len(raw) > min(MAX_FILE_BYTES, remaining):
            limits.add("达到源码字节上限")
            continue
        remaining -= len(raw)
        source_id = record.get("source_id")
        if (registry is not None and (
                not isinstance(registry, dict) or not isinstance(source_id, str)
                or len(source_id) > 128 or registry.get(source_id) != record)):
            limits.add("快照记录与来源登记不一致")
            continue
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            limits.add("存在内容哈希不匹配的记录")
            continue
        if not search.might_match(value):
            continue
        lines = _source_lines(value, limits)
        if lines:
            files[path] = (revision, lines)
    return files, limits


def snapshot_excerpts(analysis, project, terms):
    """Return revision-pinned excerpts from the existing direct search entry point."""
    search = Search.from_question(" ".join(terms))
    if not search:
        return []
    files, _ = _snapshot_files(analysis, project, search)
    return _select_sources(project.id, files, search, analysis.id)[0] if files else []


async def collect_project_evidence(session, workspace_id, skill_ids, project_id, question):
    """Collect scoped, revision-labeled sources and coverage gaps for a project turn."""
    if project_id is None:
        return []
    project = await session.scalar(select(Project).options(lazyload("*")).where(
        Project.id == project_id, Project.workspace_id == workspace_id, Project.archived_at.is_(None),
    ))
    if project is None:
        raise HTTPException(404, "项目不存在")

    analysis_query = select(ProjectAnalysis).where(
        ProjectAnalysis.workspace_id == workspace_id, ProjectAnalysis.project_id == project_id,
        or_((ProjectAnalysis.status == "SUCCESS") & (ProjectAnalysis.pipeline_version == "legacy"),
            ProjectAnalysis.report_status.in_(("VALID", "PARTIAL"))),
    ).order_by((ProjectAnalysis.generation == project.generation).desc())
    if project.last_seen_sha:
        analysis_query = analysis_query.order_by(
            (func.lower(ProjectAnalysis.source_revision) == project.last_seen_sha.lower()).desc().nulls_last())
    if project.latest_valid_analysis_id is not None:
        analysis_query = analysis_query.order_by(
            (ProjectAnalysis.id == project.latest_valid_analysis_id).desc())
    analysis = await session.scalar(analysis_query.order_by(ProjectAnalysis.id.desc()).limit(1))

    skills = []
    if skill_ids:
        rows = await session.execute(select(Skill.key, Skill.name).where(Skill.id.in_(skill_ids[:32])))
        skills = [value for row in rows for value in row]
    search = Search.from_question(question or "", skills)
    root = await asyncio.to_thread(_checkout, workspace_id, project_id)
    context, hints = _analysis_context(project, analysis, root is not None, search)

    if root is not None:
        records = await asyncio.to_thread(_local_sources, root, project_id, search, hints)
    elif search:
        records = await asyncio.to_thread(_snapshot_sources, project, analysis, search)
    else:
        records = [_gap(project_id, "no-search-terms", "提问缺少可用于定位源码的术语、标识符或路径。")]
    if root is None:
        scope = "已保存分析的固定版本"
        records.append(_gap(project_id, "no-checkout", f"未绑定本地目录。本次检索范围为{scope}，未读取仓库最新改动。"))

    # Group the profile so a full set of fields uses one material slot. It remains
    # a user statement even alongside source code and reports.
    fields = [f"{PROFILE_LABELS[key]}：{_redact(value[:750])[:250]}"
              for key, value in project_profile(project).items() if value]
    profile = [_source("project_profile", project.id, "项目资料", "\n".join(fields), "user_statement")] if fields else []
    return records + profile + context


def _snapshot_sources(project, analysis, search):
    files, limits = _snapshot_files(analysis, project, search)
    sources, exceeds_limit = _select_sources(project.id, files, search, analysis.id) if files else ([], False)
    if exceeds_limit:
        limits.add("达到摘录上限")
    revision = _revision(analysis.source_revision) if analysis is not None else None
    return sources + _search_gaps(project.id, sources, limits, "已保存快照", revision)
