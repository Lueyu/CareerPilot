"""Real owned children, pipe stalls, Windows Jobs and cancellation recovery."""
import asyncio
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, replace

import pytest

from app.services import document_runner as runner
from app.services.document_policy import PERSONAL_POLICY, ErrorKind, ParseResult


def dead(pid):
    if sys.platform == "win32":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        api.OpenProcess.restype = ctypes.c_void_p
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            return True
        try:
            return api.WaitForSingleObject(handle, 0) == 0
        finally:
            api.CloseHandle(handle)
    try:
        from pathlib import Path
        if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
            return True
        os.kill(pid, 0)
        return False
    except (ProcessLookupError, FileNotFoundError):
        return True


def probe(monkeypatch, source):
    monkeypatch.setattr(runner, "_command", lambda *_: [sys.executable, "-c", source])


def tree_source(marker, *, finish=False):
    frame = json.dumps(asdict(ParseResult(text="complete", chars=8)))
    return ("import sys,os,time,subprocess,json\n"
            "sys.stdin.buffer.read(1)\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
            f"open({str(marker)!r},'w').write(json.dumps([os.getpid(),child.pid]))\n"
            + (f"sys.stdout.write({frame!r});sys.stdout.flush()\n" if finish else "time.sleep(60)\n"))


def assert_tree_dead(marker):
    pids = json.loads(marker.read_text())
    for _ in range(100):
        if all(dead(pid) for pid in pids):
            return
        time.sleep(0.01)
    assert all(dead(pid) for pid in pids), pids


def test_real_timeout_terminates_tree_and_recovers(monkeypatch, tmp_path):
    marker = tmp_path / "pids.json"
    original = runner._command
    probe(monkeypatch, tree_source(marker))
    result = runner.parse_document(".txt", b"x", replace(PERSONAL_POLICY, wall_seconds=1))
    assert result.kind == ErrorKind.TIMEOUT, result
    assert_tree_dead(marker)
    monkeypatch.setattr(runner, "_command", original)
    assert runner.parse_document(".txt", b"recovered", PERSONAL_POLICY).text == "recovered"


def test_normal_return_also_kills_descendants(monkeypatch, tmp_path):
    marker = tmp_path / "normal.json"
    probe(monkeypatch, tree_source(marker, finish=True))
    assert runner.parse_document(".txt", b"x", PERSONAL_POLICY).text == "complete"
    assert_tree_dead(marker)


@pytest.mark.asyncio
async def test_real_repeated_cancel_terminates_tree(monkeypatch, tmp_path):
    marker = tmp_path / "cancel.json"
    probe(monkeypatch, tree_source(marker))
    task = asyncio.create_task(runner.parse_document_async(".txt", b"x", PERSONAL_POLICY))
    for _ in range(300):
        if marker.exists():
            break
        await asyncio.sleep(0.01)
    assert marker.exists()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert_tree_dead(marker)


@pytest.mark.asyncio
async def test_cancel_during_spawn_still_owns_returned_process(monkeypatch):
    created = []
    started = threading.Event()
    original = runner.subprocess.Popen

    def delayed(*args, **kwargs):
        process = original(*args, **kwargs)
        created.append(process)
        started.set()
        time.sleep(0.2)
        return process

    monkeypatch.setattr(runner.subprocess, "Popen", delayed)
    task = asyncio.create_task(runner.parse_document_async(".txt", b"x", PERSONAL_POLICY))
    while not started.is_set():
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created and all(dead(process.pid) for process in created)


@pytest.mark.parametrize("source,kind", [
    ("import sys,time;sys.stdin.buffer.read(1);sys.stdout.write('x'*500000);sys.stdout.flush();time.sleep(60)", ErrorKind.PROTOCOL),
    ("import time;time.sleep(60)", ErrorKind.TIMEOUT),
    ("import sys,time;sys.stdin.buffer.read(1);sys.stdout.write('{');sys.stdout.flush();time.sleep(60)", ErrorKind.TIMEOUT),
    ("import sys;sys.stdin.buffer.read();sys.stderr.write('x'*1000000);sys.stdout.write('{}')", ErrorKind.PROTOCOL),
])
def test_bounded_output_and_blocked_input(monkeypatch, source, kind):
    probe(monkeypatch, source)
    result = runner.parse_document(".txt", b"x" * 1_000_000, replace(PERSONAL_POLICY, wall_seconds=1))
    assert result.kind == kind, result


@pytest.mark.asyncio
async def test_both_real_callers_cancel_through_shared_owner(monkeypatch, tmp_path):
    from app.api.public_library import _parse_document
    from app.services.text_extract import extract_many_async
    marker = tmp_path / "callers.json"
    probe(monkeypatch, tree_source(marker))
    for invoke in (lambda: _parse_document(".md", b"x"),
                   lambda: extract_many_async([("x.txt", b"x")])):
        marker.unlink(missing_ok=True)
        task = asyncio.create_task(invoke())
        for _ in range(300):
            if marker.exists():
                break
            await asyncio.sleep(0.01)
        assert marker.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_tree_dead(marker)


@pytest.mark.asyncio
async def test_shared_admission_and_cancelled_waiter(monkeypatch, tmp_path):
    from app.api.public_library import _parse_document
    from app.services.text_extract import extract_many_async
    created = []
    original = runner.subprocess.Popen
    probe(monkeypatch, "import sys,time;sys.stdin.buffer.read(1);time.sleep(60)")

    def observe(*args, **kwargs):
        process = original(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(runner.subprocess, "Popen", observe)
    tasks = [asyncio.create_task(_parse_document(".md", b"x")) for _ in range(2)]
    tasks.append(asyncio.create_task(extract_many_async([("x.txt", b"x")])))
    for _ in range(300):
        if len(created) == runner.MAX_CHILDREN:
            break
        await asyncio.sleep(0.01)
    assert len(created) == runner.MAX_CHILDREN
    waiter = asyncio.create_task(_parse_document(".md", b"waiting"))
    await asyncio.sleep(0.1)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert len(created) == runner.MAX_CHILDREN
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert all(dead(process.pid) for process in created)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows Job mechanism")
def test_windows_job_membership_memory_and_no_inherited_handle(monkeypatch):
    # The probe runs only after the parent's successful assignment/input barrier.
    source = '''import ctypes,sys,json
from ctypes import wintypes as w
sys.stdin.buffer.read(1)
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.GetCurrentProcess.restype=w.HANDLE
k.IsProcessInJob.argtypes=[w.HANDLE,w.HANDLE,ctypes.POINTER(w.BOOL)]
inside=w.BOOL()
assert k.IsProcessInJob(k.GetCurrentProcess(),None,ctypes.byref(inside)) and inside.value
try:
    allocation=bytearray(128*1024*1024)
    message='unbounded'
except MemoryError:
    message='bounded'
print(json.dumps(dict(text=message,chars=len(message),warning='',error='',kind='')))
'''
    probe(monkeypatch, source)
    assert runner.parse_document(".txt", b"x", replace(PERSONAL_POLICY, memory_bytes=64*1024*1024)).text == "bounded"
    # Explicitly inspect the owned Job handle's inheritance flag.
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        job = runner.WindowsJob(process, PERSONAL_POLICY)
        flags = ctypes.c_ulong()
        job.api.GetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        assert job.api.GetHandleInformation(job.handle, ctypes.byref(flags)) and not flags.value & 1
        job.close()
    finally:
        process.kill()
        process.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows assignment failure")
def test_failed_job_never_releases_input(monkeypatch, tmp_path):
    marker = tmp_path / "read.txt"
    probe(monkeypatch, f"import sys;sys.stdin.buffer.read(1);open({str(marker)!r},'w').write('read')")
    monkeypatch.setattr(runner, "WindowsJob", lambda *_: (_ for _ in ()).throw(OSError("assignment failed")))
    assert runner.parse_document(".txt", b"private bytes", PERSONAL_POLICY).kind == ErrorKind.BOUNDARY
    assert not marker.exists()
