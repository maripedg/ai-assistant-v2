"""Chunk DOCX items by TOC level-1 sections or numeric heading boundaries.

Step 1: standalone module (not wired to pipeline).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Tuple

from backend.ingest.chunking.toc_utils import strip_toc_region

DOCX_TOC_DEBUG = (os.getenv("DOCX_TOC_DEBUG") or "").lower() in {"1", "true", "on", "yes"}
DOCX_SECTION_CHUNK_DEBUG = (os.getenv("DOCX_SECTION_CHUNK_DEBUG") or "").lower() in {"1", "true", "on", "yes"}
_log = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    return max(0, int(round(len(text) / 4))) if text else 0


def _parse_toc_level1(lines: List[str]) -> List[Tuple[str, str]]:
    entries: List[Tuple[str, str]] = []
    pat = re.compile(r"^\s*(\d+)\.?\s+(.*)$")
    for ln in lines:
        ln_norm = ln.replace("\t", " ")
        ln_norm = re.sub(r"\.{3,}", " ", ln_norm)
        ln_norm = " ".join(ln_norm.replace("–", "-").replace("—", "-").split())
        m = pat.match(ln_norm)
        if not m:
            continue
        num = m.group(1)
        if "." in num:
            continue  # only level-1
        body = m.group(2) or ""
        # remove trailing page number
        body = re.sub(r"\s+\d{1,4}$", "", body).strip()
        body_norm = " ".join(body.replace("–", "-").replace("—", "-").split())
        if not body_norm:
            continue
        entries.append((num, body_norm))
    return entries


def _strip_toc_items(items: List[Dict]) -> Tuple[List[Dict], List[str], Tuple[int, int] | None, List[str]]:
    all_lines: List[str] = []
    for it in items:
        all_lines.extend((it.get("text") or "").splitlines())
    kept = strip_toc_region(all_lines, {"toc_stop_on_heading": True})
    keep_set = set(kept)
    removed: List[str] = []
    new_items: List[Dict] = []
    start_idx = None
    end_idx = None
    raw_toc_lines: List[str] = []
    non_toc_run = 0
    toc_started = False
    for it in items:
        lines = (it.get("text") or "").splitlines()
        idx = items.index(it)
        if start_idx is None and any(ln.strip().lower() == "table of contents" for ln in lines):
            start_idx = idx
            toc_started = True
        meta = it.get("metadata") or {}
        lvl = meta.get("heading_level_of_section")
        heading = meta.get("section_heading") or ""
        if toc_started and end_idx is None and idx > (start_idx or 0):
            if lvl == 1 and heading and heading.lower() != "table of contents":
                end_idx = idx
        out_lines = []
        for ln in lines:
            if ln in keep_set:
                keep_set.remove(ln)
                out_lines.append(ln)
            else:
                removed.append(ln)
                if toc_started and end_idx is None:
                    raw_toc_lines.append(ln)
        txt = "\n".join(out_lines).strip()
        if txt:
            new_items.append({"text": txt, "metadata": dict(it.get("metadata") or {})})
        if toc_started and not raw_toc_lines:
            non_toc_run += 1
            if non_toc_run >= 5 and end_idx is None:
                end_idx = idx
    bounds = (start_idx, end_idx) if start_idx is not None else None
    return new_items, removed, bounds, raw_toc_lines


def _detect_toc_entries(items: List[Dict]) -> List[Tuple[str, str]]:
    lines = []
    for it in items:
        lines.extend((it.get("text") or "").splitlines())
    entries = _parse_toc_level1(lines)
    return entries


def _normalize_title(s: str) -> str:
    return " ".join((s or "").replace("–", "-").replace("—", "-").split()).strip().lower()


def _split_by_titles(items: List[Dict], toc_entries: List[Tuple[str, str]]) -> List[Dict]:
    if not toc_entries:
        return []
    normalized_targets = [(_normalize_title(t[1]), t[0], t[1]) for t in toc_entries]
    chunks: List[Dict] = []
    current_lines: List[str] = []
    current_meta: Dict[str, object] = {}
    idx = 0

    def flush():
        nonlocal current_lines, current_meta
        if not current_lines:
            return
        text = "\n".join(current_lines).strip()
        if text:
            chunks.append({"text": text, "metadata": dict(current_meta)})
        current_lines = []
        current_meta = {}

    for it in items:
        for ln in (it.get("text") or "").splitlines():
            norm = _normalize_title(ln)
            if idx < len(normalized_targets) and norm.startswith(normalized_targets[idx][0]):
                if current_lines:
                    if DOCX_SECTION_CHUNK_DEBUG:
                        prev = current_meta.get("section_title")
                        _log.info(
                            "DOCX_SECTION_CHUNK_DEBUG boundary: closing section=%s opening section=%s",
                            prev,
                            normalized_targets[idx][1],
                        )
                    flush()
                section_num = normalized_targets[idx][1]
                section_title = normalized_targets[idx][2]
                header = f"Section: {section_num}. {section_title}"
                current_lines.append(header)
                current_meta = dict(it.get("metadata") or {})
                current_meta.update(
                    {"section_number": section_num, "section_title": section_title, "section_strategy": "TOC_LEVEL1"}
                )
                idx += 1
            current_lines.append(ln)
    flush()
    return [c for c in chunks if len([ln for ln in c["text"].splitlines() if ln.strip()]) > 1]


def _split_inline_level1(items: List[Dict]) -> List[Dict]:
    pat = re.compile(r"^\s*(\d+)\s*[\.\)]\s+(.*)$")
    chunks: List[Dict] = []
    current_lines: List[str] = []
    current_meta: Dict[str, object] = {}

    def flush():
        nonlocal current_lines, current_meta
        if not current_lines:
            return
        txt = "\n".join(current_lines).strip()
        if txt:
            chunks.append({"text": txt, "metadata": dict(current_meta)})
        current_lines = []
        current_meta = {}

    for it in items:
        for ln in (it.get("text") or "").splitlines():
            m = pat.match(ln)
            if m:
                if current_lines:
                    if DOCX_SECTION_CHUNK_DEBUG:
                        _log.info(
                            "DOCX_SECTION_CHUNK_DEBUG boundary: closing section=%s opening section=%s",
                            current_meta.get("section_number"),
                            m.group(1),
                        )
                    flush()
                num = m.group(1)
                title = m.group(2)
                header = f"Section: {num}. {title}"
                current_lines.append(header)
                current_meta = dict(it.get("metadata") or {})
                current_meta.update({"section_number": num, "section_title": title, "section_strategy": "INLINE_LEVEL1"})
            current_lines.append(ln)
    flush()
    return [c for c in chunks if len([ln for ln in c["text"].splitlines() if ln.strip()]) > 1]


def _split_num_prefix_major(items: List[Dict]) -> List[Dict]:
    chunks: List[Dict] = []
    strategy = "NUM_PREFIX_MAJOR"
    preamble_lines: List[str] = []
    preamble_meta: Dict[str, object] = {}
    preamble_emitted = False
    current_major: str | None = None
    current_lines: List[str] = []
    current_meta: Dict[str, object] = {}

    def _is_heading(item: Dict) -> bool:
        meta = item.get("metadata") or {}
        lvl = meta.get("heading_level_of_section") if meta.get("heading_level_of_section") is not None else meta.get("heading_level")
        heading_txt = (meta.get("section_heading") or meta.get("heading_text") or "").strip()
        first_line = ((item.get("text") or "").splitlines() or [""])[0].strip()
        if lvl is None:
            return False
        if heading_txt:
            return first_line.lower().startswith(heading_txt.lower())
        return bool(first_line)

    def _item_major(item: Dict) -> Tuple[int | None, str | None, str | None]:
        meta = item.get("metadata") or {}
        raw_prefix = meta.get("num_prefix") or meta.get("numbering_prefix") or meta.get("numbering_prefix_of_section")
        chosen_key = None
        if meta.get("num_prefix") is not None:
            chosen_key = "num_prefix"
        elif meta.get("numbering_prefix") is not None:
            chosen_key = "numbering_prefix"
        elif meta.get("numbering_prefix_of_section") is not None:
            chosen_key = "numbering_prefix_of_section"
        if raw_prefix is None:
            return None, None, None
        major_part = str(raw_prefix).split(".", 1)[0].strip()
        try:
            major_int = int(major_part)
        except Exception:
            major_int = None
        return major_int, str(raw_prefix), chosen_key

    def _is_major_boundary(item: Dict) -> bool:
        meta = item.get("metadata") or {}
        if not _is_heading(item):
            return False
        major, raw_prefix, chosen_key = _item_major(item)
        if major is None:
            return False
        outline_level = meta.get("outline_level")
        lvl = meta.get("heading_level_of_section")
        num_prefix = raw_prefix
        if outline_level is not None:
            return outline_level == 0
        if lvl is not None:
            return lvl == 1 and "." not in str(num_prefix)
        return "." not in str(num_prefix)

    def _emit(lines: List[str], base_meta: Dict[str, object], major: str | None) -> None:
        if not lines:
            return
        txt = "\n".join(lines).strip()
        non_empty_lines = [ln for ln in txt.splitlines() if ln.strip()]
        if not txt or len(non_empty_lines) <= 1:
            return
        meta = dict(base_meta or {})
        meta.update({"section_number": major, "section_strategy": strategy if major is not None else "PREAMBLE"})
        if DOCX_SECTION_CHUNK_DEBUG:
            _log.info(
                "DOCX_SECTION_CHUNK_DEBUG emit major=%s lines=%d approx_tokens=%d",
                major,
                len(non_empty_lines),
                _estimate_tokens(txt),
            )
        chunks.append({"text": txt, "metadata": meta})

    for it in items:
        if DOCX_SECTION_CHUNK_DEBUG and _is_heading(it):
            maj, raw_pref, key_used = _item_major(it)
            _log.info(
                "DOCX_SECTION_CHUNK_DEBUG heading seen prefix_key=%s raw_prefix=%s major=%s heading_level=%s outline_level=%s",
                key_used,
                raw_pref,
                maj,
                (it.get("metadata") or {}).get("heading_level_of_section"),
                (it.get("metadata") or {}).get("outline_level"),
            )
        if _is_major_boundary(it):
            new_major, raw_prefix, chosen_key = _item_major(it)
            heading_lines = (it.get("text") or "").splitlines()
            heading_line = heading_lines[0].strip() if heading_lines else ""
            header = f"Section: {new_major}" if new_major is not None else "Section:"
            if heading_line:
                header = f"{header}. {heading_line}"
            if DOCX_SECTION_CHUNK_DEBUG:
                _log.info(
                    "DOCX_SECTION_CHUNK_DEBUG heading boundary prefix_key=%s raw_prefix=%s major=%s heading_level=%s outline_level=%s",
                    chosen_key,
                    raw_prefix,
                    new_major,
                    (it.get("metadata") or {}).get("heading_level_of_section"),
                    (it.get("metadata") or {}).get("outline_level"),
                )
            if current_major is None:
                if preamble_lines:
                    _emit(preamble_lines, preamble_meta, None)
                    preamble_lines = []
                    preamble_meta = {}
                    preamble_emitted = True
            elif new_major != current_major:
                if DOCX_SECTION_CHUNK_DEBUG:
                    _log.info(
                        "DOCX_SECTION_CHUNK_DEBUG boundary: closing major=%s opening major=%s",
                        current_major,
                        new_major,
                    )
                _emit(current_lines, current_meta, current_major)
                current_lines = []
            current_major = new_major
            current_meta = dict(it.get("metadata") or {})
            current_lines = [header] if header else []
            for ln in heading_lines:
                current_lines.append(ln)
            continue

        if current_major is None:
            if not preamble_meta:
                preamble_meta = dict(it.get("metadata") or {})
            preamble_lines.extend((it.get("text") or "").splitlines())
            continue

        current_lines.extend((it.get("text") or "").splitlines())

    if current_major is not None:
        _emit(current_lines, current_meta, current_major)
    if preamble_lines and not preamble_emitted:
        _emit(preamble_lines, preamble_meta, None)
    return chunks


def _split_heading1(items: List[Dict]) -> List[Dict]:
    chunks: List[Dict] = []
    current_lines: List[str] = []
    current_meta: Dict[str, object] = {}

    def flush():
        nonlocal current_lines, current_meta
        if not current_lines:
            return
        txt = "\n".join(current_lines).strip()
        if txt:
            chunks.append({"text": txt, "metadata": dict(current_meta)})
        current_lines = []
        current_meta = {}

    for it in items:
        meta = it.get("metadata") or {}
        h = meta.get("section_heading") or ""
        lvl = meta.get("heading_level_of_section")
        lines = (it.get("text") or "").splitlines()
        if lvl == 1 and h:
            if current_lines:
                if DOCX_SECTION_CHUNK_DEBUG:
                    _log.info(
                        "DOCX_SECTION_CHUNK_DEBUG boundary: closing section=%s opening section=%s",
                        current_meta.get("section_title"),
                        h,
                    )
                flush()
            header = f"Section: {h}"
            current_lines.append(header)
            current_meta = dict(meta)
            current_meta.update({"section_number": None, "section_title": h, "section_strategy": "HEADING1"})
        current_lines.extend(lines)
    flush()
    return [c for c in chunks if len([ln for ln in c["text"].splitlines() if ln.strip()]) > 1]


def _split_to_limit(text: str, max_tokens: int, header: str) -> List[str]:
    lines = text.splitlines()
    chunks: List[str] = []
    buf: List[str] = [header]
    for ln in lines:
        candidate = "\n".join(buf + [ln]).strip()
        if _estimate_tokens(candidate) > max_tokens and len(buf) > 1:
            chunks.append("\n".join(buf).strip())
            buf = [header, ln]
        else:
            buf.append(ln)
    if buf:
        chunks.append("\n".join(buf).strip())
    return chunks


def chunk_docx_toc_sections(items: List[Dict], *, cfg: Dict, source_meta: Dict | None = None) -> List[Dict]:
    effective_max = int(cfg.get("effective_max_tokens") or cfg.get("max_tokens") or 512)
    stripped_items, removed_lines, bounds, raw_toc_lines = _strip_toc_items(items)
    toc_entries = _parse_toc_level1(raw_toc_lines)
    if DOCX_TOC_DEBUG and bounds:
        _log.info(
            "DOCX_TOC_DEBUG TOC detected: entries=%d removed_items=%d remaining_items=%d bounds=%s",
            len(toc_entries),
            len(removed_lines),
            len(stripped_items),
            f"{bounds[0]}-{bounds[1]}",
        )
        for ln in raw_toc_lines[:5]:
            snippet = (ln[:80] + "...") if len(ln) > 80 else ln
            _log.info("DOCX_TOC_DEBUG TOC raw line: %s", snippet)
        for num, title in toc_entries[:3]:
            _log.info("DOCX_TOC_DEBUG TOC L1 sample: %s -> %s", num, title)

    # Strategy selection
    usable_toc = toc_entries if len(toc_entries) >= 3 else []
    if usable_toc:
        chunks = _split_by_titles(stripped_items, usable_toc)
        strategy = "TOC_LEVEL1"
    else:
        num_chunks = _split_num_prefix_major(items)
        if num_chunks:
            chunks = num_chunks
            strategy = "NUM_PREFIX_MAJOR"
        else:
            inline_chunks = _split_inline_level1(items)
            if inline_chunks:
                chunks = inline_chunks
                strategy = "INLINE_LEVEL1"
            else:
                chunks = _split_heading1(items)
                strategy = "HEADING1"
        if DOCX_TOC_DEBUG and toc_entries and len(toc_entries) < 3:
            _log.info("DOCX_TOC_DEBUG TOC parse insufficient (entries=%d); falling back to %s", len(toc_entries), strategy)
    if DOCX_SECTION_CHUNK_DEBUG:
        _log.info("DOCX_SECTION_CHUNK_DEBUG Strategy=%s", strategy)

    final_chunks: List[Dict] = []
    for ch in chunks:
        meta = dict(source_meta or {})
        meta.update(ch.get("metadata") or {})
        text = ch.get("text") or ""
        header_line = text.splitlines()[0] if text else ""
        if _estimate_tokens(text) > effective_max:
            parts = _split_to_limit("\n".join(text.splitlines()[1:]), effective_max, header_line)
            for idx, part in enumerate(parts, start=1):
                final_chunks.append(
                    {"text": part, "metadata": {**meta, "section_strategy": strategy, "split_part": idx, "is_split": True}}
                )
        else:
            final_chunks.append({"text": text, "metadata": {**meta, "section_strategy": strategy, "is_split": False}})
    if DOCX_SECTION_CHUNK_DEBUG:
        for ch in final_chunks[:20]:
            lines_cnt = len([ln for ln in ch["text"].splitlines() if ln.strip()])
            _log.info(
                "DOCX_SECTION_CHUNK_DEBUG Strategy=%s lines=%d approx_tokens=%d title_only=%s",
                strategy,
                lines_cnt,
                _estimate_tokens(ch["text"]),
                lines_cnt <= 1,
            )
    return final_chunks
