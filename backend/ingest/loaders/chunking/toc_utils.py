
"""Shared TOC helpers for structured chunkers (DOCX/PDF)."""

from __future__ import annotations

import re
from typing import Iterable, List, Dict


def is_toc_anchor(line: str) -> bool:
    """Detect TOC anchors like 'Table of Contents' or 'Contents'."""
    if not line:
        return False
    low = line.lower()
    return "table of contents" in low or low.strip() == "contents"


def _is_heading_like(line: str) -> bool:
    if not line:
        return False
    if len(line) > 80:
        return False
    if line.strip() and line.strip()[-1].isdigit():
        return False
    letters_spaces = sum(1 for ch in line if ch.isalpha() or ch.isspace())
    return letters_spaces >= len(line) * 0.7


def is_toc_like(line: str) -> bool:
    """Conservative TOC-like detection (handles concatenated entries)."""
    if not line:
        return False
    stripped = line.strip()
    # Dotted leaders or spaced/tab-separated page numbers are strong signals.
    if re.search(r"\.{3,}\s*\d{1,4}\s*$", stripped):
        return True
    if re.search(r"(?:\t+|\s{4,})\d{1,4}\s*$", stripped):
        return True
    # Concatenated number/title/page patterns e.g. "1Document Control2".
    if re.match(r"\s*\d+(\.\d+)*\s*[A-Za-z][^0-9]{0,80}?\d{1,4}\s*$", stripped):
        return True
    # Appendix sections commonly appear without spacing before the page number.
    if re.match(r"^\s*Appendix[^0-9]{0,20}?\d{1,4}\s*$", stripped, flags=re.IGNORECASE):
        return True
    return False


def _is_toc_like(line: str) -> bool:
    return is_toc_like(line)


def strip_toc_region(lines: Iterable[str], cfg: Dict[str, object]) -> List[str]:
    """Remove TOC region starting at anchor; stop when heading-like appears."""
    toc_stop_on_heading = bool(cfg.get("toc_stop_on_heading", True))
    out: List[str] = []
    toc_region = False
    for line in lines:
        if not toc_region and is_toc_anchor(line):
            toc_region = True
            continue  # drop anchor
        if toc_region:
            if toc_stop_on_heading and _is_heading_like(line):
                toc_region = False
                out.append(line)
                continue
            if _is_toc_like(line):
                continue
            # Heuristic: lines ending with page number treated as TOC
            if re.search(r"\d{1,4}\s*$", line.strip()) and not _is_heading_like(line):
                continue
        out.append(line)
    return out
