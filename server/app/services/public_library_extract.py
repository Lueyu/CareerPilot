"""Bounded document extraction worker for public imports."""

from __future__ import annotations

import io
import json
import sys
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from app.services.text_extract import extract_one

MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_IMPORT_CHARS = 24_000
PDF_PAGE_LIMIT = 30
SUFFIXES = {".pdf", ".docx", ".md", ".markdown"}


def extract(suffix: str, raw: bytes) -> dict[str, str]:
    if suffix not in SUFFIXES or not raw or len(raw) > MAX_UPLOAD_BYTES:
        return {"error": "请选择 6 MB 以内的 PDF、DOCX 或 Markdown 文件。"}

    if suffix == ".docx":
        try:
            with ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if (
                    len(entries) > 512
                    or sum(entry.file_size for entry in entries) > MAX_DOCX_EXPANDED_BYTES
                    or any(entry.compress_type not in {ZIP_DEFLATED, ZIP_STORED} for entry in entries)
                ):
                    return {"error": "DOCX 解包体积超过限制，请删减内容或拆分后上传。"}
        except BadZipFile:
            return {"error": "无法打开这个 DOCX 文件，请用 Word 另存后重试。"}

    item = extract_one(f"interview{suffix}", raw, pdf_page_limit=PDF_PAGE_LIMIT, linear_docx=True)
    if not item.ok:
        if suffix == ".pdf" and "无法解析 PDF" not in item.error:
            if "请拆分" in item.error:
                return {"error": item.error}
            return {"error": "未能读取 PDF 文字，请上传可选中文字的 PDF 或改用 DOCX、Markdown。"}
        return {"error": "文件未提取到文字，请检查文档或换一个格式上传。"}

    text = item.text.replace("\x00", "").strip()
    if not text:
        return {"error": "文件未提取到文字，请换一份可复制文字的文档。"}
    if item.chars > MAX_IMPORT_CHARS:
        return {"error": "正文超过 24,000 字符，请拆分后上传。"}
    return {"text": text}


if __name__ == "__main__":
    if sys.platform.startswith("linux"):
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (6, 7))
    raw = sys.stdin.buffer.read(MAX_UPLOAD_BYTES + 1)
    sys.stdout.buffer.write(json.dumps(extract(sys.argv[1], raw), ensure_ascii=False).encode("utf-8"))
