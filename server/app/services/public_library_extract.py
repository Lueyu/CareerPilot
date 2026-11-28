"""Registered-import adapter over the shared bounded parser."""
from __future__ import annotations

from app.services.document_policy import PUBLIC_POLICY
from app.services.document_runner import parse_document, parse_document_async

MAX_UPLOAD_BYTES = PUBLIC_POLICY.input_bytes
MAX_DOCX_EXPANDED_BYTES = PUBLIC_POLICY.expanded_bytes
MAX_IMPORT_CHARS = PUBLIC_POLICY.text_chars
PDF_PAGE_LIMIT = PUBLIC_POLICY.pdf_pages
SUFFIXES = PUBLIC_POLICY.suffixes


def extract(suffix: str, raw: bytes) -> dict[str, str]:
    result = parse_document(suffix, raw, PUBLIC_POLICY)
    return {"text": result.text} if result.ok else {"error": result.error}


async def extract_async(suffix: str, raw: bytes) -> dict[str, str]:
    result = await parse_document_async(suffix, raw, PUBLIC_POLICY)
    return {"text": result.text} if result.ok else {"error": result.error}
