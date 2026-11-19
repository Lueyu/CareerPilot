"""Drain the recorded personal worker before a prompt-bundle upgrade (Windows)."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT / "server")
sys.path.insert(0, str(Path.cwd()))

from app.db import SessionLocal
from app.models import AppSetting, Project, ProjectAnalysis, WorkerHeartbeat
from app.services.project_jobs import ACTIVE, HANDOFF_KEY
from app.services.project_prompts import PROMPT_BUNDLE_VERSION

STATE = ROOT / "personal/runtime/worker.json"
TARGET = PROMPT_BUNDLE_VERSION


def worker_record():
    return json.loads(STATE.read_text(encoding="utf-8-sig")) if STATE.exists() else {}


def powershell(command, **env):
    # A Windows venv launcher can inherit pipe handles from PowerShell. A file
    # lets the parent finish immediately while the new worker keeps running.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            [shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=ROOT, env={**os.environ, **env}, stdout=output, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=45,
        )
        output.seek(0)
        message = output.read().decode(errors="replace").strip()
        if result.returncode:
            raise RuntimeError(message)
        return message


async def transfer(session, record):
    # The project/scoring claim lock and the note-job table lock close the
    # idle-observation race for each model-calling worker loop.
    await session.execute(text("SELECT pg_advisory_xact_lock(20261117, 0)"))
    await session.execute(text("LOCK TABLE study_note_jobs IN SHARE MODE"))
    await session.execute(text("LOCK TABLE project_analyses IN SHARE ROW EXCLUSIVE MODE"))
    active = await session.scalar(select(ProjectAnalysis.id).where(
        ProjectAnalysis.status.in_(ACTIVE),
        ProjectAnalysis.prompt_bundle_version != TARGET).limit(1))
    busy = await session.scalar(text("""
        SELECT EXISTS (SELECT FROM assessment_scoring_steps WHERE status='RUNNING')
            OR EXISTS (SELECT FROM study_note_jobs WHERE status='RUNNING')
            OR EXISTS (SELECT FROM project_analysis_steps WHERE status='RUNNING')
    """))
    if active or busy:
        return False
    # Verify the launcher's PID and creation time inside its lifecycle mutex.
    powershell("""
        $ErrorActionPreference='Stop'
        . ./deploy/personal-processes.ps1
        $expected = $env:HANDOFF_IDENTITY | ConvertFrom-Json
        Stop-CareerPilotWorker (Get-Location).Path $expected
    """, HANDOFF_IDENTITY=json.dumps(record))
    return True


async def promote(session):
    waiting = (await session.scalars(select(ProjectAnalysis).where(
        ProjectAnalysis.status == 'WAITING_WORKER', ProjectAnalysis.prompt_bundle_version == TARGET
    ).order_by(ProjectAnalysis.id).with_for_update())).all()
    for run in waiting:
        project = await session.get(Project, run.project_id, with_for_update=True)
        if project.archived_at:
            run.status = 'CANCELLED'
            continue
        project.generation += 1
        run.generation = project.generation
        run.base_sha = project.last_seen_sha
        run.status = 'QUEUED'
    return len(waiting)


async def main(args):
    async with SessionLocal() as db:
        if await db.scalar(text("SELECT current_database()")) != args.database:
            raise RuntimeError("Configured database does not match --database")
        flag = await db.get(AppSetting, HANDOFF_KEY)
        if args.arm and not flag:
            record = worker_record()
            if record.get("module") != "app.project_worker":
                raise RuntimeError("Cannot identify current worker")
            db.add(AppSetting(key=HANDOFF_KEY, value={
                "target_prompt": TARGET, "old_worker": record, "phase": "draining"}))
            await db.commit()
            print("Durable queue armed for", TARGET, flush=True)
    while True:
        async with SessionLocal() as db:
            # Manual scan requests take the global lock before reading the flag.
            await db.execute(text("SELECT pg_advisory_xact_lock(20261117, 0)"))
            flag = await db.get(AppSetting, HANDOFF_KEY, with_for_update=True)
            if not flag:
                return
            value = flag.value
            if value["target_prompt"] != TARGET:
                raise RuntimeError("Handoff targets another prompt bundle")
            if value["phase"] == "draining":
                if worker_record() != value["old_worker"]:
                    raise RuntimeError("Recorded worker identity changed; staged requests stay queued")
                if await transfer(db, value["old_worker"]):
                    flag.value = {**value, "phase": "starting"}
                    await db.commit()
                    print("Legacy queue drained; launching updated worker", flush=True)
                    continue
            else:
                if not worker_record():
                    powershell("$ErrorActionPreference='Stop'; & ./deploy/start-project-worker.ps1")
                # The heartbeat is written by the interpreter child. Verify its
                # parent against the lifecycle record before releasing queued work.
                seen = await db.scalar(select(WorkerHeartbeat).where(
                    WorkerHeartbeat.worker_id.like(f"%-{TARGET}-%"),
                    WorkerHeartbeat.last_seen_at > text("now() - interval '30 seconds'")
                ).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1))
                if seen:
                    record = worker_record()
                    parent = powershell("(Get-CimInstance Win32_Process -Filter ('ProcessId='+ $env:HANDOFF_PID)).ParentProcessId",
                                        HANDOFF_PID=str(seen.pid))
                    if record.get("pid") in (seen.pid, int(parent or 0)):
                        count = await promote(db)
                        await db.delete(flag)
                        await db.commit()
                        print(f"Worker {seen.pid} verified; {count} staged scans released", flush=True)
                        return
        await asyncio.sleep(15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--arm", action="store_true")
    options = parser.parse_args()
    asyncio.run(main(options))
