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


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "on", "yes", "y", "t"}


def _estimate_tokens(text: str) -> int:
    return max(0, int(round(len(text) / 4))) if text else 0


def _normalize_heading_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _compile_heading_regex(patterns: List[str]) -> List[re.Pattern]:
    compiled: List[re.Pattern] = []
    for pat in patterns or []:
        if not pat:
            continue
        try:
            compiled.append(re.compile(pat, flags=re.IGNORECASE))
        except re.error:
            continue
    return compiled


def _matches_heading_regex(text: str, regexes: List[re.Pattern]) -> bool:
    if not text or not regexes:
        return False
    return any(rx.search(text) for rx in regexes)


def _current_heading_from_meta(meta: Dict[str, object]) -> str:
    heading = meta.get("section_heading")
    if isinstance(heading, str) and heading.strip():
        return heading.strip()
    hpath = meta.get("heading_path")
    if isinstance(hpath, (list, tuple)) and hpath:
        last = hpath[-1]
        if isinstance(last, str) and last.strip():
            return last.strip()
        if last is not None:
            return str(last).strip()
    return ""


def _figure_placeholder(figure_id: str | None) -> str:
    if not figure_id:
        return "[FIGURE:?]"
    return f"[FIGURE:{figure_id}]"


def _figure_from_item(it: Dict) -> Dict | None:
    meta = dict(it.get("metadata") or {})
    if meta.get("block_type") != "image":
        return None
    figure_id = meta.get("figure_id") or (it.get("text") or "").strip()
    image_ref = meta.get("image_ref")
    return {"figure_id": figure_id, "image_ref": image_ref, "meta": meta}


def _parse_sop_heading(text: str) -> Tuple[str | None, str | None]:
    m = re.match(r"^\s*sop\s*(\d+)\s*[:\-]?\s*(.*)$", text or "", flags=re.IGNORECASE)
    if not m:
        return None, None
    num = m.group(1)
    title = m.group(2).strip() if m.group(2) else f"SOP{num}"
    if not title.lower().startswith("sop"):
        title = f"SOP{num}: {title}".strip()
    return num, title


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
        if (it.get("metadata") or {}).get("block_type") == "image":
            continue
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
        if (it.get("metadata") or {}).get("block_type") == "image":
            new_items.append({"text": it.get("text") or "", "metadata": dict(it.get("metadata") or {})})
            continue
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
        if (it.get("metadata") or {}).get("block_type") == "image":
            continue
        lines.extend((it.get("text") or "").splitlines())
    entries = _parse_toc_level1(lines)
    return entries


def _normalize_title(s: str) -> str:
    return " ".join((s or "").replace("–", "-").replace("—", "-").split()).strip().lower()


def _split_by_titles(items: List[Dict], toc_entries: List[Tuple[str, str]], inline_placeholders: bool) -> List[Dict]:
    if not toc_entries:
        return []
    normalized_targets = [(_normalize_title(t[1]), t[0], t[1]) for t in toc_entries]
    chunks: List[Dict] = []
    current_lines: List[str] = []
    current_meta: Dict[str, object] = {}
    current_figures: List[Dict] = []
    idx = 0

    def flush():
        nonlocal current_lines, current_meta, current_figures
        if not current_lines:
            return
        text = "\n".join(current_lines).strip()
        if not text and current_figures:
            placeholders = [_figure_placeholder(f.get("figure_id")) for f in current_figures if f.get("figure_id")]
            text = "\n".join(placeholders) if inline_placeholders and placeholders else "Figure reference"
        if text:
            chunks.append({"text": text, "metadata": dict(current_meta), "figures": list(current_figures)})
        current_lines = []
        current_meta = {}
        current_figures = []

    for it in items:
        fig = _figure_from_item(it)
        if fig:
            if not current_meta:
                current_meta = dict(it.get("metadata") or {})
            current_figures.append(fig)
            if inline_placeholders:
                current_lines.append(_figure_placeholder(fig.get("figure_id")))
            continue
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
    return [c for c in chunks if len([ln for ln in c["text"].splitlines() if ln.strip()]) > 1 or (c.get("figures") or [])]


def _split_inline_level1(items: List[Dict], inline_placeholders: bool) -> List[Dict]:
    pat = re.compile(r"^\s*(\d+)\s*[\.\)]\s+(.*)$")
    chunks: List[Dict] = []
    current_lines: List[str] = []
    current_meta: Dict[str, object] = {}
    current_figures: List[Dict] = []

    def flush():
        nonlocal current_lines, current_meta, current_figures
        if not current_lines:
            return
        txt = "\n".join(current_lines).strip()
        if not txt and current_figures:
            placeholders = [_figure_placeholder(f.get("figure_id")) for f in current_figures if f.get("figure_id")]
            txt = "\n".join(placeholders) if inline_placeholders and placeholders else "Figure reference"
        if txt:
            chunks.append({"text": txt, "metadata": dict(current_meta), "figures": list(current_figures)})
        current_lines = []
        current_meta = {}
        current_figures = []

    for it in items:
        fig = _figure_from_item(it)
        if fig:
            if not current_meta:
                current_meta = dict(it.get("metadata") or {})
            current_figures.append(fig)
            if inline_placeholders:
                current_lines.append(_figure_placeholder(fig.get("figure_id")))
            continue
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
    return [c for c in chunks if len([ln for ln in c["text"].splitlines() if ln.strip()]) > 1 or (c.get("figures") or [])]


def _split_num_prefix_major(items: List[Dict], inline_placeholders: bool) -> List[Dict]:
    chunks: List[Dict] = []
    strategy = "NUM_PREFIX_MAJOR"
    preamble_lines: List[str] = []
    preamble_meta: Dict[str, object] = {}
    preamble_emitted = False
    current_major: str | None = None
    current_lines: List[str] = []
    current_meta: Dict[str, object] = {}
    preamble_figures: List[Dict] = []
    current_figures: List[Dict] = []

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

    def _is_numeric_major_boundary(item: Dict) -> bool:
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

    def _emit(lines: List[str], base_meta: Dict[str, object], major: str | None, figures: List[Dict]) -> None:
        if not lines:
            return
        txt = "\n".join(lines).strip()
        non_empty_lines = [ln for ln in txt.splitlines() if ln.strip()]
        if not txt and figures:
            if inline_placeholders:
                txt = "\n".join([_figure_placeholder(f.get("figure_id")) for f in figures if f.get("figure_id")]) or "[FIGURE]"
            else:
                txt = "Figure reference"
            non_empty_lines = [ln for ln in txt.splitlines() if ln.strip()]
        if (not txt or len(non_empty_lines) <= 1) and not figures:
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
        chunks.append({"text": txt, "metadata": meta, "figures": list(figures or [])})

    for it in items:
        fig = _figure_from_item(it)
        if fig:
            target_lines = preamble_lines if current_major is None else current_lines
            target_figs = preamble_figures if current_major is None else current_figures
            target_figs.append(fig)
            if current_major is None and not preamble_meta:
                preamble_meta = dict(it.get("metadata") or {})
            if current_major is not None and not current_meta:
                current_meta = dict(it.get("metadata") or {})
            if inline_placeholders:
                target_lines.append(_figure_placeholder(fig.get("figure_id")))
            continue

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
        heading_lines = (it.get("text") or "").splitlines()
        heading_line = heading_lines[0].strip() if heading_lines else ""
        sop_num, sop_title = _parse_sop_heading(heading_line)
        numeric_boundary = _is_numeric_major_boundary(it)
        should_open_new = bool(sop_num) or (not current_meta.get("procedure_title") and numeric_boundary)
        if should_open_new:
            new_major, raw_prefix, chosen_key = _item_major(it)
            if sop_num:
                new_major = sop_num or new_major
            header = f"Section: {new_major}" if new_major is not None else "Section:"
            if sop_title:
                header = f"Procedure: {sop_title}"
                new_major = sop_num or new_major
            elif heading_line:
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
                    _emit(preamble_lines, preamble_meta, None, preamble_figures)
                    preamble_lines = []
                    preamble_meta = {}
                    preamble_emitted = True
                    preamble_figures = []
            elif new_major != current_major:
                if DOCX_SECTION_CHUNK_DEBUG:
                    _log.info(
                        "DOCX_SECTION_CHUNK_DEBUG boundary: closing major=%s opening major=%s",
                        current_major,
                        new_major,
                    )
                _emit(current_lines, current_meta, current_major, current_figures)
                current_lines = []
                current_figures = []
            current_major = new_major
            current_meta = dict(it.get("metadata") or {})
            if sop_title:
                current_meta["procedure_title"] = sop_title
                if sop_num:
                    current_meta["procedure_number"] = sop_num
                current_meta.setdefault("section_heading", sop_title)
                current_meta.setdefault("section_title", sop_title)
                current_meta.setdefault("section_number", sop_num)
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
        _emit(current_lines, current_meta, current_major, current_figures)
    if preamble_lines and not preamble_emitted:
        _emit(preamble_lines, preamble_meta, None, preamble_figures)
    return chunks


def _split_heading1(items: List[Dict], inline_placeholders: bool) -> List[Dict]:
    chunks: List[Dict] = []
    current_lines: List[str] = []
    current_meta: Dict[str, object] = {}
    current_figures: List[Dict] = []

    def flush():
        nonlocal current_lines, current_meta, current_figures
        if not current_lines:
            return
        txt = "\n".join(current_lines).strip()
        if not txt and current_figures:
            placeholders = [_figure_placeholder(f.get("figure_id")) for f in current_figures if f.get("figure_id")]
            txt = "\n".join(placeholders) if inline_placeholders and placeholders else "Figure reference"
        if txt:
            chunks.append({"text": txt, "metadata": dict(current_meta), "figures": list(current_figures)})
        current_lines = []
        current_meta = {}
        current_figures = []

    for it in items:
        fig = _figure_from_item(it)
        if fig:
            if not current_meta:
                current_meta = dict(it.get("metadata") or {})
            current_figures.append(fig)
            if inline_placeholders:
                current_lines.append(_figure_placeholder(fig.get("figure_id")))
            continue
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


def _figures_for_part(text: str, figures: List[Dict], inline_placeholders: bool) -> List[Dict]:
    if not figures:
        return []
    if inline_placeholders:
        found = set(re.findall(r"\[FIGURE:([^\]]+)\]", text))
        if not found:
            return []
        return [f for f in figures if f.get("figure_id") in found]
    return list(figures)


def _build_figure_description(chunk_text: str, meta: Dict[str, object], fig: Dict) -> str:
    figure_id = fig.get("figure_id") or "unknown_figure"
    image_ref = fig.get("image_ref") or "unknown_image"
    proc = meta.get("procedure_title") or meta.get("section_title") or meta.get("section_heading") or meta.get("doc_id") or "document"
    proc = " ".join(str(proc).split())
    return f"Figure {figure_id} for {proc}. Image reference: {image_ref}."


def _ensure_procedure_prefix(text: str, meta: Dict[str, object]) -> str:
    proc_title = meta.get("procedure_title")
    if not proc_title:
        return text
    prefix = f"Procedure: {proc_title}"
    lines = (text or "").splitlines()
    first = lines[0].strip() if lines else ""
    if first.lower().startswith(prefix.lower()):
        return text
    body = "\n".join(lines).strip()
    if body:
        return f"{prefix}\n{body}"
    return prefix


def chunk_docx_toc_sections(items: List[Dict], *, cfg: Dict, source_meta: Dict | None = None) -> List[Dict]:
    drop_admin_sections = bool(cfg.get("drop_admin_sections", False))
    admin_cfg = cfg.get("admin_sections") or {}
    admin_enabled = bool(admin_cfg.get("enabled", False)) and drop_admin_sections
    if admin_enabled:
        match_mode = (admin_cfg.get("match_mode") or "heading_regex").strip().lower()
        heading_regexes = _compile_heading_regex(admin_cfg.get("heading_regex") or [])
        heading_exact = {_normalize_heading_text(h) for h in (admin_cfg.get("heading_exact") or []) if h}
        stop_regexes = _compile_heading_regex(admin_cfg.get("stop_excluding_after_heading_regex") or [])
        stop_exact = {
            _normalize_heading_text(h) for h in (admin_cfg.get("stop_excluding_after_heading_exact") or []) if h
        }
        admin_exclusion_active = True
        filtered_items: List[Dict] = []
        for item in items:
            meta = item.get("metadata") or {}
            heading = _current_heading_from_meta(meta)
            heading_norm = _normalize_heading_text(heading)
            stop_match = False
            if heading_norm:
                if _matches_heading_regex(heading, stop_regexes):
                    stop_match = True
                elif heading_norm in stop_exact:
                    stop_match = True
            if stop_match:
                admin_exclusion_active = False
                filtered_items.append(item)
                continue
            admin_match = False
            if heading_norm:
                if match_mode in {"heading_regex", "both"} and _matches_heading_regex(heading, heading_regexes):
                    admin_match = True
                if not admin_match and match_mode in {"heading_exact", "both"} and heading_norm in heading_exact:
                    admin_match = True
            if admin_match:
                continue
            if admin_exclusion_active and not heading_norm:
                filtered_items.append(item)
                continue
            filtered_items.append(item)
        if DOCX_SECTION_CHUNK_DEBUG:
            _log.info(
                "DOCX_SECTION_CHUNK_DEBUG admin_filter enabled=%s before=%d after=%d",
                admin_enabled,
                len(items),
                len(filtered_items),
            )
        items = filtered_items

    inline_placeholders = _env_flag("DOCX_INLINE_FIGURE_PLACEHOLDERS", False)
    figure_chunks_enabled = _env_flag("DOCX_FIGURE_CHUNKS", False)
    track_figures = inline_placeholders or figure_chunks_enabled
    items_for_chunking = items if track_figures else [it for it in items if (it.get("metadata") or {}).get("block_type") != "image"]
    effective_max = int(cfg.get("effective_max_tokens") or cfg.get("max_tokens") or 512)
    stripped_items, removed_lines, bounds, raw_toc_lines = _strip_toc_items(items_for_chunking)
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
        chunks = _split_by_titles(stripped_items, usable_toc, inline_placeholders)
        strategy = "TOC_LEVEL1"
    else:
        num_chunks = _split_num_prefix_major(items_for_chunking, inline_placeholders)
        if num_chunks:
            chunks = num_chunks
            strategy = "NUM_PREFIX_MAJOR"
        else:
            inline_chunks = _split_inline_level1(items_for_chunking, inline_placeholders)
            if inline_chunks:
                chunks = inline_chunks
                strategy = "INLINE_LEVEL1"
            else:
                chunks = _split_heading1(items_for_chunking, inline_placeholders)
                strategy = "HEADING1"
        if DOCX_TOC_DEBUG and toc_entries and len(toc_entries) < 3:
            _log.info("DOCX_TOC_DEBUG TOC parse insufficient (entries=%d); falling back to %s", len(toc_entries), strategy)
    if DOCX_SECTION_CHUNK_DEBUG:
        _log.info("DOCX_SECTION_CHUNK_DEBUG Strategy=%s", strategy)

    final_chunks: List[Dict] = []
    chunk_local_index = 0
    placeholders_count = 0
    figure_chunk_count = 0
    parent_links = 0
    images_seen = 0
    for ch in chunks:
        meta = dict(source_meta or {})
        meta.update(ch.get("metadata") or {})
        text = _ensure_procedure_prefix(ch.get("text") or "", meta)
        figures = ch.get("figures") or []
        header_line = text.splitlines()[0] if text else ""
        if _estimate_tokens(text) > effective_max:
            parts = _split_to_limit("\n".join(text.splitlines()[1:]), effective_max, header_line)
            chunk_parts = [
                (part, {**meta, "section_strategy": strategy, "split_part": idx, "is_split": True})
                for idx, part in enumerate(parts, start=1)
            ]
        else:
            chunk_parts = [(text, {**meta, "section_strategy": strategy, "is_split": False})]

        for part_text, part_meta in chunk_parts:
            part_text = _ensure_procedure_prefix(part_text, part_meta)
            include_figures = inline_placeholders or figure_chunks_enabled
            part_figures = _figures_for_part(part_text, figures, inline_placeholders) if include_figures else []
            chunk_local_index += 1
            part_meta["chunk_local_index"] = chunk_local_index
            if part_figures:
                part_meta["figure_ids"] = [f.get("figure_id") for f in part_figures if f.get("figure_id")]
                images_seen += len(part_figures)
                placeholders_count += sum(1 for f in part_figures if inline_placeholders)
            cleaned_text = part_text
            if not inline_placeholders and part_figures:
                cleaned_text = re.sub(r"\s*\[FIGURE:[^\]]+\]\s*", "\n", cleaned_text).strip()
                if not cleaned_text:
                    cleaned_text = "Figure reference"
            final_chunks.append({"text": cleaned_text, "metadata": part_meta})

            if figure_chunks_enabled and part_figures:
                for fig in part_figures:
                    parent_guess = part_meta.get("chunk_id")
                    doc_id_val = part_meta.get("doc_id")
                    if not parent_guess and doc_id_val:
                        parent_guess = f"{doc_id_val}_chunk_{chunk_local_index}"
                    fig_meta = {
                        **part_meta,
                        "chunk_type": "figure",
                        "figure_id": fig.get("figure_id"),
                        "parent_chunk_id": parent_guess,
                        "parent_chunk_local_index": chunk_local_index,
                        "image_ref": fig.get("image_ref"),
                    }
                    fig_meta.pop("figure_ids", None)
                    desc = _build_figure_description(cleaned_text, part_meta, fig)
                    final_chunks.append({"text": desc, "metadata": fig_meta})
                    figure_chunk_count += 1
                    if parent_guess:
                        parent_links += 1
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
    if track_figures:
        doc_for_log = (source_meta or {}).get("doc_id") or next(
            (c.get("metadata", {}).get("doc_id") for c in final_chunks if c.get("metadata")), ""
        )
        _log.info(
            "DOCX_FIGURE_CHUNKING_SUMMARY doc_id=%s placeholders=%s figure_chunks=%s parents=%s images_seen=%s",
            doc_for_log,
            placeholders_count,
            figure_chunk_count,
            parent_links,
            images_seen,
        )
    return final_chunks
