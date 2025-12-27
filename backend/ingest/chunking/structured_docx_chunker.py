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
from typing import Dict, List, Optional, Sequence

from backend.ingest.chunking.toc_utils import strip_toc_region, is_toc_like
import os
import logging

STRUCTURED_CHUNK_DEBUG = (os.getenv("STRUCTURED_CHUNK_DEBUG") or "").lower() in {"1", "true", "on", "yes"}
_log = logging.getLogger(__name__)


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
    return [ln.rstrip() for ln in block.splitlines() if ln.strip()]


def _is_major_sop_heading(line: str) -> bool:
    return bool(re.match(r"^\s*sop\s*\d+\b", line, flags=re.IGNORECASE))


def _is_major_numeric_heading(line: str) -> bool:
    return bool(re.match(r"^\s*\d+\s*(?:[.)-]|\s)\s*\S", line))


def _is_sub_numeric_heading(line: str) -> bool:
    return bool(re.match(r"^\s*\d+\.\d+\b", line))


def _is_strong_numbered_heading(line: str) -> bool:
    return bool(re.match(r"^\s*(\d{1,3}(?:\.\d{1,3}){0,4}\.?)\s+.+$", line))


def _extract_numeric_heading_prefix(line: str) -> Optional[str]:
    m = re.match(r"^\s*(\d+\.\d+(?:\.\d+)*)\b", line or "")
    return m.group(1) if m else None


def _heading_from_meta(meta: Dict[str, object]) -> str:
    heading = meta.get("section_heading")
    if isinstance(heading, str) and heading.strip():
        return heading.strip()
    hpath = meta.get("heading_path")
    if isinstance(hpath, (list, tuple)) and hpath:
        parts = [str(x).strip() for x in hpath if str(x).strip()]
        return " > ".join(parts) if parts else ""
    return ""


def _ensure_heading_in_text(text: str, heading: str) -> str:
    if not heading:
        return text.strip()
    h = heading.strip()
    if text.strip().startswith(h):
        return text.strip()
    return f"{h}\n{text.strip()}"


def _split_block_with_heading(
    base_text: str,
    heading: str,
    max_tokens: int,
    overlap_tokens: int,
    min_block_tokens: int,
    sub_heading: str | None = None,
) -> List[str]:
    raw_parts = _split_to_token_limit(base_text, max_tokens)
    chunks: List[str] = []
    prev_tail: List[str] = []
    for idx, part in enumerate(raw_parts):
        body = part.strip()
        if not body:
            continue
        # add overlap below heading
        if idx > 0 and overlap_tokens > 0 and prev_tail:
            overlap = " ".join(prev_tail[-overlap_tokens:]).strip()
            if overlap:
                body = f"{overlap}\n{body}"
        body = _ensure_heading_in_text(body, heading)
        if sub_heading and not body.startswith(sub_heading) and sub_heading not in body.splitlines()[0]:
            body = f"{sub_heading}\n{body}"
        tokens = _estimate_tokens(body)
        if min_block_tokens and tokens < min_block_tokens and chunks:
            body_to_merge = body
            if heading and body.startswith(heading):
                body_to_merge = body[len(heading):].strip()
            if body_to_merge:
                chunks[-1] = (chunks[-1] + "\n" + body_to_merge).strip()
        else:
            chunks.append(body)
        # track tail without repeating heading
        body_no_heading = body
        if heading and body.startswith(heading):
            body_no_heading = body[len(heading):].strip()
        prev_tail = body_no_heading.split()
    return chunks


def _is_list_item(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[\-\*\u2022]|[0-9]+[.)])\s+", line))


def _is_headingish_line(line: str) -> bool:
    if not line:
        return False
    if len(line) > 120:
        return False
    if _is_list_item(line):
        return False
    if re.match(r"^\s*[0-9]+(\.[0-9]+)*\b", line):
        return True
    words = line.split()
    return len(words) <= 10 and (line.istitle() or line.isupper())


def _is_procedure_title(line: str) -> bool:
    if _extract_numeric_heading_prefix(line):
        return True
    if _is_strong_numbered_heading(line):
        return True
    ln = line.rstrip()
    if ln.endswith(":") and len(ln) <= 120 and re.search(r"[A-Za-z]", ln):
        letters = [ch for ch in ln if ch.isalpha()]
        if letters:
            upper_ratio = sum(1 for ch in letters if ch.isupper()) / float(len(letters))
            if upper_ratio >= 0.6:
                return True
    return False


def _split_into_procedures(text: str, skip_lines: set[str]) -> List[Dict[str, object]]:
    procedures: List[Dict[str, object]] = []
    current: Dict[str, object] = {"title": None, "lines": []}

    def start_new(title: Optional[str]) -> None:
        nonlocal current
        if current["lines"]:
            procedures.append(current)
        current = {"title": title, "lines": [] if title is None else [title]}

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            if current["lines"]:
                current["lines"].append("")
            continue
        if line.rstrip() in skip_lines:
            continue
        if _is_procedure_title(line):
            start_new(line)
            continue
        current["lines"].append(line)

    if current["lines"]:
        procedures.append(current)
    return procedures


def _prepend_context_lines(context_lines: List[str], content_lines: List[str]) -> List[str]:
    def norm(s: str) -> str:
        return " ".join((s or "").split()).strip().lower()

    merged: List[str] = []
    ctx = [ln for ln in context_lines if ln]
    content = [ln for ln in content_lines if ln]
    if ctx:
        if content and norm(ctx[0]) == norm(content[0]):
            merged.append(content[0])
            merged.extend(ctx[1:])
            merged.extend(content[1:])
        else:
            merged.extend(ctx)
            merged.extend(content)
    else:
        merged.extend(content)
    return merged


def _blocks_from_text(text: str, skip_lines: Optional[set[str]] = None) -> List[str]:
    blocks: List[str] = []
    skip_lines = skip_lines or set()
    buf: List[str] = []

    def flush_buffer():
        if buf:
            blocks.append("\n".join(buf))
            buf.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        if not line.strip():
            flush_buffer()
            continue
        if line.rstrip() in skip_lines:
            continue
        if _is_headingish_line(line) or _is_strong_numbered_heading(line):
            flush_buffer()
            blocks.append(line)
            continue
        if _is_list_item(line):
            flush_buffer()
            blocks.append(line)
            continue
        buf.append(line)
    flush_buffer()
    return blocks


def _pack_blocks(blocks: List[str], max_tokens: int) -> List[Dict[str, object]]:
    chunks: List[Dict[str, object]] = []
    buf: List[str] = []
    buf_tokens = 0
    part = 1
    for blk in blocks:
        t = _estimate_tokens(blk)
        if t >= max_tokens:
            # oversize block: split using existing splitter
            lines = blk.splitlines()
            if lines and _is_headingish_line(lines[0]) and len(lines) > 1:
                heading_line = lines[0]
                rest = "\n".join(lines[1:])
                pieces = _split_to_token_limit(rest, max_tokens)
                if pieces:
                    pieces[0] = f"{heading_line}\n{pieces[0]}".strip()
                    for j in range(1, len(pieces)):
                        if not pieces[j].strip():
                            continue
                        first_line = pieces[j].splitlines()[0].strip()
                        if first_line != heading_line:
                            pieces[j] = f"{heading_line}\n{pieces[j]}".strip()
            else:
                pieces = _split_to_token_limit(blk, max_tokens)
            if len(pieces) > 1 and len(pieces[0].splitlines()) == 1:
                first = pieces.pop(0)
                pieces[0] = f"{first}\n{pieces[0]}".strip()
            for piece in pieces:
                chunks.append({"text": piece, "is_split": True, "split_reason": "oversize_block", "split_part": part})
                part += 1
            continue
        if buf and buf_tokens + t > max_tokens:
            chunks.append(
                {"text": "\n".join(buf), "is_split": part > 1, "split_reason": "max_tokens" if part > 1 else None, "split_part": part}
            )
            part += 1
            buf = [blk]
            buf_tokens = t
        else:
            buf.append(blk)
            buf_tokens += t
    if buf:
        chunks.append({"text": "\n".join(buf), "is_split": len(chunks) > 0, "split_reason": "max_tokens" if len(chunks) > 0 else None, "split_part": len(chunks) + 1 if len(chunks) > 0 else 1})
    return chunks


def _merge_heading_only(chunks: List[Dict[str, object]]) -> List[Dict[str, object]]:
    merged: List[Dict[str, object]] = []
    i = 0
    while i < len(chunks):
        chunk = chunks[i]
        lines = [ln for ln in chunk.get("text", "").splitlines() if ln.strip()]
        if len(lines) <= 1 and i + 1 < len(chunks):
            nxt = chunks[i + 1]
            combined_text = (chunk.get("text", "").strip() + "\n" + nxt.get("text", "").strip()).strip()
            nxt = dict(nxt)
            nxt["text"] = combined_text
            nxt["is_split"] = True
            nxt["split_reason"] = nxt.get("split_reason") or chunk.get("split_reason") or "max_tokens"
            nxt["split_part"] = nxt.get("split_part", i + 2)
            chunks[i + 1] = nxt
            i += 1
            continue
        merged.append(chunk)
        i += 1
    return merged


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
    return units


def _explode_unit_on_strong_headings(text: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    for line in text.splitlines():
        ln = line.strip()
        if _is_strong_numbered_heading(ln):
            if buf:
                parts.append("\n".join(buf).strip())
                buf = []
            buf.append(ln)
            continue
        buf.append(line)
    if buf:
        parts.append("\n".join(buf).strip())
    return [p for p in parts if p]


def chunk_structured_docx_items(
    items: Sequence[Dict],
    chunker_cfg: Dict,
    effective_max_tokens: int,
) -> List[Dict[str, object]]:
    """Return structured chunks with metadata preserved, packed per major section."""
    min_tokens = int(chunker_cfg.get("min_tokens", 0) or 0)
    drop_toc = bool(chunker_cfg.get("drop_toc", False))

    groups: Dict[str | None, List[Dict[str, object]]] = {}
    order: List[str | None] = []
    # If no heading paths, attempt to infer majors from heading-like lines
    if not any((item.get("metadata") or {}).get("heading_path") for item in items):
        inferred: List[Dict[str, object]] = []
        for item in items:
            meta = item.get("metadata") or {}
            lines = (item.get("text") or "").splitlines()
            current_lines: List[str] = []
            current_heading: Optional[str] = None
            for ln in lines:
                ln_strip = ln.strip()
                is_sub_num = bool(re.match(r"^\s*\d+\.\d+", ln_strip))
                is_heading = (not is_sub_num and bool(re.match(r"^\s*\d+\b", ln_strip))) or bool(
                    re.match(r"^\s*sop\s*\d+", ln_strip, flags=re.IGNORECASE)
                )
                if is_heading:
                    if current_lines:
                        inferred.append(
                            {"text": "\n".join(current_lines), "metadata": {**meta, "heading_path": [current_heading] if current_heading else []}}
                        )
                    current_heading = ln_strip
                    current_lines = [ln_strip]
                else:
                    current_lines.append(ln)
            if current_lines:
                inferred.append(
                    {"text": "\n".join(current_lines), "metadata": {**meta, "heading_path": [current_heading] if current_heading else []}}
                )
        items = inferred

    for item in items:
        meta = item.get("metadata") or {}
        heading_path = meta.get("heading_path") or []
        major = heading_path[0] if heading_path else None
        if major not in groups:
            groups[major] = []
            order.append(major)
        groups[major].append(item)

    chunks: List[Dict[str, object]] = []
    for major in order:
        group_items = groups.get(major, [])
        if not group_items:
            continue
        if STRUCTURED_CHUNK_DEBUG:
            _log.info("STRUCTURED_CHUNK_DEBUG major=%s items=%d", major, len(group_items))
        group_blocks: List[str] = []
        numbering_prefix = None
        base_meta = group_items[0].get("metadata") or {}
        # detect repeated header/footer lines to skip
        header_pat = re.compile(r"(confidential|restricted|page\s+\d+\s+of\s+\d+)", re.IGNORECASE)
        line_counts: Counter[str] = Counter()
        for item in group_items:
            for ln in (item.get("text") or "").splitlines():
                line_counts[ln.rstrip()] += 1
        skip_lines = {
            ln
            for ln, cnt in line_counts.items()
            if cnt > 1
            and header_pat.search(ln)
        }
        if drop_toc:
            toc_lines = {ln for ln in line_counts.keys() if _is_toc_line(ln) or _is_toc_line_strict(ln)}
            skip_lines = skip_lines.union(toc_lines)

        numeric_prefixes: List[str] = []
        combined_lines: List[str] = []
        for item in group_items:
            meta = item.get("metadata") or {}
            if not numbering_prefix:
                numbering_prefix = meta.get("numbering_prefix_of_section") or meta.get("numbering_prefix")
            prefix_meta = meta.get("numbering_prefix_of_section") or meta.get("numbering_prefix")
            if prefix_meta and "." in str(prefix_meta):
                numeric_prefixes.append(str(prefix_meta))
            text_val = item.get("text") or ""
            numeric_prefixes.extend(
                [p for p in (_extract_numeric_heading_prefix(ln.strip()) for ln in text_val.splitlines()) if p]
            )
            combined_lines.extend(text_val.splitlines())
            combined_lines.append("")
            group_blocks.extend(_blocks_from_text(text_val, skip_lines=skip_lines))

        section_range = None
        seen = []
        for p in numeric_prefixes:
            if p not in seen:
                seen.append(p)
        if len(seen) >= 2:
            section_range = f"{seen[0]}-{seen[-1]}"
        elif len(seen) == 1:
            section_range = seen[0]

        procedures = _split_into_procedures("\n".join(combined_lines), skip_lines)
        has_proc_titles = any(p.get("title") for p in procedures)

        if not has_proc_titles:
            packed = _pack_blocks(group_blocks, effective_max_tokens)
            packed = _merge_heading_only(packed)
            total_parts = len(packed)
            for idx, entry in enumerate(packed, start=1):
                text_val = entry["text"]
                if min_tokens > 0 and _estimate_tokens(text_val) < min_tokens:
                    continue
                meta: Dict[str, object] = {}
                meta["major_section"] = major
                meta["heading_path"] = [major] if major else []
                meta["section_heading"] = major
                meta["heading_level_of_section"] = 1 if major else None
                if numbering_prefix:
                    meta["numbering_prefix_of_section"] = numbering_prefix
                if section_range:
                    meta["section_range"] = section_range
                meta["is_split"] = entry.get("is_split", False) or total_parts > 1
                meta["split_reason"] = entry.get("split_reason") if total_parts > 1 else None
                meta["split_part"] = entry.get("split_part", idx)
                meta["source"] = base_meta.get("source")
                meta["content_type"] = base_meta.get("content_type")
                chunks.append({"text": text_val, "metadata": meta})
            continue

        chunk_lines: List[str] = []
        chunk_prefixes: List[str] = []
        chunk_proc_titles: List[str] = []
        chunk_seq = 0

        def flush_chunk(prefixes_override: Optional[List[str]] = None, split_reason: Optional[str] = None, split_part: Optional[int] = None) -> None:
            nonlocal chunk_lines, chunk_prefixes, chunk_seq, chunk_proc_titles
            if not chunk_lines:
                return
            text_val = "\n".join(chunk_lines).strip()
            if not text_val:
                chunk_lines = []
                chunk_prefixes = []
                chunk_proc_titles = []
                return
            lines_clean = [ln for ln in chunk_lines if ln.strip()]
            if major and lines_clean == [major]:
                chunk_lines = []
                chunk_prefixes = []
                chunk_proc_titles = []
                return
            if min_tokens > 0 and _estimate_tokens(text_val) < min_tokens:
                chunk_lines = []
                chunk_prefixes = []
                chunk_proc_titles = []
                return
            chunk_seq += 1
            prefixes = prefixes_override if prefixes_override is not None else chunk_prefixes
            sr = None
            clean_prefixes = [p for p in prefixes if p]
            if len(clean_prefixes) >= 2:
                sr = f"{clean_prefixes[0]}-{clean_prefixes[-1]}"
            elif len(clean_prefixes) == 1:
                sr = clean_prefixes[0]
            meta: Dict[str, object] = {
                "major_section": major,
                "heading_path": [major] if major else [],
                "section_heading": major,
                "heading_level_of_section": 1 if major else None,
                "is_split": chunk_seq > 1,
                "split_reason": split_reason,
                "split_part": split_part or chunk_seq,
                "source": base_meta.get("source"),
                "content_type": base_meta.get("content_type"),
            }
            if numbering_prefix:
                meta["numbering_prefix_of_section"] = numbering_prefix
            if sr:
                meta["section_range"] = sr
            if chunk_proc_titles:
                meta["procedure_title_first"] = chunk_proc_titles[0]
                meta["procedure_title_last"] = chunk_proc_titles[-1]
            if STRUCTURED_CHUNK_DEBUG:
                lines_cnt = len([ln for ln in text_val.splitlines() if ln.strip()])
                _log.info(
                    "STRUCTURED_CHUNK_DEBUG emit major=%s proc_first=%s lines=%d tokens=%d title_only=%s split_reason=%s",
                    major,
                    chunk_proc_titles[0] if chunk_proc_titles else None,
                    lines_cnt,
                    _estimate_tokens(text_val),
                    lines_cnt <= 1,
                    split_reason,
                )
            chunks.append({"text": text_val, "metadata": meta})
            chunk_lines = []
            chunk_prefixes = []
            chunk_proc_titles = []

        def split_procedure(proc: Dict[str, object]) -> List[str]:
            title_line = proc.get("title") or (proc.get("lines")[0] if proc.get("lines") else "")
            proc_lines: List[str] = proc.get("lines") or []
            blocks: List[str] = []
            buf: List[str] = []
            for ln in proc_lines:
                if not ln.strip():
                    if buf:
                        blocks.append("\n".join(buf).strip())
                        buf = []
                    continue
                if _is_list_item(ln) and buf:
                    blocks.append("\n".join(buf).strip())
                    buf = [ln]
                    continue
                buf.append(ln)
            if buf:
                blocks.append("\n".join(buf).strip())

            padding_tokens = _estimate_tokens((major or "") + " " + (title_line or "")) + 4
            inner_max = max(8, effective_max_tokens - padding_tokens)
            packed_parts = _pack_blocks(blocks, inner_max)
            texts: List[str] = []
            for part in packed_parts:
                part_text = part["text"]
                part_lines = part_text.splitlines()
                body = part_text.strip()
                if title_line and part_lines and part_lines[0].strip() == title_line.strip():
                    body = "\n".join(part_lines[1:]).strip()
                prefix_lines: List[str] = []
                if major:
                    prefix_lines.append(major)
                if title_line:
                    prefix_lines.append(title_line.strip())
                if body:
                    prefix_lines.append(body)
                final = "\n".join(prefix_lines).strip()
                texts.append(final)
            return texts

        for proc in procedures:
            proc_lines = proc.get("lines") or []
            body_only = [ln for ln in proc_lines[1:] if ln.strip()]
            if not any(ln.strip() for ln in proc_lines):
                continue
            if proc.get("title") and not body_only:
                continue
            proc_text = "\n".join(proc_lines).strip()
            if not proc_text:
                continue
            proc_prefix = _extract_numeric_heading_prefix(proc.get("title") or "")

            if not chunk_lines and major:
                chunk_lines.append(major)

            candidate_lines = list(chunk_lines) + [proc_text]
            if chunk_lines and _estimate_tokens("\n".join(candidate_lines)) > effective_max_tokens:
                flush_chunk(split_reason="max_tokens")
                if major:
                    chunk_lines.append(major)
                candidate_lines = list(chunk_lines) + [proc_text]

            if _estimate_tokens("\n".join(candidate_lines)) > effective_max_tokens:
                flush_chunk(split_reason="max_tokens")
                parts = split_procedure(proc)
                part_idx = 1
                for ptxt in parts:
                    prefixes_for_chunk = [proc_prefix] if proc_prefix else []
                    sr_local = None
                    if prefixes_for_chunk:
                        sr_local = prefixes_for_chunk[0]
                    meta: Dict[str, object] = {
                        "major_section": major,
                        "heading_path": [major] if major else [],
                        "section_heading": major,
                        "heading_level_of_section": 1 if major else None,
                        "is_split": True,
                        "split_reason": "oversize_procedure",
                        "split_part": part_idx,
                        "source": base_meta.get("source"),
                        "content_type": base_meta.get("content_type"),
                    }
                    if numbering_prefix:
                        meta["numbering_prefix_of_section"] = numbering_prefix
                    if sr_local:
                        meta["section_range"] = sr_local
                    if proc.get("title"):
                        meta["procedure_title_first"] = proc.get("title")
                        meta["procedure_title_last"] = proc.get("title")
                    if min_tokens <= 0 or _estimate_tokens(ptxt) >= min_tokens:
                        chunks.append({"text": ptxt, "metadata": meta})
                    part_idx += 1
                continue

            context_lines = []
            if major:
                context_lines.append(major)
            if proc.get("title"):
                context_lines.append(proc.get("title"))
            merged_lines = _prepend_context_lines(context_lines, [proc_text])
            if not chunk_lines:
                chunk_lines.extend(merged_lines)
            else:
                chunk_lines.append("\n".join(merged_lines))
            if proc_prefix:
                chunk_prefixes.append(proc_prefix)
            if proc.get("title"):
                chunk_proc_titles.append(proc.get("title"))

        flush_chunk()
        if STRUCTURED_CHUNK_DEBUG:
            _log.info(
                "STRUCTURED_CHUNK_DEBUG major=%s chunks_emitted=%d",
                major,
                len([c for c in chunks if (c.get("metadata") or {}).get("major_section") == major]),
            )

    if not chunks:
        for item in items:
            meta = item.get("metadata") or {}
            text = item.get("text") or ""
            tokens = _estimate_tokens(text)
            if tokens > effective_max_tokens:
                parts = _split_to_token_limit(text, effective_max_tokens)
                for i, part in enumerate(parts, start=1):
                    chunks.append(
                        {
                            "text": part,
                            "metadata": {**meta, "is_split": True, "split_reason": "oversize_block", "split_part": i},
                        }
                    )
            else:
                chunks.append({"text": text, "metadata": meta})
    return chunks
