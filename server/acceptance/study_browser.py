"""Run with the app's server/.venv Python. Each run retains a fresh PostgreSQL test DB."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import traceback
import time
from uuid import uuid4

# This option confines the Windows platform fallback to acceptance processes.
if sys.platform == "win32" and "--windows-no-wmi" in sys.argv:
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    sys.modules["_wmi"] = None

if __name__ == "__main__" and "--acceptance-child" in sys.argv:
    import runpy
    index = sys.argv.index("--acceptance-child")
    module = sys.argv[index + 1]
    sys.path.insert(0, os.getcwd())
    sys.argv = [module, *sys.argv[index + 2:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    raise SystemExit

import asyncpg
import httpx
from sqlalchemy.engine import make_url

ROOT = Path(os.environ.get("STUDY_APP_ROOT", Path(__file__).resolve().parents[2])).resolve()
SERVER = ROOT / "server"
WORKSPACE = "study-browser-acceptance"
SCENARIOS = (("售后工单", 8, 4, 10), ("医疗预约手册", 6, 3, 12),
             ("设备维修知识库", 10, 2, 8), ("财务报销条款", 4, 2, 5))
REQUIREMENT = (
    "用人工标注的相关文档集合计算检索 precision = 命中相关数 / 返回数，"
    "recall = 命中相关数 / 全部相关数。"
)


async def provision(connection_url):
    from app.db import SessionLocal, engine
    from app.models import Base, Question, Skill, Workspace

    assert engine.url.database == connection_url.database and engine.url.database.endswith("_test")
    admin = await asyncpg.connect(connection_url.set(
        database="postgres", drivername="postgresql", query={}
    ).render_as_string(hide_password=False))
    try:
        await admin.execute(f'CREATE DATABASE "{connection_url.database}"')
    finally:
        await admin.close()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    migrations = sorted((SERVER / "migrations").glob("*.sql"))
    sqlconn = await asyncpg.connect(connection_url.set(
        drivername="postgresql", query={}
    ).render_as_string(hide_password=False))
    try:
        for file in migrations:
            await sqlconn.execute(file.read_text(encoding="utf-8"))
        for file in migrations:
            if file.name.startswith(("010", "011", "012")):
                await sqlconn.execute(file.read_text(encoding="utf-8"))
    finally:
        await sqlconn.close()
    async with SessionLocal() as session:
        workspace = Workspace(slug=WORKSPACE, name="Synthetic study browser", is_demo=False)
        skill = Skill(key="rag", name="RAG 检索与召回")
        session.add_all([workspace, skill])
        await session.flush()
        questions = [
            Question(
                workspace_id=workspace.id, skill_id=skill.id, short_code=f"STUDY-RAG-{index}",
                topic="rag", qtype="open", tags=["RAG", "召回", "precision", "recall"],
                keywords="RAG 召回 precision recall",
                title=f"在{scenario}的 RAG 检索评估中，返回 {returned} 条，其中 {hits} 条相关，标注共有 {total} 条相关。计算 precision@{returned} 和 recall@{returned}，并解释分母。",
                answer_html=f"<p>{REQUIREMENT}本题应得 {hits}/{returned} 和 {hits}/{total}。</p>",
                rubric={"max_points": 5, "points": [{"title": "计算并解释两项指标", "detail": f"{REQUIREMENT}本题应得 {hits}/{returned} 和 {hits}/{total}。"}]},
            )
            for index, (scenario, returned, hits, total) in enumerate(SCENARIOS, 1)
        ]
        session.add_all(questions)
        await session.commit()
        return workspace.id, skill.id, [question.id for question in questions]



QUERIES = {
    "messages": "select id, role, status, model, content, sources from study_messages order by id",
    "versions": "select id, base_version_id, author, model, title, body, conflict from study_note_versions order by id",
    "note_jobs": "select id, note_id, version_id, status, conflict from study_note_jobs order by id",
    "cards": "select id, version_id, status, knowledge_point from study_cards order by id",
    "card_events": "select id, card_id, kind from study_card_events order by id",
    "practices": "select id, card_id, skill_id, retest_exam_id from study_practices order by id",
    "activities": "select id, meta->'assistance_events' as assistance_events from learning_activities order by id",
    "exam_answers": "select q.exam_id, a.judge_version, a.points, a.max_points, a.feedback from exam_answers a join exam_questions q on q.id = a.exam_question_id order by a.id",
    "submissions": "select id, revision, parent_submission_id, payload, assistance, status from learning_submissions order by id",
    "runs": "select id, exam_id, mode, status, error_category, judge_version, model_fingerprint from assessment_scoring_runs order by id",
    "steps": "select run_id, status, attempt_count, error_category from assessment_scoring_steps order by id",
    "exam_questions": "select exam_id, question_id from exam_questions order by id",
    "evidence": "select id, workspace_id, skill_id, kind, source_ref, grade, meta from evidence order by id",
    "skill_states": "select workspace_id, skill_id, status, estimated_level, evidence_coverage from skill_states order by skill_id",
}


async def snapshot(url):
    conn = await asyncpg.connect(url.set(drivername="postgresql", query={}).render_as_string(hide_password=False))
    try:
        for name in ("json", "jsonb"):
            await conn.set_type_codec(name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
        return {name: [dict(row) for row in await conn.fetch(sql)] for name, sql in QUERIES.items()}
    finally:
        await conn.close()


def write_json(path, data, token, config):
    raw = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    for value in (token, config.get("api_key"), config.get("base_url")):
        if value:
            raw = raw.replace(value, "[redacted]")
    path.write_text(raw, encoding="utf-8")


def wait_http(url, process):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and process.poll() is None:
        try:
            if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.4)
    raise RuntimeError("local process did not become ready")


def verify(db, browser, question_ids, workspace_id, skill_id):
    assistants = [m for m in db["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 2 and all(m["status"] == "completed" and m["model"] and m["content"] for m in assistants)
    assert any(v["author"] == "USER" and "STUDY-ACCEPTANCE" in v["title"] for v in db["versions"])
    assert any(v["model"] and not v["conflict"] for v in db["versions"])
    practice, = db["practices"]
    original, revision = db["submissions"]
    assert original["revision"] == 1 and revision["parent_submission_id"] == original["id"]
    assert original["status"] == revision["status"] == "COMPLETED"
    assert not original["assistance"].get("hint_used") and revision["assistance"]["hint_used"]
    assert practice["card_id"] in {c["id"] for c in db["cards"] if c["status"] == "ACTIVE"}
    activity, = db["activities"]
    assert activity["id"] == practice["id"]
    assert [event["kind"] for event in activity["assistance_events"]] == ["HINT"]
    assert practice["skill_id"] == skill_id and practice["retest_exam_id"] == browser["retest"]["exam_id"]
    exam_id = practice["retest_exam_id"]
    question, = [q for q in db["exam_questions"] if q["exam_id"] == exam_id]
    assert question["question_id"] in question_ids
    run, = [r for r in db["runs"] if r["exam_id"] == exam_id]
    assert run["status"] == "COMPLETED" and run["model_fingerprint"]
    assert sorted(r["mode"] for r in db["runs"]) == ["OFFICIAL", "PRACTICE", "PRACTICE"]
    for assessed_exam_id in [r["exam_id"] for r in db["runs"]]:
        answer, = [a for a in db["exam_answers"] if a["exam_id"] == assessed_exam_id]
        assert answer["judge_version"] == "judge-v2" and answer["feedback"]["validation_status"] == "VALID"
    assert browser["before"]["evidence_ids"] == []
    assert browser["before"]["skill_state"]["evidence_coverage"] == 0
    assert browser["retest"]["model_fingerprint"] == run["model_fingerprint"]
    evidence, = db["evidence"]
    assert [evidence["id"]] == browser["retest"]["evidence_ids"]
    assert (evidence["kind"], evidence["workspace_id"], evidence["skill_id"]) == ("EXAM_ANSWER", workspace_id, skill_id)
    assert evidence["meta"]["exam_id"] == exam_id
    assert evidence["meta"]["question_id"] == question["question_id"]
    state, = db["skill_states"]
    assert state["evidence_coverage"] == 1 and state["skill_id"] == skill_id
    assert state["status"] == "UNKNOWN" and state["estimated_level"] is None
    assert browser["retest"]["skill_state"]["evidence_coverage"] == state["evidence_coverage"]
    assert browser["replays"] == ["message", "note-job", "cards", "practice", "original", "hint", "revision", "retest"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--windows-no-wmi", action="store_true", help="Use the Python platform fallback for this acceptance run only")
    args = parser.parse_args()
    os.chdir(SERVER)
    sys.path.insert(0, str(SERVER))
    from app.config import get_settings

    settings = get_settings()
    original_url = make_url(settings.database_url)
    config = {key: getattr(settings, "llm_" + key) for key in ("provider", "model", "api_key", "base_url")}
    assert all(config.values()), "configure the deployment model in the application environment"
    for marker in ("web/src/pages/StudyChat.tsx", "web/src/pages/StudyNotebook.tsx", "server/app/api/v1/study_practice.py"):
        assert (ROOT / marker).is_file(), f"study integration pending: {marker}"
    for port in (18860, 18861):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    if args.preflight:
        async def check():
            conn = await asyncpg.connect(original_url.set(
                database="postgres", drivername="postgresql", query={}
            ).render_as_string(hide_password=False))
            try:
                assert await conn.fetchval("select rolcreatedb or rolsuper from pg_roles where rolname=current_user")
            finally:
                await conn.close()
        asyncio.run(check())
        print(f"READY: PostgreSQL create permission; {config['provider']} / {config['model']}; study files and ports")
        return

    database = f"careerpilot_study_{uuid4().hex[:12]}_test"
    target = original_url.set(database=database)
    token = uuid4().hex
    preferred = Path(os.environ.get("STUDY_ACCEPTANCE_OUTPUT", ROOT / ".tmp/study-acceptance"))
    try:
        output = preferred / database
        output.mkdir(parents=True)
    except PermissionError:
        output = Path(tempfile.mkdtemp(prefix="study-acceptance-")) / database
        output.mkdir()
    print(f"Evidence: {output}", flush=True)
    env = {
        "DATABASE_URL": target.render_as_string(hide_password=False),
        "API_TOKEN": token, "DEFAULT_WORKSPACE_SLUG": WORKSPACE, "WORKSPACE_TOKEN_HASHES": "{}",
        "DB_ECHO": "false", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
    }
    if os.name == "nt":
        scratch = output / "tmp"
        scratch.mkdir(exist_ok=True)
        env.update(TEMP=str(scratch), TMP=str(scratch))
    os.environ.update(env)
    get_settings.cache_clear()
    processes = []
    manifest = {"database": database, "provider": config["provider"], "model": config["model"], "status": "FAILED"}
    try:
        workspace_id, skill_id, bank = asyncio.run(provision(target))
        manifest.update(workspace_id=workspace_id, skill_id=skill_id, bank_ids=bank)
        child_python = ([sys.executable, str(Path(__file__).resolve()), "--windows-no-wmi", "--acceptance-child"]
                        if args.windows_no_wmi and sys.platform == "win32" else [sys.executable, "-m"])
        commands = (
            ([*child_python, "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18861", "--no-access-log"], SERVER, {}),
            ([*child_python, "app.project_worker"], SERVER, {}),
            (["node", "node_modules/vite/bin/vite.js", "--host", "127.0.0.1", "--port", "18860", "--strictPort"], ROOT / "web",
             {"VITE_API_BASE": "http://127.0.0.1:18861/api/v1", "VITE_API_TOKEN": token, "VITE_WORKSPACE": WORKSPACE, "VITE_AUTH_MODE": "token"}),
        )
        for command, cwd, extra in commands:
            processes.append(subprocess.Popen(command, cwd=cwd, env={**os.environ, **extra},
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_http("http://127.0.0.1:18861/healthz", processes[0])
        wait_http("http://127.0.0.1:18860/", processes[2])
        assert processes[1].poll() is None, "worker exited"
        browser_temp = output / "browser-temp"
        browser_temp.mkdir()
        browser_env = {**os.environ, "TEMP": str(browser_temp), "TMP": str(browser_temp),
                       "STUDY_EVIDENCE": str(output), "STUDY_REDACT": json.dumps(
            [token, config["api_key"], config["base_url"]]
        )}
        run = subprocess.run(["node", str(Path(__file__).with_suffix(".cjs"))], env=browser_env, timeout=1800)
        assert run.returncode == 0, "browser run stopped; inspect browser.json"
        browser = json.loads((output / "browser.json").read_text(encoding="utf-8"))
        verify(asyncio.run(snapshot(target)), browser, bank, workspace_id, skill_id)
        manifest["status"] = "PASSED"
    except Exception as error:
        manifest["failure"] = type(error).__name__
        manifest["line"] = traceback.extract_tb(error.__traceback__)[-1].lineno
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        if "bank_ids" in manifest:
            write_json(output / "database.json", asyncio.run(snapshot(target)), token, config)
        write_json(output / "result.json", manifest, token, config)
    print((output / "result.json").read_text(encoding="utf-8"), flush=True)
    return int(manifest["status"] != "PASSED")


if __name__ == "__main__":
    sys.exit(main())
