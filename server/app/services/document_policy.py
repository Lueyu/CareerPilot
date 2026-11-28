"""Trusted, immutable document parsing policies and bounded result protocol."""
from dataclasses import dataclass
from enum import StrEnum

TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text", ".csv", ".json", ".log", ".html", ".htm", ""})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic"})


class ErrorKind(StrEnum):
    INVALID = "invalid_input"
    ARCHIVE = "unsafe_archive"
    PAGES = "page_limit"
    PIXELS = "image_limit"
    OCR = "ocr_unavailable"
    DECODER = "decoder_error"
    EMPTY = "empty_text"
    LENGTH = "text_limit"
    RESOURCE = "resource_limit"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BUSY = "busy"
    BOUNDARY = "boundary_unavailable"
    PROTOCOL = "protocol_error"


@dataclass(frozen=True)
class ParserPolicy:
    name: str
    suffixes: frozenset[str]
    input_bytes: int
    text_chars: int
    memory_bytes: int
    wall_seconds: float = 10
    cpu_seconds: int = 6
    pdf_pages: int = 30
    zip_entries: int = 512
    expanded_bytes: int = 16 * 1024 * 1024
    compression_ratio: int = 200
    image_pixels: int = 12_000_000
    image_edge: int = 12_000
    linear_docx: bool = False
    reject_long_text: bool = False


PERSONAL_POLICY = ParserPolicy("personal", TEXT_SUFFIXES | IMAGE_SUFFIXES | {".pdf", ".docx"},
                               12 * 1024 * 1024, 60_000, 768 * 1024 * 1024, wall_seconds=20)
PUBLIC_POLICY = ParserPolicy("public", frozenset({".pdf", ".docx", ".md", ".markdown"}),
                             6 * 1024 * 1024, 24_000, 256 * 1024 * 1024,
                             linear_docx=True, reject_long_text=True)
POLICIES = {policy.name: policy for policy in (PERSONAL_POLICY, PUBLIC_POLICY)}
MAX_PROTOCOL_BYTES = 400_000


@dataclass(frozen=True)
class ParseResult:
    text: str = ""
    chars: int = 0
    warning: str = ""
    error: str = ""
    kind: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class ParseRejected(Exception):
    def __init__(self, kind: ErrorKind, message: str):
        super().__init__(message)
        self.kind = kind
