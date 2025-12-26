"""Structured DOCX chunker.

Goals
- Preserve fixed chunker behaviour for non-structured types (handled in caller).
- Group logical units: paragraph + following list items; tables as separate blocks.
- Remove TOC-like lines and repeated headers/footers when configured.
- Enforce an effective max token budget derived from embedding profile limits.

Exports
- chunk_structured_docx_items(items, chunker_cfg, effective_max_tokens) -> list[dict]
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Sequence

from backend.ingest.chunking.toc_utils import strip_toc_region, is_toc_like


def _estimate_tokens(text: str) -> int:
    # Heuristic: ~4 chars per token
    return max(0, int(round(len(text) / 4))) if text else 0


def _split_to_token_limit(text: str, max_tokens: int) -> List[str]:
    if _estimate_tokens(text) <= max_tokens:
        return [text] if text.strip() else []

    parts = re.split(r"(?<=[.!?])\s+", text)
    chunks: List[str] = []
    buf: List[str] = []
    for part in parts:
        candidate = (" ".join(buf + [part])).strip()
        if not candidate:
            continue
        if _estimate_tokens(candidate) <= max_tokens:
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
                if _estimate_tokens(candidate_w) <= max_tokens:
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


def _is_bullet(line: str) -> bool:
    return bool(re.match(r"^(\s*[-*•\u2022]\s+|\s*\d+[.)]\s+)", line))


def _is_toc_line(line: str) -> bool:
    # Detect "Section ....... 3" style
    return bool(re.search(r"\.{3,}\s*\d+$", line))


def _is_toc_line_strict(line: str) -> bool:
    """Stricter TOC signal: dotted leaders, tabs, or numbered heading with trailing page."""
    if not line or len(line) < 4:
        return False
    if re.search(r"\.{3,}\s*\d+$", line):
        return True
    if re.search(r"\t+\s*\d+$", line):
        return True
    if re.match(r"\s*\d+(\.\d+)*\s+.+\s+\d{1,4}$", line):
        return True
    return False


def _is_toc_candidate(line: str) -> bool:
    """Conservative TOC-like heuristic for auto mode."""
    if not line:
        return False
    if len(line) > 160:
        return False
    return is_toc_like(line)


def _count_toc_entries_in_line(line: str) -> int:
    """Count TOC-like entries inside a single flattened line."""
    if not line:
        return 0
    pattern = re.compile(r"\b\d+(?:\.\d+)*\s*[A-Za-z][^0-9]{0,80}?\s*\d{1,4}\b")
    return len(pattern.findall(line))


def _looks_admin_heading(text: str) -> bool:
    lowered = text.lower()
    admin_keys = (
        "document control",
        "version history",
        "revision history",
        "reviewers",
        "approvals",
        "appendix",
    )
    return any(k in lowered for k in admin_keys)


def _normalize_block_lines(block: str) -> List[str]:
    return [ln.strip() for ln in block.splitlines() if ln.strip()]


def _table_to_rows(lines: Sequence[str], mode: str) -> List[str]:
    if mode == "skip":
        return []
    if mode == "raw_text":
        return [" ".join(lines).strip()]

    rows: List[str] = []
    for ln in lines:
        if "|" in ln:
            cells = [c.strip() for c in ln.split("|") if c.strip()]
            if len(cells) >= 2:
                rows.append(": ".join([cells[0], " ".join(cells[1:])]))
                continue
        if "\t" in ln:
            cells = [c.strip() for c in ln.split("\t") if c.strip()]
            if len(cells) >= 2:
                rows.append(": ".join([cells[0], " ".join(cells[1:])]))
                continue
        rows.append(ln.strip())
    return rows


def _clean_lines(
    lines: List[str],
    *,
    drop_toc: bool,
    toc_mode: str,
    toc_min_hits: int,
    toc_window_lines: int,
    repeated: set[str],
) -> List[str]:
    filtered = strip_toc_region(
        lines,
        {
            "toc_stop_on_heading": True,
        },
    ) if (drop_toc and toc_mode == "auto") else lines
    cleaned: List[str] = []
    for ln in filtered:
        if not ln:
            continue
        if drop_toc and toc_mode != "off":
            if toc_mode == "strict" and _is_toc_line_strict(ln):
                continue
            if toc_mode == "auto" and (_is_toc_line(ln) or _is_toc_candidate(ln)):
                continue
        if ln in repeated:
            continue
        cleaned.append(ln)
    return cleaned


def _find_repeated_lines(items: Sequence[Dict]) -> set[str]:
    counts: Counter[str] = Counter()
    for it in items:
        for ln in _normalize_block_lines(it.get("text", "")):
            if 1 <= len(ln) <= 120:
                counts[ln] += 1
    if not counts:
        return set()
    threshold = max(2, int(len(items) * 0.5))
    return {ln for ln, c in counts.items() if c >= threshold}


def _build_units(
    blocks: List[Dict[str, str]],
    *,
    table_mode: str,
    max_tokens: int,
) -> List[Dict[str, str]]:
    units: List[Dict[str, str]] = []
    i = 0
    while i < len(blocks):
        blk = blocks[i]
        btype = blk["type"]
        text = blk["text"]

        if btype == "table":
            rows = _table_to_rows(text.splitlines(), table_mode)
            if not rows:
                i += 1
                continue
            for row in rows:
                if row.strip():
                    units.append({"type": "table", "text": row.strip()})
            i += 1
            continue

        if btype == "list" and units and units[-1]["type"] == "paragraph":
            units[-1]["text"] = (units[-1]["text"] + "\n" + text).strip()
            i += 1
            continue

        units.append({"type": btype, "text": text})
        i += 1

    final_units: List[Dict[str, str]] = []
    for unit in units:
        splits = _split_to_token_limit(unit["text"], max_tokens)
        for part in splits:
            if part.strip():
                final_units.append({"type": unit["type"], "text": part.strip()})
    return final_units


def chunk_structured_docx_items(
    items: Sequence[Dict],
    chunker_cfg: Dict,
    effective_max_tokens: int,
) -> List[Dict[str, object]]:
    """Return structured chunks with metadata preserved."""
    drop_toc = bool(chunker_cfg.get("drop_toc", True))
    toc_mode = str(chunker_cfg.get("toc_mode", "auto") or "auto").lower() if drop_toc else "off"
    toc_min_hits = int(chunker_cfg.get("toc_min_hits", 6) or 6)
    toc_window_lines = int(chunker_cfg.get("toc_window_lines", 20) or 20)
    drop_repeated = bool(chunker_cfg.get("drop_repeated_headers_footers", True))
    drop_admin = bool(chunker_cfg.get("drop_admin_sections", False))
    table_mode = str(chunker_cfg.get("table_mode", "row_kv") or "row_kv").lower()
    min_tokens = int(chunker_cfg.get("min_tokens", 0) or 0)

    repeated_lines = _find_repeated_lines(items) if drop_repeated else set()

    chunks: List[Dict[str, object]] = []
    for idx, item in enumerate(items, start=1):
        text = item.get("text") or ""
        meta = dict(item.get("metadata") or {})
        heading_path = meta.get("heading_path") or []
        section_heading = meta.get("section_heading")
        if drop_admin and section_heading and _looks_admin_heading(section_heading):
            continue

        blocks_raw = text.split("\n\n")
        cleaned_blocks: List[Dict[str, str]] = []
        for blk in blocks_raw:
            lines = _normalize_block_lines(blk)
            lines = _clean_lines(
                lines,
                drop_toc=drop_toc,
                toc_mode=toc_mode,
                toc_min_hits=toc_min_hits,
                toc_window_lines=toc_window_lines,
                repeated=repeated_lines,
            )
            if not lines:
                continue
            joined = "\n".join(lines).strip()
            if not joined:
                continue
            block_type = "list" if all(_is_bullet(ln) for ln in lines) else "paragraph"
            if any(("|" in ln or "\t" in ln) for ln in lines):
                block_type = "table"
            cleaned_blocks.append({"type": block_type, "text": joined})

        units = _build_units(cleaned_blocks, table_mode=table_mode, max_tokens=effective_max_tokens)
        for u_idx, unit in enumerate(units, start=1):
            if min_tokens > 0 and _estimate_tokens(unit["text"]) < min_tokens:
                continue
            chunk_meta = dict(meta)
            if heading_path:
                chunk_meta["heading_path"] = heading_path
            if section_heading:
                chunk_meta["section_heading"] = section_heading
            chunk_meta["section_index"] = idx
            chunk_meta["unit_type"] = unit["type"]
            chunks.append(
                {
                    "text": unit["text"],
                    "metadata": chunk_meta,
                }
            )
    return chunks
