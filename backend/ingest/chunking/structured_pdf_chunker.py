"""Structured PDF chunker.

Goals
- Work on per-page items produced by pdf_loader (text + metadata.page).
- Remove repeated headers/footers across pages and TOC-like lines (dot leaders).
- Reconstruct paragraphs/lists from wrapped lines.
- Enforce an effective max token budget before embedding preflight.

Exports
- chunk_structured_pdf_items(items, chunker_cfg, effective_max_tokens) -> list[dict]
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Sequence

from backend.ingest.chunking.toc_utils import strip_toc_region

def _estimate_tokens(text: str) -> int:
    return max(0, int(round(len(text) / 4))) if text else 0


def _split_to_token_limit(text: str, max_tokens: int) -> List[str]:
    if _estimate_tokens(text) <= max_tokens:
        return [text] if text.strip() else []

    parts = re.split(r"(?<=[.!?])\s+", text)
    chunks: List[str] = []
    buf: List[str] = []
    for part in parts:
        candidate = (" ".join(buf + [part])).strip()
        if candidate and _estimate_tokens(candidate) <= max_tokens:
            buf.append(part)
            continue
        if buf:
            chunks.append(" ".join(buf).strip())
            buf = [part]
        else:
            words = part.split()
            wbuf: List[str] = []
            for w in words:
                candidate_w = " ".join(wbuf + [w]).strip()
                if candidate_w and _estimate_tokens(candidate_w) <= max_tokens:
                    wbuf.append(w)
                else:
                    if wbuf:
                        chunks.append(" ".join(wbuf).strip())
                    wbuf = [w]
            if wbuf:
                buf = [wbuf[-1]]
            else:
                buf = []
    if buf:
        chunks.append(" ".join(buf).strip())
    return [c for c in chunks if c]


def _is_toc_line(line: str) -> bool:
    return bool(re.search(r"\.{3,}\s*\d+$", line))


def _is_bullet(line: str) -> bool:
    return bool(re.match(r"^(\s*[-*•\u2022]\s+|\s*\d+[.)]\s+)", line))


def _toc_like_score(line: str) -> int:
    """Return 1 if line looks like TOC entry, else 0 (conservative)."""
    if not line or len(line) < 4:
        return 0
    if re.search(r"\.{3,}\s*\d+$", line):
        return 1
    if re.search(r"\t+\s*\d+$", line):
        return 1
    if re.match(r"\s*\d+(\.\d+)*\s+.+\s+\d{1,4}$", line):
        return 1
    spaced = re.match(r"^[A-Za-z].+\s+\d{1,4}$", line)
    if spaced and re.search(r"[\\.]{2,}", line):
        return 1
    if re.match(r"^\s*\d+(\.\d+)*[A-Za-z].*\d+\s*$", line):
        return 1
    return 0


def _filter_toc_blocks(lines: List[str], *, cfg: Dict[str, int], page_no: int | None) -> List[str]:
    """Drop TOC-like blocks using toc_mode heuristics."""
    mode = cfg.get("toc_mode", "auto")
    remove_toc = bool(cfg.get("drop_toc", True))
    if not remove_toc or mode == "off":
        return lines
    if page_no is not None and mode == "auto":
        max_pages = int(cfg.get("pdf_toc_max_pages", 5) or 5)
        if page_no > max_pages:
            return lines
    window_lines = int(cfg.get("toc_window_lines", 20) or 20)
    min_hits = int(cfg.get("toc_min_hits", 6) or 6)

    if mode == "strict":
        return [ln for ln in lines if _toc_like_score(ln) == 0]

    n = len(lines)
    drop_indices: set[int] = set()
    step = max(1, window_lines)
    for start in range(0, n, step):
        window = lines[start : start + window_lines]
        hits = [i for i, ln in enumerate(window) if _toc_like_score(ln)]
        if len(hits) >= min_hits:
            drop_indices.update(range(start, min(start + window_lines, n)))
    return [ln for idx, ln in enumerate(lines) if idx not in drop_indices]


def _find_repeated_lines(pages: Sequence[Dict]) -> set[str]:
    counts: Counter[str] = Counter()
    for page in pages:
        for ln in (page.get("text") or "").splitlines():
            ln = ln.strip()
            if 1 <= len(ln) <= 120:
                counts[ln] += 1
    if not counts:
        return set()
    threshold = max(2, int(len(pages) * 0.6))
    return {ln for ln, c in counts.items() if c >= threshold}


def _clean_lines(lines: List[str], *, drop_toc: bool, repeated: set[str]) -> List[str]:
    cleaned: List[str] = []
    for ln in lines:
        if not ln:
            continue
        if drop_toc and _is_toc_line(ln):
            continue
        if ln in repeated:
            continue
        cleaned.append(ln)
    return cleaned


def _reconstruct_blocks(lines: List[str]) -> List[Dict[str, str]]:
    blocks: List[Dict[str, str]] = []
    current: List[str] = []
    for ln in lines:
        if _is_bullet(ln):
            if current:
                blocks.append({"type": "paragraph", "text": " ".join(current).strip()})
                current = []
            blocks.append({"type": "list", "text": ln.strip()})
            continue

        if not current:
            current = [ln]
        else:
            current.append(ln)
    if current:
        blocks.append({"type": "paragraph", "text": " ".join(current).strip()})
    return blocks


def _combine_blocks(blocks: List[Dict[str, str]], max_tokens: int) -> List[Dict[str, str]]:
    combined: List[Dict[str, str]] = []
    for blk in blocks:
        text = blk["text"].strip()
        if not text:
            continue
        if blk["type"] == "list" and combined and combined[-1]["type"] == "paragraph":
            combined[-1]["text"] = (combined[-1]["text"] + "\n" + text).strip()
            continue

        splits = _split_to_token_limit(text, max_tokens)
        for part in splits:
            if part.strip():
                combined.append({"type": blk["type"], "text": part.strip()})
    return combined


def chunk_structured_pdf_items(
    items: Sequence[Dict],
    chunker_cfg: Dict,
    effective_max_tokens: int,
) -> List[Dict[str, object]]:
    drop_toc = bool(chunker_cfg.get("drop_toc", True))
    toc_mode = str(chunker_cfg.get("toc_mode", "auto") or "auto").lower() if drop_toc else "off"
    toc_min_hits = int(chunker_cfg.get("toc_min_hits", 6) or 6)
    toc_window_lines = int(chunker_cfg.get("toc_window_lines", 20) or 20)
    pdf_toc_max_pages = int(chunker_cfg.get("pdf_toc_max_pages", 5) or 5)
    drop_repeated = bool(chunker_cfg.get("drop_repeated_headers_footers", True))
    min_tokens = int(chunker_cfg.get("min_tokens", 0) or 0)
    pdf_cfg = {
        "drop_toc": drop_toc,
        "toc_mode": toc_mode,
        "toc_min_hits": toc_min_hits,
        "toc_window_lines": toc_window_lines,
        "pdf_toc_max_pages": pdf_toc_max_pages,
    }

    repeated_lines = _find_repeated_lines(items) if drop_repeated else set()
    chunks: List[Dict[str, object]] = []

    for page in items:
        text = page.get("text") or ""
        meta = dict(page.get("metadata") or {})
        page_no = meta.get("page")
        lines = [ln.strip() for ln in text.splitlines()]
        if drop_toc and toc_mode == "auto" and isinstance(page_no, int) and page_no <= pdf_toc_max_pages:
            lines = strip_toc_region(lines, {"toc_stop_on_heading": True})
        lines = _clean_lines(lines, drop_toc=drop_toc, repeated=repeated_lines)
        if not lines:
            continue
        blocks = _reconstruct_blocks(lines)
        units = _combine_blocks(blocks, effective_max_tokens)
        for unit in units:
            if min_tokens > 0 and _estimate_tokens(unit["text"]) < min_tokens:
                continue
            chunk_meta = dict(meta)
            if page_no is not None:
                chunk_meta["page_start"] = page_no
                chunk_meta["page_end"] = page_no
            chunk_meta["unit_type"] = unit["type"]
            chunks.append(
                {
                    "text": unit["text"],
                    "metadata": chunk_meta,
                }
            )
    return chunks
