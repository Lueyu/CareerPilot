"""上传文件 → 纯文本。

面经可能是 PDF / DOCX / Markdown / 纯文本 / 图片。这里只做"取出文字"这件事，
不做题目提取(那是 bank._extract_questions 的职责)。

设计原则(与 PRD 一致): 任何解析失败都返回结构化错误, 不抛异常打断整个导入;
调用方把所有文件的结果汇总后展示给用户, 成功的部分继续走流水线。
"""
from __future__ import annotations

import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

# 单文件最大 12MB / 总文本 60k 字符, 防止一次导入撑爆上下文
MAX_FILE_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 60_000

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".text", ".csv", ".json", ".log", ".html", ".htm"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic"}


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


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _from_pdf(raw: bytes, page_limit: int | None = None) -> tuple[str, str]:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - 依赖缺失时给出可操作提示
        raise RuntimeError("服务端未安装 pypdf, 无法解析 PDF（pip install pypdf）")
    reader = PdfReader(io.BytesIO(raw))
    if page_limit is not None and len(reader.pages) > page_limit:
        raise RuntimeError(f"PDF 超过 {page_limit} 页，请拆分后再上传")
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    text = _clean("\n".join(pages))
    if not text:
        raise RuntimeError("PDF 中没有可提取的文字（可能是扫描件/图片版），请改用文本粘贴或截图识别")
    warning = ""
    if len(reader.pages) > 30:
        warning = f"文档共 {len(reader.pages)} 页，仅解析前 30 页以外的内容也会一并提取，请留意长度"
    return text, warning


def _from_docx(raw: bytes, *, linear: bool = False) -> tuple[str, str]:
    try:
        import docx  # python-docx
    except ImportError:  # pragma: no cover
        raise RuntimeError("服务端未安装 python-docx, 无法解析 DOCX（pip install python-docx）")
    document = docx.Document(io.BytesIO(raw))
    if linear:
        from docx.oxml.ns import qn
        from docx.text.paragraph import Paragraph

        parts = [Paragraph(node, document).text for node in document.element.body.iter(qn("w:p"))]
    else:
        parts: list[str] = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
    text = _clean("\n".join(parts))
    if not text:
        raise RuntimeError("DOCX 中没有可提取的文字")
    return text, ""


_OCR_ENGINE = None
_OCR_LOCK = threading.Lock()


def _get_ocr_engine():
    """rapidocr 引擎懒加载并缓存(模型加载较重, 只初始化一次; 加锁保证多线程并行解析时只建一次)。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        with _OCR_LOCK:
            if _OCR_ENGINE is None:
                from rapidocr_onnxruntime import RapidOCR
                _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _from_image(raw: bytes) -> tuple[str, str]:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        raise RuntimeError("服务端未安装 Pillow, 无法读取图片（pip install Pillow）")
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        raise RuntimeError("服务端未安装 numpy, 无法处理图片")
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401 - 仅探测依赖是否存在
    except ImportError:
        raise RuntimeError("服务端未安装 rapidocr-onnxruntime, 无法识别图片文字（pip install rapidocr-onnxruntime）")
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        result, _elapse = _get_ocr_engine()(np.array(img))
    except Exception as exc:
        raise RuntimeError(f"图片识别失败: {type(exc).__name__}: {exc}")
    if not result:
        raise RuntimeError("图片中未识别到文字（可能过于模糊，或图片里没有文字）")
    lines = [str(line[1]).strip() for line in result if len(line) > 1 and str(line[1]).strip()]
    return _clean("\n".join(lines)), ""


def extract_one(name: str, raw: bytes, *, pdf_page_limit: int | None = None, linear_docx: bool = False) -> ExtractedFile:
    """解析单个文件; 永远返回 ExtractedFile, 不抛异常。"""
    lower = name.lower()
    suffix = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
    item = ExtractedFile(name=name, suffix=suffix, ok=False)

    if len(raw) > MAX_FILE_BYTES:
        item.error = f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 上限"
        return item
    if not raw:
        item.error = "空文件"
        return item

    try:
        if suffix == ".pdf":
            text, warning = _from_pdf(raw, pdf_page_limit)
        elif suffix == ".docx":
            text, warning = _from_docx(raw, linear=linear_docx)
        elif suffix == ".doc":
            item.error = "旧版 .doc 暂不支持，请另存为 .docx 或粘贴文本"
            return item
        elif suffix in TEXT_SUFFIXES or not suffix:
            text, warning = _clean(_decode(raw)), ""
        elif suffix in IMAGE_SUFFIXES:
            text, warning = _from_image(raw)
        else:
            item.error = f"不支持的文件类型 {suffix or '未知'}（支持 PDF / DOCX / Markdown / TXT）"
            return item
    except RuntimeError as exc:
        item.error = str(exc)
        return item
    except Exception as exc:  # 解析库的各种奇怪报错: 统一收敛
        item.error = f"解析失败: {type(exc).__name__}: {exc}"
        return item

    if not text:
        item.error = "没有提取到文字内容"
        return item
    item.ok = True
    item.text = text[:MAX_TEXT_CHARS]
    item.chars = len(text)
    item.warning = warning
    return item


def extract_many(items: list[tuple[str, bytes]]) -> ExtractResult:
    """并行解析多个文件(OCR/Office 解析是 CPU 密集型, 线程池可显著提速多文件导入)。

    onnxruntime InferenceSession.run 线程安全, 共享单个 OCR 引擎实例没有问题。
    """
    result = ExtractResult()
    if len(items) <= 1:
        for name, raw in items:
            result.files.append(extract_one(name, raw))
        return result
    workers = min(3, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        result.files = list(pool.map(lambda kv: extract_one(kv[0], kv[1]), items))
    return result
