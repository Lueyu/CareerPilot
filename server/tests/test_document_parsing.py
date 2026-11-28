"""Synthetic documents exercise the actual extraction boundary."""
import io
import struct
from dataclasses import replace
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

import pytest
from docx import Document
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.services import document_worker as worker
from app.services.document_policy import PERSONAL_POLICY, PUBLIC_POLICY, ErrorKind, ParseRejected
from app.services.document_runner import parse_document
from app.services.public_library_extract import extract as public_extract
from app.services.text_extract import extract_many, extract_one


def docx_bytes(extra=0):
    stream = io.BytesIO()
    document = Document()
    document.add_paragraph("Question before table")
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Answer in table"
    document.save(stream)
    if extra:
        with ZipFile(stream, "a", ZIP_STORED) as archive:
            for index in range(extra):
                archive.writestr(f"padding/{index}", b"x")
    return stream.getvalue()


def pdf_bytes(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
            NameObject("/F1"): writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 10 100 Td (Question text) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_personal_docx_entry_limit():
    result = extract_one("many.docx", docx_bytes(513))
    assert not result.ok, "personal caller accepted a DOCX above the shared entry limit"


def test_personal_pdf_page_limit():
    result = extract_one("long.pdf", pdf_bytes(31))
    assert not result.ok, "personal caller extracted 31 pages without a page gate"


@pytest.mark.parametrize("policy", [PERSONAL_POLICY, PUBLIC_POLICY])
@pytest.mark.parametrize("suffix,raw", [(".docx", docx_bytes()), (".pdf", pdf_bytes()), (".md", b"Question text")])
def test_actual_valid_documents(policy, suffix, raw):
    result = parse_document(suffix, raw, policy)
    assert result.ok, result
    assert "Question" in result.text
    if suffix == ".docx":
        assert result.text.index("Question") < result.text.index("Answer in table")


def test_caller_contracts_and_partial_order():
    result = extract_many([("first.txt", b"first"), ("broken.docx", b"bad zip"),
                           ("last.md", "最后".encode())])
    assert [f.name for f in result.files] == ["first.txt", "broken.docx", "last.md"]
    assert [f.ok for f in result.files] == [True, False, True]
    assert result.text == "first\n\n最后"
    assert "text" not in public_extract(".txt", b"private only")
    assert extract_one("okay.txt", b"a" * (6 * 1024 * 1024 + 1)).ok
    assert not extract_one("too-big.txt", b"a" * (12 * 1024 * 1024 + 1)).ok
    personal = extract_one("long.txt", b"x" * 60_001)
    assert personal.ok and len(personal.text) == 60_000 and personal.chars == 60_001
    assert "24,000" in public_extract(".md", b"x" * 24_001)["error"]
    assert public_extract(".md", "中".encode() * 24_000) == {"text": "中" * 24_000}


def zip_bytes(name="word/document.xml", data=b"x", compression=ZIP_STORED):
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression) as archive:
        archive.writestr(name, data)
    return stream.getvalue()


@pytest.mark.parametrize("raw", [b"not zip", zip_bytes("../escape"),
    zip_bytes(data=b"x" * (17 * 1024 * 1024), compression=ZIP_DEFLATED),
    zip_bytes(data=b"x" * 100_000, compression=ZIP_DEFLATED)])
def test_archive_rejected_before_document(monkeypatch, raw):
    monkeypatch.setattr("docx.Document", lambda _: pytest.fail("Document reached before ZIP refusal"))
    with pytest.raises(ParseRejected) as rejected:
        worker.from_docx(raw, PERSONAL_POLICY)
    assert rejected.value.kind == ErrorKind.ARCHIVE


def test_crc_and_encryption_before_document(monkeypatch):
    raw = bytearray(zip_bytes(data=b"unique data"))
    start = 30 + len("word/document.xml")
    raw[start] ^= 1
    with pytest.raises(ParseRejected):
        worker.check_docx(bytes(raw), PERSONAL_POLICY)
    raw = bytearray(zip_bytes())
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, 6, 1)
    struct.pack_into("<H", raw, central + 8, 1)
    with pytest.raises(ParseRejected):
        worker.check_docx(bytes(raw), PERSONAL_POLICY)


def test_actual_zip_reads_precede_document(monkeypatch):
    # A corrupt member fails only when read, not when central-directory sizes are inspected.
    raw = docx_bytes()
    monkeypatch.setattr(ZipFile, "open", lambda *args: (_ for _ in ()).throw(BadZipFile("CRC")))
    monkeypatch.setattr("docx.Document", lambda _: pytest.fail("unchecked member reached Document"))
    with pytest.raises(ParseRejected):
        worker.from_docx(raw, PERSONAL_POLICY)


def test_pdf_limit_before_extract_text(monkeypatch):
    raw = pdf_bytes(31)
    monkeypatch.setattr("pypdf._page.PageObject.extract_text", lambda *_: pytest.fail("page extracted before count gate"))
    with pytest.raises(ParseRejected) as rejected:
        worker.from_pdf(raw, PERSONAL_POLICY)
    assert rejected.value.kind == ErrorKind.PAGES
    assert parse_document(".pdf", pdf_bytes(30), PUBLIC_POLICY).ok


def test_pixel_gate_before_conversion_or_ocr(monkeypatch):
    stream = io.BytesIO()
    Image.new("RGB", (4000, 3100)).save(stream, "PNG")
    monkeypatch.setattr(Image.Image, "convert", lambda *_: pytest.fail("RGB allocated before pixel gate"))
    with pytest.raises(ParseRejected) as rejected:
        worker.from_image(stream.getvalue(), PERSONAL_POLICY)
    assert rejected.value.kind == ErrorKind.PIXELS
    assert parse_document(".png", stream.getvalue(), PERSONAL_POLICY).kind == ErrorKind.PIXELS


def test_optional_ocr_and_decoder_are_distinct(monkeypatch):
    import sys
    stream = io.BytesIO()
    Image.new("RGB", (20, 20)).save(stream, "PNG")
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
    assert worker.extract(".png", stream.getvalue(), PERSONAL_POLICY).kind == ErrorKind.OCR
    assert parse_document(".png", b"not an image", PERSONAL_POLICY).kind == ErrorKind.DECODER


def test_actual_optional_ocr():
    pytest.importorskip("rapidocr_onnxruntime")
    image = Image.new("RGB", (1000, 200), "white")
    ImageDraw.Draw(image).text((30, 50), "HELLO DOCUMENT 123", fill="black", font=ImageFont.load_default(size=50))
    stream = io.BytesIO()
    image.save(stream, "PNG")
    result = parse_document(".png", stream.getvalue(), PERSONAL_POLICY)
    assert result.ok and "123" in result.text, result


def test_actual_read_budget(monkeypatch):
    from contextlib import nullcontext
    # Declared metadata is tiny, but the member stream exceeds the budget.
    raw = zip_bytes()
    monkeypatch.setattr(ZipFile, "open", lambda *_: nullcontext(io.BytesIO(b"x" * 100)))
    with pytest.raises(ParseRejected):
        worker.check_docx(raw, replace(PERSONAL_POLICY, expanded_bytes=32))
