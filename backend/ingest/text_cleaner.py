"""Deterministic text cleaning utilities for ingestion.

Pipeline order: loader -> CLEAN -> sanitize -> chunk -> embed

Exports
- clean_text(text: str, *, preserve_tables: bool = False) -> str

Cleaning policy
1) Normalize Unicode to NFC.
2) Remove/replace invisible chars: ZERO WIDTH (\u200B-\u200D), NBSP (\u00A0 -> space), SOFT HYPHEN (\u00AD -> remove).
3) Convert common ligatures: ﬁ -> fi, ﬂ -> fl.
4) Normalize line endings to '\n'; trim trailing spaces; collapse multiple spaces to a single space (do not collapse newlines).
5) Safe de-hyphenation at line breaks: join words split with '-' only when the next line continues the same word (avoid real hyphenated terms).
6) Header/footer dedup (conservative): drop short lines that repeat many times.
7) When preserve_tables=True (e.g., XLSX summaries), keep row structure (one row per line) and skip de-hyphenation.
8) Filter noise blocks: drop blocks with <10 alphabetic chars unless heading-like (ALL-CAPS or Title Case).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_ZW_RE = re.compile(r"[\u200B-\u200D]")  # zero-widths
_NBSP_RE = re.compile(r"\u00A0")
_SOFT_HYPHEN_RE = re.compile(r"\u00AD")
_SPACES_RE = re.compile(r"[ \t]+")
_LINE_ENDING_RE = re.compile(r"\r\n?|\n")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _strip_invisible(s: str) -> str:
    s = _SOFT_HYPHEN_RE.sub("", s)
    s = _ZW_RE.sub("", s)
    s = _NBSP_RE.sub(" ", s)
    return s


def _convert_ligatures(s: str) -> str:
    return s.replace("ﬁ", "fi").replace("ﬂ", "fl")


def _normalize_lines(s: str) -> str:
    # Normalize line endings to \n and trim trailing spaces per line
    s = _LINE_ENDING_RE.sub("\n", s)
    lines = [line.rstrip() for line in s.split("\n")]
    # Collapse multiple spaces (not newlines)
    lines = [_SPACES_RE.sub(" ", ln) for ln in lines]
    return "\n".join(lines).strip()


def _safe_dehyphenate(s: str) -> str:
    """Join words split across lines by a hyphen at line end.

    Only dehyphenate patterns like: 'exam-\nple' -> 'example'.
    Avoids real hyphenated terms by requiring both sides to be alphabetic
    and the next token to start lowercase.
    """
    # Replace occurrences of letter-letter hyphen-newline-lowercase with joined form
    pattern = re.compile(r"([A-Za-z]{2,})-\n([a-z]{2,})")
    while True:
        new_s = pattern.sub(r"\1\2\n", s)
        if new_s == s:
            break
        s = new_s
    return s


def _dedup_headers_footers(lines: list[str]) -> list[str]:
    # Conservative: identify short lines (<= 60 chars) that repeat >=3 times and form >5% of lines
    from collections import Counter

    short_lines = [ln for ln in lines if ln and len(ln) <= 60]
    counts = Counter(short_lines)
    total = max(1, len(lines))
    drop = {ln for ln, c in counts.items() if c >= 3 and (c / total) > 0.05}
    if not drop:
        return lines
    return [ln for ln in lines if ln not in drop]


def _is_heading_like(line: str) -> bool:
    if not line:
        return False
    # ALL CAPS short or Title Case short
    if len(line) <= 60:
        if re.fullmatch(r"[A-Z0-9 ,.:;()\-/]+", line):
            return True
        if re.fullmatch(r"([A-Z][a-z]+)( [A-Z][a-z]+)*", line):
            return True
    return False


def _filter_noise_blocks(text: str) -> str:
    blocks = text.split("\n\n")
    kept: list[str] = []
    for b in blocks:
        alpha = sum(1 for ch in b if ch.isalpha())
        if alpha >= 10 or _is_heading_like(b.strip()):
            kept.append(b)
    return "\n\n".join(kept).strip()


def clean_text(text: str, *, preserve_tables: bool = False) -> str:
    """Apply deterministic cleaning to text.

    preserve_tables: when True, keeps per-line structure (no dehyphenation), suitable for table-like content.
    """
    if not text:
        return ""

    s = _nfc(str(text))
    s = _strip_invisible(s)
    s = _convert_ligatures(s)
    s = _normalize_lines(s)

    # Optional header/footer de-duplication
    lines = s.split("\n")
    lines = _dedup_headers_footers(lines)
    s = "\n".join(lines)

    # Safe dehyphenation (skip when preserving rows)
    if not preserve_tables:
        s = _safe_dehyphenate(s)

    # Noise block filtering
    s = _filter_noise_blocks(s)
    return s.strip()

