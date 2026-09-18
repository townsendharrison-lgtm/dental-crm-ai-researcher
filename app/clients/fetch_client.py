"""Bounded PDF/DOCX parsing with page/section provenance and OCR fallback."""
import hashlib
import io
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
import pdfplumber
import pypdfium2 as pdfium
import pytesseract

from app.config import Settings

_ocr_lock = threading.Lock()


class DocumentError(ValueError):
    pass


class OcrUnavailable(DocumentError):
    pass


@dataclass(frozen=True)
class TextSection:
    text: str
    page_number: int | None
    section: str | None
    used_ocr: bool = False


@dataclass(frozen=True)
class DocumentChunk:
    index: int
    text: str
    page_number: int | None
    section: str | None
    used_ocr: bool

    @property
    def content_hash(self):
        return hashlib.sha256(self.text.encode()).hexdigest()


def normalize_text(value: str) -> str:
    return " ".join(value.split())


class DocumentParser:
    def __init__(self, settings: Settings, *, ocr=None):
        self.settings = settings
        self.ocr = ocr or self._ocr_page

    def validate(self, data: bytes, filename: str) -> str:
        if not data or len(data) > self.settings.document_max_bytes:
            raise DocumentError("Document is empty or exceeds the upload size limit")
        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf" and data.startswith(b"%PDF-"):
            return "application/pdf"
        if suffix == ".docx" and zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise DocumentError("Not a Word document")
                if sum(item.file_size for item in archive.infolist()) > 100_000_000:
                    raise DocumentError("Expanded Word document exceeds limit")
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        raise DocumentError("Only valid PDF and DOCX documents are supported")

    def parse(self, data: bytes, filename: str) -> list[DocumentChunk]:
        media_type = self.validate(data, filename)
        try:
            sections = self._pdf(data) if media_type == "application/pdf" else self._docx(data)
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError("Document could not be parsed") from exc
        if sum(len(s.text) for s in sections) > self.settings.document_max_chars:
            raise DocumentError("Extracted text exceeds limit; document was not truncated")
        chunks = []
        for section in sections:
            for piece in self._split(section.text):
                chunks.append(DocumentChunk(len(chunks), piece, section.page_number,
                                            section.section, section.used_ocr))
                if len(chunks) > self.settings.document_max_chunks:
                    raise DocumentError("Document has too many chunks; document was not truncated")
        if not chunks:
            raise DocumentError("No readable text found")
        return chunks

    def _pdf(self, data: bytes) -> list[TextSection]:
        sections = []
        total = 0
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > self.settings.document_max_pages:
                raise DocumentError("PDF exceeds page limit")
            for index, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                used_ocr = False
                if len(normalize_text(page_text)) < 30 and page.images:
                    # A scan is not an empty successful extraction if OCR is unavailable.
                    recognized = self.ocr(data, index)
                    page_text = recognized if len(recognized) > len(page_text) else page_text
                    used_ocr = True
                    if not normalize_text(page_text):
                        raise DocumentError("OCR produced no readable text")
                if page_text.strip():
                    sections.append(TextSection(page_text.strip(), index + 1, None, used_ocr))
                    total += len(page_text)
                    if total > self.settings.document_max_chars:
                        raise DocumentError("Extracted text exceeds limit")
                page.close()
        return sections

    def _ocr_page(self, data: bytes, index: int) -> str:
        # pytesseract uses a global executable setting; serialize changes and rendering.
        with _ocr_lock:
            old_command = pytesseract.pytesseract.tesseract_cmd
            pytesseract.pytesseract.tesseract_cmd = self.settings.tesseract_cmd
            try:
                with pdfium.PdfDocument(data) as pdf:
                    page = pdf[index]
                    try:
                        width, height = page.get_size()
                        scale = min(2.5, (20_000_000 / (width * height)) ** 0.5)
                        bitmap = page.render(scale=scale)
                        try:
                            return pytesseract.image_to_string(
                                bitmap.to_pil(), timeout=self.settings.ocr_timeout_seconds,
                            )
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
            except pytesseract.TesseractNotFoundError as exc:
                raise OcrUnavailable("Install Tesseract and configure TESSERACT_CMD for scanned PDFs") from exc
            finally:
                pytesseract.pytesseract.tesseract_cmd = old_command

    def _docx(self, data: bytes) -> list[TextSection]:
        document = Document(io.BytesIO(data))
        sections = []
        heading = None
        lines = []
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                content = block.text.strip()
                if not content:
                    continue
                if block.style and block.style.name.startswith("Heading"):
                    if lines:
                        sections.append(TextSection("\n".join(lines), None, heading))
                    heading, lines = content, [content]
                else:
                    lines.append(content)
            elif isinstance(block, Table):
                lines.extend(" | ".join(cell.text.strip() for cell in row.cells) for row in block.rows)
        if lines:
            sections.append(TextSection("\n".join(lines), None, heading))
        # DOCX does not encode stable rendered pagination; never invent page numbers.
        return sections

    def _split(self, value: str):
        limit = self.settings.document_chunk_chars
        rest = value.strip()
        while rest:
            if len(rest) <= limit:
                yield rest
                return
            boundary = rest.rfind("\n", 0, limit + 1)
            if boundary < limit // 2:
                boundary = rest.rfind(" ", 0, limit + 1)
            if boundary < limit // 2:
                boundary = limit
            yield rest[:boundary].strip()
            rest = rest[boundary:].strip()
