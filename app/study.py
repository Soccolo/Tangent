"""PDF text extraction and physical page selection, with no model calls."""

import random
import re
from io import BytesIO
from textwrap import wrap

from pypdf import PdfReader

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 300
MAX_TEXT_CHARS = 1_000_000
MAX_QUESTION_PAGES = 40
MAX_QUESTION_CHARS = 60_000


def extract_pdf(data: bytes) -> list[str]:
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("PDFs must be 10 MiB or smaller.")
    if not data.startswith(b"%PDF-"):
        raise ValueError("Choose a valid PDF file.")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Upload an unlocked PDF; password-protected PDFs are not supported.")
        if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise ValueError("PDFs must contain between 1 and 300 pages.")
        pages = []
        total_chars = 0
        for page in reader.pages:
            content = page.get_contents()
            if content is not None and len(content.get_data()) > 2_000_000:
                raise ValueError("A PDF page is too complex to read. Export a simpler PDF.")
            text = (page.extract_text() or "").replace("\x00", "").strip()
            total_chars += len(text)
            if total_chars > MAX_TEXT_CHARS:
                raise ValueError("This PDF contains too much text. Split it into smaller PDFs.")
            pages.append(text)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("This PDF could not be read. Try exporting it as a new PDF.") from exc
    if not any(pages):
        raise ValueError("No readable text was found. Scanned or handwritten notes need OCR first.")
    return pages


def parse_pages(value: str, page_count: int) -> list[int]:
    """Accept inclusive ranges such as '1-4, 8, 12-15', never silently clamp."""
    if not value.strip() or len(value) > 1000:
        raise ValueError("Enter page numbers, for example 1-4, 8, 12-15.")
    pages: set[int] = set()
    for part in value.split(","):
        match = re.fullmatch(r"\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?", part)
        if match is None:
            raise ValueError("Use page numbers and ranges separated by commas, for example 1-4, 8.")
        start = int(match[1])
        end = int(match[2] or match[1])
        if not 1 <= start <= end <= page_count:
            raise ValueError(f"Page ranges must run forwards and stay between 1 and {page_count}.")
        pages.update(range(start, end + 1))
    return sorted(pages)


def random_excerpt(pages: list[dict]) -> dict:
    page = random.choice(pages)
    # ponytail: PDF paragraphs are heuristic; use layout-aware chunking if needed.
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", page["text"]) if part.strip()]
    paragraph = random.choice(paragraphs)
    # Bound the card even when a PDF exports an entire page as one paragraph.
    chunks = wrap(paragraph, width=1200, expand_tabs=False, replace_whitespace=False)
    return {"page": page["page"], "text": random.choice(chunks)}
