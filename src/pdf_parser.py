"""
pdf_parser.py — PDF text extraction for research papers and official reports.

parse_pdf(filepath)  -> {title, authors, date, full_text, page_count, source_path}
chunk_for_llm(text)  -> token-window chunks with overlap (delegates to llm_client)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz  # pymupdf

from llm_client import chunk_text

logger = logging.getLogger(__name__)

_DATE_PATTERNS = [
    re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b"),                       # 2020-03-01
    re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+(19|20)\d{2}\b", re.I),
    re.compile(r"\b(19|20)\d{2}\b"),                                    # bare year, last resort
]


def _first_date(text: str) -> str | None:
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _guess_title(doc: fitz.Document, fallback: str) -> str:
    meta_title = (doc.metadata or {}).get("title", "").strip()
    if meta_title and meta_title.lower() not in {"untitled", "microsoft word"}:
        return meta_title
    # Heuristic: largest font line on page 1 is usually the title.
    try:
        page = doc[0]
        best_text, best_size = "", 0.0
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                line_text = "".join(s["text"] for s in line.get("spans", [])).strip()
                size = max((s["size"] for s in line.get("spans", [])), default=0.0)
                if len(line_text) > 10 and size > best_size:
                    best_text, best_size = line_text, size
        if best_text:
            return best_text[:300]
    except Exception:  # noqa: BLE001 — heuristic only, never fatal
        pass
    return fallback


def parse_pdf(filepath: str | Path) -> dict:
    """
    Extract text + best-effort metadata from one PDF.
    Raises FileNotFoundError / RuntimeError on unreadable files so the
    orchestrator can log-and-skip.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(path)

    try:
        doc = fitz.open(path)
    except Exception as exc:  # corrupted / encrypted
        raise RuntimeError(f"Cannot open PDF {path.name}: {exc}") from exc

    try:
        pages = [page.get_text("text") for page in doc]
        full_text = "\n\n".join(p.strip() for p in pages if p.strip())
        meta = doc.metadata or {}
        first_page = pages[0] if pages else ""

        record = {
            "title": _guess_title(doc, fallback=path.stem.replace("_", " ")),
            "authors": (meta.get("author") or "").strip() or None,
            "date": (meta.get("creationDate") or "")[2:10] or _first_date(first_page),
            "full_text": full_text,
            "page_count": doc.page_count,
            "source_path": str(path),
        }
    finally:
        doc.close()

    if not record["full_text"]:
        raise RuntimeError(
            f"{path.name}: no extractable text (likely a scanned/image PDF — needs OCR first)"
        )
    logger.info("Parsed PDF %s (%d pages, %d chars)", path.name, record["page_count"], len(record["full_text"]))
    return record


def chunk_for_llm(text: str, max_tokens: int = 8000, overlap: int = 400) -> list[str]:
    """Token-window chunking with overlap for long documents."""
    return chunk_text(text, max_tokens=max_tokens, overlap=overlap)
