"""上传文件 → 纯文本。

面经可能是 PDF / DOCX / Markdown / 纯文本 / 图片。这里只做"取出文字"这件事，
不做题目提取(那是 bank._extract_questions 的职责)。

设计原则(与 PRD 一致): 任何解析失败都返回结构化错误, 不抛异常打断整个导入;
调用方把所有文件的结果汇总后展示给用户, 成功的部分继续走流水线。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.services.document_policy import IMAGE_SUFFIXES, PERSONAL_POLICY, TEXT_SUFFIXES  # noqa: F401
from app.services.document_runner import parse_document, run_cancellable

# 单文件最大 12MB / 总文本 60k 字符, 防止一次导入撑爆上下文
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 60_000

@dataclass
class ExtractedFile:
    name: str
    suffix: str
    ok: bool
    chars: int = 0
    text: str = ""
    error: str = ""
    warning: str = ""

    def as_meta(self) -> dict:
        return {
            "name": self.name,
            "suffix": self.suffix,
            "ok": self.ok,
            "chars": self.chars,
            "error": self.error,
            "warning": self.warning,
        }


@dataclass
class ExtractResult:
    files: list[ExtractedFile] = field(default_factory=list)

    @property
    def text(self) -> str:
        joined = "\n\n".join(f.text for f in self.files if f.ok and f.text.strip())
        return joined[:MAX_TEXT_CHARS]

    @property
    def ok_files(self) -> list[ExtractedFile]:
        return [f for f in self.files if f.ok]


def extract_one(name: str, raw: bytes, *, cancel: threading.Event | None = None) -> ExtractedFile:
    """Keep legacy metadata while all decoding runs in an owned resource boundary."""
    suffix = "." + name.lower().rsplit(".", 1)[-1] if "." in name else ""
    result = parse_document(suffix, raw, PERSONAL_POLICY, cancel=cancel)
    return ExtractedFile(name, suffix, result.ok, result.chars, result.text,
                         result.error, result.warning)


def extract_many(items: list[tuple[str, bytes]], *, cancel: threading.Event | None = None) -> ExtractResult:
    """Source order and partial successes survive concurrent bounded parses."""
    if len(items) <= 1:
        return ExtractResult([extract_one(name, raw, cancel=cancel) for name, raw in items])
    with ThreadPoolExecutor(max_workers=min(3, len(items))) as pool:
        return ExtractResult(list(pool.map(lambda kv: extract_one(*kv, cancel=cancel), items)))


async def extract_many_async(items: list[tuple[str, bytes]]) -> ExtractResult:
    return await run_cancellable(extract_many, items)
