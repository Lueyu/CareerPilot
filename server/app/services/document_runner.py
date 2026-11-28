"""Own a bounded process tree per parse, shared by both import callers.

This constrains resources, not filesystem/network permissions. Admission is local
to one API process. No parser or untrusted archive is opened in this module.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.services.document_policy import (
    MAX_PROTOCOL_BYTES,
    ErrorKind,
    ParseResult,
    ParserPolicy,
)

MAX_CHILDREN = 3
_SLOTS = threading.BoundedSemaphore(MAX_CHILDREN)
_SERVER = Path(__file__).resolve().parents[2]


class WindowsJob:
    """A non-inheritable Job owns the entire worker tree and total memory budget."""

    def __init__(self, process: subprocess.Popen, policy: ParserPolicy):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                        ("active", w.DWORD), ("affinity", ctypes.c_size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                        ("peak_job", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetHandleInformation": ([w.HANDLE, w.DWORD, w.DWORD], w.BOOL),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = Extended()
            # JOB_TIME | ACTIVE_PROCESS | JOB_MEMORY | KILL_ON_JOB_CLOSE
            limits.basic.flags = 0x4 | 0x8 | 0x200 | 0x2000
            limits.basic.job_time = policy.cpu_seconds * 10_000_000
            limits.basic.active = 4
            limits.job_memory = policy.memory_bytes
            if not (self.api.SetHandleInformation(self.handle, 1, 0)
                    and self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                    and self.api.AssignProcessToJobObject(self.handle, int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
            self.api.CloseHandle(self.handle)
            self.handle = None


def _command(policy: ParserPolicy, suffix: str) -> list[str]:
    return [sys.executable, "-m", "app.services.document_worker", policy.name, suffix]


def _worker_env() -> dict[str, str]:
    # Workers need the interpreter runtime, never API/provider/database secrets.
    allowed = ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    return env


def _read_result(payload: bytes, policy: ParserPolicy) -> ParseResult:
    try:
        frame = json.loads(payload)
        if not isinstance(frame, dict) or set(frame) != {"text", "chars", "warning", "error", "kind"}:
            raise ValueError
        if (any(not isinstance(frame[k], str) for k in ("text", "warning", "error", "kind"))
                or type(frame["chars"]) is not int or not 0 <= frame["chars"] <= policy.input_bytes * 100
                or len(frame["text"]) > policy.text_chars or len(frame["error"]) > 2048
                or len(frame["warning"]) > 512 or frame["kind"] not in {"", *ErrorKind}
                or bool(frame["error"]) != bool(frame["kind"])
                or (frame["error"] and (frame["text"] or frame["chars"]))
                or (not frame["error"] and (not frame["text"] or frame["chars"] < len(frame["text"])))
                or (policy.reject_long_text and frame["chars"] > policy.text_chars)):
            raise ValueError
        return ParseResult(**frame)
    except (ValueError, TypeError, UnicodeError):
        return ParseResult(error="解析进程返回了无效结果，请重试。", kind=ErrorKind.PROTOCOL)


def parse_document(suffix: str, raw: bytes, policy: ParserPolicy,
                   *, cancel: threading.Event | None = None) -> ParseResult:
    if not raw or len(raw) > policy.input_bytes or suffix not in policy.suffixes:
        return ParseResult(error="文件为空、类型不支持或大小超过限制。", kind=ErrorKind.INVALID)
    cancel = cancel or threading.Event()
    deadline = time.monotonic() + policy.wall_seconds
    while not _SLOTS.acquire(timeout=0.02):
        if cancel.is_set():
            return ParseResult(error="文件解析已取消。", kind=ErrorKind.CANCELLED)
        if time.monotonic() >= deadline:
            return ParseResult(error="解析任务繁忙，请稍后重试。", kind=ErrorKind.BUSY)
    process = job = None
    threads = []
    output = bytearray()
    overflow = threading.Event()
    try:
        if cancel.is_set():
            return ParseResult(error="文件解析已取消。", kind=ErrorKind.CANCELLED)
        if sys.platform != "win32" and not sys.platform.startswith("linux"):
            return ParseResult(error="当前系统不支持受限文件解析。", kind=ErrorKind.BOUNDARY)
        process = subprocess.Popen(_command(policy, suffix), cwd=_SERVER, env=_worker_env(),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   close_fds=True, start_new_session=sys.platform != "win32",
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if sys.platform == "win32":
            try:
                job = WindowsJob(process, policy)
            except OSError:
                return ParseResult(error="无法建立文件解析资源边界。", kind=ErrorKind.BOUNDARY)

        def write_input():
            try:
                process.stdin.write(b"1")
                process.stdin.write(raw)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def read_output():
            try:
                while chunk := process.stdout.read(4096):
                    remaining = MAX_PROTOCOL_BYTES + 1 - len(output)
                    output.extend(chunk[:remaining])
                    if len(output) > MAX_PROTOCOL_BYTES:
                        overflow.set()
                        break
            except OSError:
                pass

        threads = [threading.Thread(target=write_input, daemon=True),
                   threading.Thread(target=read_output, daemon=True)]
        for thread in threads:
            thread.start()
        while True:
            if cancel.is_set():
                return ParseResult(error="文件解析已取消。", kind=ErrorKind.CANCELLED)
            if overflow.is_set():
                return ParseResult(error="解析结果超过协议大小限制。", kind=ErrorKind.PROTOCOL)
            if time.monotonic() >= deadline:
                return ParseResult(error="文件解析超时，请拆分文档后重试。", kind=ErrorKind.TIMEOUT)
            if process.poll() is not None:
                break
            time.sleep(0.01)
        # A descendant might still hold stdout open. Terminate the tree before joining readers.
        if job:
            job.close()
        elif sys.platform.startswith("linux"):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for thread in threads:
            thread.join(timeout=2)
        if overflow.is_set():
            return ParseResult(error="解析结果超过协议大小限制。", kind=ErrorKind.PROTOCOL)
        if process.returncode:
            return ParseResult(error="文件解析进程异常退出或超过资源限制，请拆分后重试。", kind=ErrorKind.RESOURCE)
        return _read_result(bytes(output), policy)
    except OSError:
        return ParseResult(error="无法启动受限文件解析进程。", kind=ErrorKind.BOUNDARY)
    finally:
        if job:
            job.close()
        if process:
            if sys.platform.startswith("linux"):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait()
            for thread in threads:
                thread.join(timeout=2)
            for pipe in (process.stdin, process.stdout):
                if pipe and not pipe.closed:
                    pipe.close()
        _SLOTS.release()


async def run_cancellable(function, *args):
    """Keep ownership through spawn and repeated cancellation until cleanup finishes."""
    cancel = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(function, *args, cancel=cancel))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancel.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


async def parse_document_async(suffix: str, raw: bytes, policy: ParserPolicy) -> ParseResult:
    return await run_cancellable(parse_document, suffix, raw, policy)
