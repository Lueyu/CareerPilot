"""One document, one bounded worker. No application configuration or credentials."""
from __future__ import annotations

import io
import json
import re
import sys
from dataclasses import asdict
from pathlib import PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from app.services.document_policy import (
    IMAGE_SUFFIXES,
    POLICIES,
    ErrorKind,
    ParseRejected,
    ParseResult,
    ParserPolicy,
)


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return re.sub(r"\n{4,}", "\n\n\n", re.sub(r"[ \t]+\n", "\n", text)).strip()


def check_docx(raw: bytes, policy: ParserPolicy) -> None:
    message = "DOCX 解包体积、条目或压缩比例超过限制，请删减内容或拆分后上传。"
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > policy.zip_entries or sum(e.file_size for e in entries) > policy.expanded_bytes:
                raise ParseRejected(ErrorKind.ARCHIVE, message)
            names = set()
            for entry in entries:
                path = PurePosixPath(entry.filename.replace("\\", "/"))
                if (entry.flag_bits & 1 or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                        or entry.file_size > max(1, entry.compress_size) * policy.compression_ratio
                        or path.is_absolute() or ".." in path.parts or ":" in entry.filename
                        or entry.filename in names):
                    raise ParseRejected(ErrorKind.ARCHIVE, message)
                names.add(entry.filename)
            actual = 0
            for entry in entries:
                with archive.open(entry) as member:
                    while chunk := member.read(min(65536, policy.expanded_bytes - actual + 1)):
                        actual += len(chunk)
                        if actual > policy.expanded_bytes:
                            raise ParseRejected(ErrorKind.ARCHIVE, message)
    except (BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError) as exc:
        raise ParseRejected(ErrorKind.ARCHIVE, "无法打开这个 DOCX 文件，请用 Word 另存后重试。") from exc


def from_docx(raw: bytes, policy: ParserPolicy) -> str:
    check_docx(raw, policy)
    import docx
    document = docx.Document(io.BytesIO(raw))
    if policy.linear_docx:
        from docx.oxml.ns import qn
        from docx.text.paragraph import Paragraph
        parts = [Paragraph(node, document).text for node in document.element.body.iter(qn("w:p"))]
    else:
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
    return clean("\n".join(parts))


def from_pdf(raw: bytes, policy: ParserPolicy) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted:
        raise ParseRejected(ErrorKind.DECODER, "PDF 已加密，请解密后重试。")
    if len(reader.pages) > policy.pdf_pages:
        raise ParseRejected(ErrorKind.PAGES, f"PDF 超过 {policy.pdf_pages} 页，请拆分后再上传")
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except (MemoryError, RecursionError):
            raise
        except Exception:
            pages.append("")
    text = clean("\n".join(pages))
    if not text:
        raise ParseRejected(ErrorKind.EMPTY, "未能读取 PDF 文字，请上传可选中文字的 PDF 或改用 DOCX、Markdown。")
    return text


def from_image(raw: bytes, policy: ParserPolicy) -> str:
    from PIL import Image
    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
            if (width <= 0 or height <= 0 or max(width, height) > policy.image_edge
                    or width * height > policy.image_pixels):
                raise ParseRejected(ErrorKind.PIXELS, "图片尺寸或像素超过限制，请缩小后上传。")
            try:
                import numpy as np
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                raise ParseRejected(ErrorKind.OCR, "服务端未安装图片 OCR 依赖，请改用文本粘贴。") from exc
            engine = RapidOCR(intra_op_num_threads=1, inter_op_num_threads=1)
            result, _ = engine(np.array(image.convert("RGB")))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ParseRejected(ErrorKind.PIXELS, "图片尺寸或像素超过限制，请缩小后上传。") from exc
    if not result:
        raise ParseRejected(ErrorKind.EMPTY, "图片中未识别到文字，请改用清晰图片或文本粘贴。")
    return clean("\n".join(str(line[1]).strip() for line in result if len(line) > 1))


def extract(suffix: str, raw: bytes, policy: ParserPolicy) -> ParseResult:
    try:
        if suffix == ".docx":
            text = from_docx(raw, policy)
        elif suffix == ".pdf":
            text = from_pdf(raw, policy)
        elif suffix in IMAGE_SUFFIXES:
            text = from_image(raw, policy)
        else:
            for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
                try:
                    text = clean(raw.decode(encoding))
                    break
                except UnicodeDecodeError:
                    continue
        if not text:
            raise ParseRejected(ErrorKind.EMPTY, "没有提取到文字内容")
        if policy.reject_long_text and len(text) > policy.text_chars:
            raise ParseRejected(ErrorKind.LENGTH, "正文超过 24,000 字符，请拆分后上传。")
        return ParseResult(text=text[:policy.text_chars], chars=len(text))
    except ParseRejected as exc:
        return ParseResult(error=str(exc), kind=exc.kind)
    except MemoryError:
        return ParseResult(error="文件解析超过内存限制，请拆分文档后重试。", kind=ErrorKind.RESOURCE)
    except Exception:
        return ParseResult(error="文件解析失败，请检查文档或换一个格式上传。", kind=ErrorKind.DECODER)


def main() -> None:
    policy = POLICIES[sys.argv[1]]
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (policy.memory_bytes,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (policy.cpu_seconds, policy.cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    elif sys.platform != "win32":
        raise SystemExit(2)
    # Parent assigns the Windows Job before releasing even this first byte.
    if sys.stdin.buffer.read(1) != b"1":
        raise SystemExit(2)
    suffix = sys.argv[2]
    raw = sys.stdin.buffer.read(policy.input_bytes + 1)
    if suffix not in policy.suffixes or not raw or len(raw) > policy.input_bytes:
        result = ParseResult(error="文件类型或大小不符合限制。", kind=ErrorKind.INVALID)
    else:
        result = extract(suffix, raw, policy)
    sys.stdout.buffer.write(json.dumps(asdict(result), ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
