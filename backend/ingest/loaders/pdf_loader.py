"""PDF loader implementation with optional OCR fallback (ocrmypdf).

Purpose
- Use native PDF text extraction via pdfplumber as default.
- When configured, selectively apply OCR: per-page (auto) or entire file (force).

Environment controls
- PDF_OCR_MODE: off | auto | force (default auto)
- PDF_OCR_LANGS: language codes (default eng)
- PDF_OCR_PAGE_LIMIT: int (0 = unlimited)
- PDF_OCR_MIN_TEXT_CHARS: per-page native text threshold (default 50)

Contract
- export: load(path: str) -> list[dict]
- Each returned item includes:
  - "text": str (skip if empty)
  - "metadata": dict with: `source` (abs), `content_type`, `page` (int), `has_ocr` (bool)
"""

from typing import List, Dict
import logging
import os
from tempfile import TemporaryDirectory
from backend.ingest.text_cleaner import clean_text

import pdfplumber  # type: ignore

logger = logging.getLogger(__name__)

# simple module-level counters for visibility
pages_total = 0
pages_ocr = 0
pages_native_empty = 0


def _extract_native_texts(abs_path: str) -> List[str]:
    texts: List[str] = []
    with pdfplumber.open(abs_path) as pdf:
        for page in pdf.pages:
            try:
                txt = page.extract_text() or ""
            except Exception:  # noqa: BLE001
                txt = ""
            texts.append((txt or "").replace("\r", "\n").strip())
    return texts


def _ocr_single_page(ocr, src_pdf: str, page_number: int, langs: str) -> str:
    """Run OCR on a single page by creating one-page PDF and reading OCR result."""
    try:
        from PyPDF2 import PdfReader, PdfWriter  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.warning("PyPDF2 unavailable for per-page OCR: %s", exc)
        return ""

    with TemporaryDirectory() as td:
        single_in = os.path.join(td, "in.pdf")
        single_out = os.path.join(td, "out.pdf")
        try:
            reader = PdfReader(src_pdf)
            writer = PdfWriter()
            # page_number is 1-based
            src_idx = max(0, page_number - 1)
            if src_idx >= len(reader.pages):
                return ""
            writer.add_page(reader.pages[src_idx])
            with open(single_in, "wb") as f:
                writer.write(f)
            # ocrmypdf.ocr
            ocr.ocr(single_in, single_out, language=langs, force_ocr=True, progress_bar=False)
            # read OCR text
            with pdfplumber.open(single_out) as ocr_pdf:
                page = ocr_pdf.pages[0]
                return (page.extract_text() or "").replace("\r", "\n").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR single-page failed (page=%d): %s", page_number, exc)
            return ""


def load(path: str) -> List[Dict]:
    """Load a PDF file and return a list of items with text and metadata."""
    global pages_total, pages_ocr, pages_native_empty
    abs_path = os.path.abspath(path)

    # env knobs
    mode = (os.getenv("PDF_OCR_MODE", "auto") or "auto").lower()
    langs = os.getenv("PDF_OCR_LANGS", "eng") or "eng"
    try:
        page_limit = int(os.getenv("PDF_OCR_PAGE_LIMIT", "0") or "0")
    except Exception:
        page_limit = 0
    try:
        min_chars = int(os.getenv("PDF_OCR_MIN_TEXT_CHARS", "50") or "50")
    except Exception:
        min_chars = 50

    native_texts = _extract_native_texts(abs_path)
    n_pages = len(native_texts)
    pages_total += n_pages
    # mark native-empty pages
    native_empty_flags = [len(t) < min_chars for t in native_texts]
    pages_native_empty += sum(1 for f in native_empty_flags if f)

    ocr_texts: List[str] = [""] * n_pages
    performed_ocr = 0

    if mode == "off":
        pass  # no OCR
    else:
        # Try import lazily
        try:
            import ocrmypdf as ocr  # type: ignore
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR disabled (ocrmypdf unavailable): %s", exc)
            ocr = None

        if ocr is not None:
            if mode == "force":
                with TemporaryDirectory() as td:
                    out_pdf = os.path.join(td, "ocr_full.pdf")
                    try:
                        ocr.ocr(abs_path, out_pdf, language=langs, force_ocr=True, progress_bar=False)
                        with pdfplumber.open(out_pdf) as pdf:
                            for i, page in enumerate(pdf.pages):
                                if page_limit > 0 and i >= page_limit:
                                    break
                                txt = (page.extract_text() or "").replace("\r", "\n").strip()
                                ocr_texts[i] = txt
                                performed_ocr += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("OCR full-file failed: %s", exc)
                        performed_ocr = performed_ocr  # no-op; keep native
            else:  # auto
                # build list of pages to OCR
                to_ocr = [i + 1 for i, flag in enumerate(native_empty_flags) if flag]
                if page_limit > 0:
                    to_ocr = to_ocr[:page_limit]
                for pno in to_ocr:
                    txt = _ocr_single_page(ocr, abs_path, pno, langs)
                    if txt:
                        ocr_texts[pno - 1] = txt
                        performed_ocr += 1

    pages_ocr += performed_ocr

    items: List[Dict] = []
    for idx in range(n_pages):
        text = ocr_texts[idx] or native_texts[idx] or ""
        text = clean_text(text, preserve_tables=False)
        if not text:
            # Skip empty items
            continue
        items.append(
            {
                "text": text,
                "metadata": {
                    "source": abs_path,
                    "content_type": "application/pdf",
                    "page": idx + 1,
                    "has_ocr": bool(ocr_texts[idx]),
                },
            }
        )
    return items
