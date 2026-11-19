"""Read-only project tutoring evidence from an explicitly bound local checkout."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import lazyload

from app.config import get_settings
from app.models.project import Project
from app.models.project_analysis import ProjectAnalysis
from app.models.skill import Skill
from app.services.project_sources import is_safe_source_path, redact_source

MAX_ENTRIES = 8000
MAX_FILE_BYTES = 192_000
MAX_TOTAL_BYTES = 12_000_000
MAX_EXCERPTS = 8
_WORD = re.compile(r"[\w]+", re.UNICODE)
_COMMON = set("a an and are can code could does explain for how in is it me of or project show the to what where which why with works would careerpilot".split())
_DEFINITION = re.compile(
    r"^\s*(?:(?:(?:export|default|public|private|static|pub|async)\s+)*"
    r"(?:def|class|function|fn|func|struct|interface|type|const)\s+)([a-zA-Z_]\w*)"
)
_CALL = re.compile(r"\b([a-zA-Z_]\w*)\s*\(")
_SENSITIVE_PART = re.compile(r"(^|[._-])(secrets?|credentials?|tokens?|passwords?|private[._-]?keys?)([._-]|$)", re.I)
_EXTRA_SECRET = re.compile(
    r"(?i)\b(?:Bearer\s+[a-z0-9._~+/=-]{12,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"eyJ[a-z0-9_-]+\.eyJ[a-z0-9_-]+\.[a-z0-9_-]+)\b"
)


def _redact(text):
    return _EXTRA_SECRET.sub("[REDACTED]", redact_source(text))


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


def _terms(question, skills):
    terms = dict.fromkeys(word.casefold() for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", question[:4000])
                          if len(word) > 1 and word.casefold() not in _COMMON)
    aliases = {"聊天": ["study_chat", "generate_reply"], "学习教练": ["study_chat","teaching"],
               "路由": ["study_routing","decide"], "模型选择":["study_routing", "decide", "profile"], "选择模型":["study_routing","decide","profile"], "怎么选":["decide"], "如何选择":["decide","profile"], "笔记":["study_notes"],
               "评分":["assessment", "judge"], "流式":["llm_stream"], "恢复":["recover"],
               "复测":["retest"], "出卷":["selection"], "队列":["worker"], "工具调用":["tool"],
               "画像":["skill_state"], "架构":["main", "router"], "卡片":["study_notes","card"]}
    for word, tokens in aliases.items():
        if word in question:
            terms.update(dict.fromkeys(tokens))
    for value in skills:
        terms.update(dict.fromkeys(word.casefold() for word in _WORD.findall(value or "")
                                   if len(word) > 1 and word.casefold() not in _COMMON))
    return list(terms)[:32]


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


def _read_checkout(root, terms, hints):
    # Enumerate paths, then open only a bounded question-specific working set.
    from collections import deque
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
                    elif _allowed_path(relative) and stat.S_ISREG(info.st_mode):
                        name = relative.casefold()
                        preferred = sum(4 if token in path.stem.casefold() else 1 for token in terms if token in name)
                        rank = 10 * preferred + 7 * (relative in hints) + 2 * name.startswith(("server/app/", "web/src/"))
                        candidates.append((-rank, relative, path))
        except OSError:
            limitations.add("unreadable checkout entries")
    candidates.sort()
    if len(candidates) > 100:
        limitations.add("file shortlist limited to 100 paths")
    for _, relative, path in candidates[:100]:
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
            if chr(0) not in value and "-----BEGIN" not in value:
                normalized = _redact(value.replace("\r\n", "\n").replace("\r", "\n"))
                files[relative] = (hashlib.sha256(raw).hexdigest(), normalized.removesuffix("\n").split("\n"))
        except (OSError, UnicodeError):
            limitations.add("unreadable source files")
    return files, limitations


def term_hit(term, value):
    # "RAG" must not match "coverage"; underscores delimit identifier words.
    if len(term) <= 4 and term.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", value) is not None
    return term in value


def _matches(path, lines, terms, hints):
    path_text = path.casefold()
    is_test = not path_text.endswith((".md",".rst")) and bool(re.search(r"(^|[/_.-])(?:tests?|specs?|__tests__)(?=[/_.-]|$)", path_text))
    best = None
    for index, line in enumerate(lines):
        value = line.casefold()
        hits = [term for term in terms if term_hit(term, value)]
        if not hits:
            continue
        definition = _DEFINITION.match(line)
        symbol = definition[1] if definition else None
        defines = symbol and any(term in symbol.casefold() for term in hits)
        calls = [name for name in _CALL.findall(line) if name.casefold() in hits and name != symbol]
        role = "test" if is_test else "definition" if defines else "caller" if calls else "reference"
        score = (5 * len(hits) + 7 * bool(defines) + 4 * bool(calls)
                 + 12 * (Path(path).stem.casefold() in terms)
                 + 12 * bool(symbol and symbol.casefold() in terms)
                 + sum(term_hit(term, path_text) for term in hits) + int(path in hints))
        if best is None or score > best[0]:
            best = score, index, role, symbol or (calls[0] if calls else None)
    return best


def _local_sources(root, project_id, terms, hints):
    if not terms:
        return [_gap(project_id, "no-search-terms", "The question and selected skills supplied no searchable terms.")]
    files, limits = _read_checkout(root, terms, hints)
    symbols = set()
    for _, lines in files.values():
        for line in lines:
            definition = _DEFINITION.match(line)
            if definition:
                name = definition[1].casefold()
                if any(term_hit(term, name) for term in terms):
                    symbols.add(name)
    terms = list(dict.fromkeys([*terms, *sorted(symbols)[:16]]))
    matches = []
    for path, (revision, lines) in files.items():
        if path.lower().endswith((".md", ".rst")) or path.startswith(("docs/","portal/content/")):
            continue
        match = _matches(path, lines, terms, hints)
        if match:
            score, index, role, symbol = match
            matches.append((-score, path, index, role, symbol, revision, lines))
    matches.sort()
    selected = []
    for preferred in ("definition", "test", "caller"):
        match = next((item for item in matches if item[3] == preferred), None)
        if match:
            selected.append(match)
    selected.extend(item for item in matches[:MAX_EXCERPTS] if item not in selected)
    # Include call sites in the selected implementation; a definition alone
    # may only show setup and imports. Prefer distinct excerpt windows.
    sites = []
    for item in selected:
        _, path, first, role, _, digest, lines = item
        if role != "definition" or len(sites) >= 3:
            continue
        for i, line in enumerate(lines):
            names = [name for name in _CALL.findall(line) if name.casefold() in terms]
            if names and not _DEFINITION.match(line) and abs(i-first)>9 and all(
                    prev[1] != path or abs(prev[2]-i)>9 for prev in sites):
                sites.append((0, path, i, "caller", names[0], digest, lines))
                if len(sites)>=3:
                    break
    selected = selected[:1] + sites + selected[1:]
    sources = []
    for _, path, index, role, symbol, digest, lines in selected[:MAX_EXCERPTS]:
        start = index if role == "definition" else max(0, index - 3)
        end = start
        size = 0
        for line in lines[start:min(len(lines), index + (32 if role == "definition" else 9))]:
            if size + len(line) > 3600:
                break
            size += len(line) + 1
            end += 1
        if end <= index:
            limits.add("oversized source lines")
            continue
        sources.append(_source(
            {"test": "project_test", "caller": "project_caller"}.get(role, "project_code"),
            f"P{project_id}:{digest}:{path}:{start + 1}", f"{path}:{start + 1}-{end}",
            "\n".join(lines[start:end]), "repository_observation", revision=f"sha256:{digest}",
            path=path, line_start=start + 1, line_end=end, symbol=symbol,
        ))
    if limits:
        sources.append(_gap(project_id, "search-limits", "Checkout search omitted " + ", ".join(sorted(limits)) + "."))
    if len(matches) > MAX_EXCERPTS:
        sources.append(_gap(project_id, "excerpt-limit", "Additional matching files were omitted by the excerpt limit."))
    if not any(item["provenance"] == "repository_observation" for item in sources):
        sources.append(_gap(project_id, "no-matches", "The bounded checkout search found no relevant code excerpts."))
    for kind, label in (("project_test", "tests"), ("project_caller", "upstream callers")):
        if not any(item["kind"] == kind for item in sources):
            sources.append(_gap(project_id, kind, f"The bounded checkout search found no relevant {label}."))
    sources.append(_gap(project_id, "static-search", "Checkout evidence comes from static text search. "
                        "Indirect callers may be missed and test execution is unverified."))
    return sources


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _analysis_context(project, analysis, has_checkout):
    if analysis is None:
        return [_gap(project.id, "no-analysis", "No completed project analysis is recorded.")], set()
    revision = analysis.source_revision
    if not isinstance(revision, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", revision):
        revision = None
    baseline = project.last_seen_sha
    stale = analysis.generation != project.generation or (
        baseline and revision and baseline.casefold() != revision.casefold()
    )
    sources = []
    if stale:
        sources.append(_gap(project.id, "stale-analysis", "Stored analysis is stale relative to the recorded project version.",
                            revision))
    elif not revision or not baseline:
        sources.append(_gap(project.id, "unknown-analysis-version", "Stored analysis freshness could not be verified.", revision))
    if has_checkout and not stale:
        sources.append(_gap(project.id, "checkout-version", "Stored analysis uses a repository revision. Checkout citations "
                            "use file content hashes, so equivalence to that revision is unverified.", revision))

    report = _mapping(analysis.report)
    snapshot = _mapping(analysis.snapshot)
    coverage = _mapping(snapshot.get("coverage")) or _mapping(report.get("coverage"))
    if analysis.coverage_status != "COMPLETE" or coverage.get("tree_truncated"):
        sources.append(_gap(project.id, "analysis-coverage", "Stored analysis coverage is partial or unverified.", revision))
    for field in ("unread_files", "truncated_files", "failed_files", "unread_paths",
                  "truncated_paths", "failed_paths", "deferred_paths"):
        values = coverage.get(field)
        if isinstance(values, list) and values:
            paths = [_redact(path) for path in values[:20] if _allowed_path(path)][:5]
            detail = f" ({', '.join(paths)})" if paths else ""
            sources.append(_gap(project.id, field, f"Stored analysis lists {len(values)} {field.replace('_', ' ')}"
                                f"{detail}.", revision))
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
        notes = [_redact(note[:4000])[:500] for note in unknowns[:8] if isinstance(note, str) and note.strip()]
        if notes:
            sources.append(_gap(project.id, "analysis-unknowns", "Stored analysis limitations:\n" + "\n".join(notes),
                                revision))
    if revision and not stale and isinstance(report.get("summary"), str) and report["summary"].strip():
        sources.append(_source("project_analysis", analysis.id, "Stored project analysis",
                               _redact(report["summary"][:4000]), "project_report", revision=revision))
    return sources, hints if not stale and revision else set()


def snapshot_excerpts(analysis, project, terms):
    """Read immutable, version-checked source records already acquired in this workspace."""
    if not analysis or analysis.generation != project.generation or not analysis.source_revision:
        return []
    if project.last_seen_sha and project.last_seen_sha != analysis.source_revision:
        return []
    candidates = []
    records = _mapping(analysis.snapshot).get("files", [])
    if not isinstance(records, list):
        return []
    for record in records[:1000]:
        record = _mapping(record)
        path = record.get("path")
        if not _allowed_path(path) or path.endswith((".md", ".rst")) or record.get("truncated"):
            continue
        if record.get("source_revision") != analysis.source_revision:
            continue
        matches = sum(t in path.casefold() for t in terms)
        candidates.append((-matches, path, record))
    selected = []
    for _, path, record in sorted(candidates, key=lambda item: item[:2])[:24]:
        value = record.get("content")
        if not isinstance(value, str) or len(value)>MAX_FILE_BYTES:
            continue
        if hashlib.sha256(value.encode()).hexdigest() != record.get("content_sha256"):
            continue
        lines = value.splitlines()
        match = _matches(path, lines, terms, ())
        if match:
            score, index, role, symbol = match
            first, end = max(0, index-2), min(len(lines),index+7)
            excerpt = _redact("\n".join(lines[first:end]))
            if len(excerpt)>3600:
                continue
            selected.append((-score, _source("project_test" if role=="test" else "project_code",
                f"P{project.id}:A{analysis.id}:{path}:{first+1}", f"{path}:{first+1}-{end}", excerpt,
                "repository_observation", revision=analysis.source_revision, path=path,
                line_start=first+1, line_end=end, symbol=symbol)))
    return [row for _, row in sorted(selected,key=lambda x:x[0])[:MAX_EXCERPTS]]


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
        or_(ProjectAnalysis.status == "SUCCESS",
            ProjectAnalysis.report_status.in_(("VALID", "PARTIAL"))),
    )
    analysis_query = analysis_query.order_by((ProjectAnalysis.generation == project.generation).desc())
    if project.last_seen_sha:
        analysis_query = analysis_query.order_by(
            (ProjectAnalysis.source_revision == project.last_seen_sha).desc().nulls_last())
    if project.latest_valid_analysis_id is not None:
        analysis_query = analysis_query.order_by(
            (ProjectAnalysis.id == project.latest_valid_analysis_id).desc())
    analysis = await session.scalar(analysis_query.order_by(ProjectAnalysis.id.desc()).limit(1))
    root = await asyncio.to_thread(_checkout, workspace_id, project_id)
    sources, hints = _analysis_context(project, analysis, root is not None)

    skills = []
    if skill_ids:
        rows = await session.execute(select(Skill.key, Skill.name).where(Skill.id.in_(skill_ids[:32])))
        skills = [value for row in rows for value in row]
    terms = _terms(question or "", skills)
    if root is None:
        records = await asyncio.to_thread(snapshot_excerpts, analysis, project, terms)
        return records + sources + [_gap(project_id, "no-checkout",
            "未绑定本地目录。本次仅能检索已有分析固定版本的源码快照，未读取工作区最新改动。")]
    return await asyncio.to_thread(_local_sources, root, project_id, terms, hints) + sources
