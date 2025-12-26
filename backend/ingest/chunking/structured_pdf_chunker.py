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
            # Paragraph continuation
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
        # Attach list to previous paragraph when applicable
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
    drop_toc = bool(chunker_cfg.get("drop_toc", chunker_cfg.get("pdf_remove_toc", True)))
    drop_repeated = bool(chunker_cfg.get("drop_repeated_headers_footers", True))
    min_tokens = int(chunker_cfg.get("min_tokens", 0) or 0)

    repeated_lines = _find_repeated_lines(items) if drop_repeated else set()
    chunks: List[Dict[str, object]] = []

    for page in items:
        text = page.get("text") or ""
        meta = dict(page.get("metadata") or {})
        page_no = meta.get("page")
        lines = [ln.strip() for ln in text.splitlines()]
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
