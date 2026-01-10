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
    title = m.group(2).strip() if m.group(2) else ""
    return num, title or None


def _extract_numeric_heading_prefix(text: str) -> str | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)*)\b", text or "")
    if not m:
        return None
    return m.group(1)


def _extract_integer_major_from_text(text: str) -> str | None:
    m = re.match(r"^\s*(\d+)(?:\b|[.)-])", text or "")
    if not m:
        return None
    return m.group(1)


def _strip_major_prefix(text: str, major: str) -> str:
    if not text or not major:
        return (text or "").strip()
    pattern = rf"^\s*{re.escape(str(major))}\s*[.)-]?\s*"
    return re.sub(pattern, "", text, count=1).strip()


def _strip_numeric_prefix(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"^\s*\d+(?:\.\d+)*\s*[\).\-:]?\s*", "", text).strip()


def _display_section_number(prefix_raw: str) -> str:
    # Display format requested: keep at most major.minor (e.g., 4.1) when raw has more depth.
    parts = prefix_raw.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return prefix_raw


def _synth_section_number(major: str | None, seq: int) -> str | None:
    if major is None:
        return None
    return f"{major}.{seq}"


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
        body = re.sub(r"\s+\d{1,4}$", "", body).strip()
        body_norm = " ".join(body.replace("–", "-").replace("—", "-").split())
        if not body_norm:
            continue
        entries.append((num, body_norm))
    return entries


def _parse_toc_hierarchy(lines: List[str]) -> List[Tuple[str, str, str, int]]:
    entries: List[Tuple[str, str, str, int]] = []
    pat = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.*)$")
    for ln in lines:
        ln_norm = ln.replace("\t", " ")
        ln_norm = re.sub(r"\.{3,}", " ", ln_norm)
        ln_norm = " ".join(ln_norm.replace("–", "-").replace("—", "-").split())
        m = pat.match(ln_norm)
        if not m:
            continue
        prefix = m.group(1)
        body = m.group(2) or ""
        body = re.sub(r"\s+\d{1,4}$", "", body).strip()
        body_norm = " ".join(body.replace("–", "-").replace("—", "-").split())
        if not body_norm:
            continue
        level = prefix.count(".") + 1
        entries.append((prefix, _normalize_title(body_norm), body_norm, level))
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


def _split_by_toc_hierarchy(items: List[Dict], toc_entries: List[Tuple[str, str, str, int]], inline_placeholders: bool) -> List[Dict]:
    if not toc_entries:
        return []
    toc_by_major: Dict[str, List[Tuple[str, str, str, int]]] = {}
    title_map_by_major: Dict[str, Dict[str, List[Tuple[str, str, str, int]]]] = {}
    prefix_title_by_major: Dict[str, Dict[str, str]] = {}
    for prefix, title_norm, title_raw, level in toc_entries:
        major = prefix.split(".", 1)[0]
        toc_by_major.setdefault(major, []).append((prefix, title_norm, title_raw, level))
        title_map_by_major.setdefault(major, {}).setdefault(title_norm, []).append((prefix, title_norm, title_raw, level))
        prefix_title_by_major.setdefault(major, {})[prefix] = title_raw

    chunks: List[Dict] = []
    current_major: str | None = None
    current_proc_title: str | None = None
    current_section_raw: str | None = None
    current_section_display: str | None = None
    current_section_title: str | None = None
    current_path_line: str | None = None
    current_lines: List[str] = []
    current_figures: List[Dict] = []
    pending_lines: List[str] = []
    pending_figures: List[Dict] = []
    current_meta: Dict[str, object] = {}
    step_seq = 1
    strategy = "TOC_HIERARCHY"

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

    def _build_path_line(prefix_raw: str, section_title: str | None) -> str:
        major = prefix_raw.split(".", 1)[0]
        prefix_titles = prefix_title_by_major.get(major, {})
        parts = prefix_raw.split(".")
        pieces: List[str] = []
        for i in range(1, len(parts) + 1):
            prefix = ".".join(parts[:i])
            title = prefix_titles.get(prefix)
            if i == 1 and not title:
                title = current_proc_title
            if i == len(parts) and section_title:
                title = section_title
            if title:
                pieces.append(f"{prefix}. {title}")
            else:
                pieces.append(prefix)
        return "Path: " + " | ".join(pieces)

    def _emit_current() -> None:
        if current_section_raw is None or current_section_display is None:
            return
        proc_header = f"Procedure: {current_major}"
        if current_proc_title:
            proc_header = f"Procedure: {current_major}. {current_proc_title}"
        section_header = f"Section: {current_section_display}"
        if current_section_title:
            section_header = f"{section_header}. {current_section_title}"
        header_lines = [proc_header, section_header]
        if current_section_raw != current_section_display:
            header_lines.append(f"Step: {current_section_raw}")
        if current_path_line:
            header_lines.append(current_path_line)
        text_lines = header_lines + [ln for ln in current_lines if ln is not None]
        meta = dict(current_meta or {})
        meta.update(
            {
                "procedure_number": str(current_major),
                "procedure_title": proc_header.replace("Procedure: ", ""),
                "section_number": str(current_section_display),
                "section_number_raw": str(current_section_raw),
                "section_number_display": str(current_section_display),
                "section_title": current_section_title,
                "section_strategy": strategy,
            }
        )
        chunks.append({"text": "\n".join(text_lines).strip(), "metadata": meta, "figures": list(current_figures)})

    def _start_section(prefix_raw: str, section_title: str | None, heading_lines: List[str], meta: Dict[str, object]) -> None:
        nonlocal current_section_raw, current_section_display, current_section_title, current_path_line
        nonlocal current_lines, current_figures, current_meta
        current_section_raw = prefix_raw
        current_section_display = _display_section_number(prefix_raw)
        current_section_title = section_title
        current_path_line = _build_path_line(prefix_raw, section_title)
        current_lines = []
        current_figures = []
        current_meta = dict(meta or {})
        if pending_lines:
            current_lines.extend(pending_lines)
            pending_lines.clear()
        if pending_figures:
            current_figures.extend(pending_figures)
            pending_figures.clear()
        current_lines.extend(heading_lines)

    for it in items:
        fig = _figure_from_item(it)
        if fig:
            if current_section_raw is None:
                pending_figures.append(fig)
                if inline_placeholders:
                    pending_lines.append(_figure_placeholder(fig.get("figure_id")))
            else:
                current_figures.append(fig)
                if inline_placeholders:
                    current_lines.append(_figure_placeholder(fig.get("figure_id")))
            continue

        heading_lines = (it.get("text") or "").splitlines()
        heading_line = heading_lines[0].strip() if heading_lines else ""
        sop_num, sop_title = _parse_sop_heading(heading_line)
        is_heading = _is_heading(it)
        if sop_num:
            if current_section_raw is not None:
                _emit_current()
            if current_major is not None:
                pending_lines = []
                pending_figures = []
            current_major = sop_num
            current_proc_title = sop_title or _strip_numeric_prefix(heading_line)
            current_section_raw = None
            current_section_display = None
            current_section_title = None
            current_path_line = None
            current_lines = []
            current_figures = []
            current_meta = dict(it.get("metadata") or {})
            step_seq = 1
            continue
        if is_heading and heading_line:
            major_from_heading = _extract_integer_major_from_text(heading_line)
            if major_from_heading:
                if current_section_raw is not None:
                    _emit_current()
                if current_major is not None:
                    pending_lines = []
                    pending_figures = []
                current_major = str(major_from_heading)
                current_proc_title = _strip_major_prefix(heading_line, str(major_from_heading))
                current_section_raw = None
                current_section_display = None
                current_section_title = None
                current_path_line = None
                current_lines = []
                current_figures = []
                current_meta = dict(it.get("metadata") or {})
                step_seq = 1
                continue

        if current_major is None:
            pending_lines.extend(heading_lines)
            continue

        if is_heading:
            prefix_raw = None
            title_norm = _normalize_title(heading_line)
            candidates = title_map_by_major.get(str(current_major), {}).get(title_norm, [])
            for cand in candidates:
                if str(cand[0]).startswith(f"{current_major}."):
                    prefix_raw = cand[0]
                    break
            if not prefix_raw:
                prefix_raw = _extract_numeric_heading_prefix(heading_line)
                if prefix_raw and not str(prefix_raw).startswith(f"{current_major}."):
                    prefix_raw = None
            if not prefix_raw:
                prefix_raw = _synth_section_number(current_major, step_seq)
                step_seq += 1
            if current_section_raw is not None:
                _emit_current()
            section_title = None
            if prefix_raw:
                prefix_titles = prefix_title_by_major.get(str(current_major), {})
                section_title = prefix_titles.get(str(prefix_raw))
            if not section_title:
                section_title = _strip_numeric_prefix(heading_line)
            _start_section(str(prefix_raw), section_title or None, heading_lines, it.get("metadata") or {})
            continue

        if current_section_raw is None:
            pending_lines.extend(heading_lines)
        else:
            current_lines.extend(heading_lines)

    if current_section_raw is not None:
        _emit_current()

    return [c for c in chunks if len([ln for ln in c["text"].splitlines() if ln.strip()]) > 1 or (c.get("figures") or [])]


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
    """
    Heading-driven procedure/section splitter using heading levels.
    """
    chunks: List[Dict] = []
    strategy = "NUM_PREFIX_MAJOR"

    preamble_lines: List[str] = []
    preamble_meta: Dict[str, object] = {}
    preamble_emitted = False
    preamble_figures: List[Dict] = []

    current_proc_heading: str | None = None
    current_proc_meta: Dict[str, object] = {}
    proc_items: List[Dict] = []

    def _heading_level(item: Dict) -> int | None:
        meta = item.get("metadata") or {}
        lvl = meta.get("heading_level_of_section")
        if lvl is None:
            lvl = meta.get("heading_level")
        if lvl is None:
            outline = meta.get("outline_level")
            if outline is not None:
                try:
                    outline_val = int(outline)
                    return outline_val + 1
                except Exception:
                    return None
        try:
            return int(lvl) if lvl is not None else None
        except Exception:
            return None

    def _is_heading(item: Dict) -> bool:
        return _heading_level(item) is not None

    def _heading_text(item: Dict) -> str:
        meta = item.get("metadata") or {}
        heading = (meta.get("section_heading") or meta.get("heading_text") or "").strip()
        if heading:
            return heading
        lines = (item.get("text") or "").splitlines()
        return (lines[0].strip() if lines else "").strip()

    def _emit_text(lines: List[str], meta: Dict[str, object], figures: List[Dict]) -> None:
        if not lines and not figures:
            return
        txt = "\n".join(lines).strip()
        if not txt and figures:
            if inline_placeholders:
                txt = "\n".join([_figure_placeholder(f.get("figure_id")) for f in figures if f.get("figure_id")]) or "[FIGURE]"
            else:
                txt = "Figure reference"
        non_empty_lines = [ln for ln in txt.splitlines() if ln.strip()]
        if (not txt or len(non_empty_lines) <= 1) and not figures:
            return
        meta_out = dict(meta or {})
        meta_out.setdefault("section_strategy", strategy)
        chunks.append({"text": txt, "metadata": meta_out, "figures": list(figures or [])})

    def _process_procedure(proc_heading: str, proc_meta: Dict[str, object], items_in_proc: List[Dict]) -> None:
        proc_title = (proc_heading or "").strip() or "Procedure"
        proc_number = _extract_integer_major_from_text(proc_heading)

        has_subheadings = False
        for it in items_in_proc:
            if (it.get("metadata") or {}).get("block_type") == "image":
                continue
            lvl = _heading_level(it)
            if lvl is not None and lvl >= 2:
                has_subheadings = True
                break

        def _procedure_header(title: str) -> str:
            if proc_number:
                return f"Procedure {proc_number}: {title}"
            return f"Procedure: {title}"

        def _path_line(path_parts: List[str]) -> str:
            return "Path: " + " | ".join(path_parts)

        if not has_subheadings:
            body_lines: List[str] = []
            body_figures: List[Dict] = []
            for it in items_in_proc:
                fig = _figure_from_item(it)
                if fig:
                    body_figures.append(fig)
                    if inline_placeholders:
                        body_lines.append(_figure_placeholder(fig.get("figure_id")))
                    continue
                body_lines.extend((it.get("text") or "").splitlines())
            header_lines = [_procedure_header(proc_title), f"Section: {proc_title}", _path_line([proc_title])]
            meta = dict(proc_meta or {})
            if proc_number:
                meta["procedure_number"] = proc_number
            meta["procedure_title"] = proc_title
            meta["section_title"] = proc_title
            meta["section_heading"] = proc_title
            meta["heading_path"] = [proc_title]
            _emit_text(header_lines + body_lines, meta, body_figures)
            return

        pending_lines: List[str] = []
        pending_figures: List[Dict] = []
        current_lines: List[str] = []
        current_figures: List[Dict] = []
        current_meta: Dict[str, object] = {}
        current_section_title: str | None = None
        current_section_level: int | None = None
        current_heading_path: List[str] = []
        current_path_line: str | None = None
        heading_stack: List[Tuple[int, str]] = [(1, proc_title)]

        def _flush_section():
            nonlocal current_lines, current_figures, current_meta, current_section_title
            nonlocal current_section_level, current_heading_path, current_path_line
            if not current_section_title:
                return
            section_number = _extract_numeric_heading_prefix(current_section_title)
            meta = dict(current_meta or {})
            if proc_number:
                meta["procedure_number"] = proc_number
            meta["procedure_title"] = proc_title
            meta["section_title"] = current_section_title
            meta["section_heading"] = current_section_title
            if section_number:
                meta["section_number"] = section_number
            if current_heading_path:
                meta["heading_path"] = list(current_heading_path)
            header_lines = [_procedure_header(current_section_title), f"Section: {current_section_title}"]
            if current_path_line:
                header_lines.append(current_path_line)
            _emit_text(header_lines + current_lines, meta, current_figures)
            current_lines = []
            current_figures = []
            current_meta = {}
            current_section_title = None
            current_section_level = None
            current_heading_path = []
            current_path_line = None

        def _update_stack(level: int, title: str) -> None:
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))

        for it in items_in_proc:
            fig = _figure_from_item(it)
            if fig:
                if current_section_title is None:
                    pending_figures.append(fig)
                    if inline_placeholders:
                        pending_lines.append(_figure_placeholder(fig.get("figure_id")))
                else:
                    current_figures.append(fig)
                    if inline_placeholders:
                        current_lines.append(_figure_placeholder(fig.get("figure_id")))
                continue

            lvl = _heading_level(it)
            is_heading = _is_heading(it)
            heading_text = _heading_text(it)
            lines = (it.get("text") or "").splitlines()

            if is_heading and lvl is not None and lvl >= 2:
                if current_section_title is None or (current_section_level is not None and lvl <= current_section_level):
                    _flush_section()
                    _update_stack(lvl, heading_text)
                    current_section_title = heading_text
                    current_section_level = lvl
                    current_heading_path = [t for _, t in heading_stack]
                    current_path_line = _path_line(current_heading_path)
                    current_meta = dict(it.get("metadata") or {})
                    if pending_lines:
                        current_lines.extend(pending_lines)
                        pending_lines = []
                    if pending_figures:
                        current_figures.extend(pending_figures)
                        pending_figures = []
                    if len(lines) > 1:
                        current_lines.extend(lines[1:])
                    continue

                _update_stack(lvl, heading_text)
                current_lines.extend(lines)
                continue

            if current_section_title is None:
                pending_lines.extend(lines)
            else:
                current_lines.extend(lines)

        _flush_section()

        if current_section_title is None and (pending_lines or pending_figures):
            header_lines = [_procedure_header(proc_title), f"Section: {proc_title}", _path_line([proc_title])]
            meta = dict(proc_meta or {})
            if proc_number:
                meta["procedure_number"] = proc_number
            meta["procedure_title"] = proc_title
            meta["section_title"] = proc_title
            meta["section_heading"] = proc_title
            meta["heading_path"] = [proc_title]
            _emit_text(header_lines + pending_lines, meta, pending_figures)

    # Partition by heading level 1
    for it in items:
        fig = _figure_from_item(it)
        if fig and current_proc_heading is None:
            preamble_figures.append(fig)
            if inline_placeholders:
                preamble_lines.append(_figure_placeholder(fig.get("figure_id")))
            if not preamble_meta:
                preamble_meta = dict(it.get("metadata") or {})
            continue

        lvl = _heading_level(it)
        is_heading = _is_heading(it)
        heading_text = _heading_text(it)
        if is_heading and lvl == 1:
            if current_proc_heading is None:
                if preamble_lines:
                    _emit_text(preamble_lines, preamble_meta, preamble_figures)
                    preamble_lines = []
                    preamble_meta = {}
                    preamble_emitted = True
                    preamble_figures = []
            else:
                _process_procedure(current_proc_heading, current_proc_meta, proc_items)
                proc_items = []
            current_proc_heading = heading_text
            current_proc_meta = dict(it.get("metadata") or {})
            continue

        if current_proc_heading is None:
            if fig:
                preamble_figures.append(fig)
                if inline_placeholders:
                    preamble_lines.append(_figure_placeholder(fig.get("figure_id")))
            else:
                preamble_lines.extend((it.get("text") or "").splitlines())
            continue

        proc_items.append(it)

    if current_proc_heading is not None:
        _process_procedure(current_proc_heading, current_proc_meta, proc_items)
    if preamble_lines and not preamble_emitted:
        _emit_text(preamble_lines, preamble_meta, preamble_figures)

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


def _select_procedure_title(meta: Dict[str, object]) -> str:
    if not meta:
        return ""
    section_heading = meta.get("section_heading")
    if isinstance(section_heading, str) and section_heading.strip():
        return section_heading.strip()
    heading_path = meta.get("heading_path")
    if isinstance(heading_path, (list, tuple)) and heading_path:
        last = heading_path[-1]
        if isinstance(last, str) and last.strip():
            return last.strip()
        if last is not None:
            return str(last).strip()
    proc_title = meta.get("procedure_title")
    if isinstance(proc_title, str) and proc_title.strip():
        return proc_title.strip()
    if proc_title is not None:
        return str(proc_title).strip()
    return ""


def _ensure_procedure_prefix(text: str, meta: Dict[str, object]) -> str:
    proc_title = _select_procedure_title(meta)
    if not proc_title:
        return text
    proc_number = meta.get("procedure_number")
    if proc_number:
        prefix = f"Procedure {proc_number}: {proc_title}"
    else:
        prefix = f"Procedure: {proc_title}"
    lines = (text or "").splitlines()
    first = ""
    for ln in lines:
        if ln.strip():
            first = ln.strip()
            break
    if first.lower().startswith("procedure:") or re.match(r"^procedure\s+\d+\s*:", first, flags=re.IGNORECASE):
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
    num_chunks = _split_num_prefix_major(items_for_chunking, inline_placeholders)
    if num_chunks:
        chunks = num_chunks
        strategy = "NUM_PREFIX_MAJOR"
    else:
        chunks = _split_heading1(items_for_chunking, inline_placeholders)
        strategy = "HEADING1"
    if DOCX_SECTION_CHUNK_DEBUG:
        _log.info("DOCX_SECTION_CHUNK_DEBUG Strategy=%s", strategy)

    final_chunks: List[Dict] = []
    chunk_local_index = 0
    placeholders_count = 0
    figure_chunk_count = 0
    parent_links = 0
    images_seen = 0
    debug_emitted = 0
    for ch in chunks:
        meta = dict(source_meta or {})
        meta.update(ch.get("metadata") or {})
        text = _ensure_procedure_prefix(ch.get("text") or "", meta)
        figures = ch.get("figures") or []
        if _estimate_tokens(text) > effective_max:
            lines = text.splitlines()
            header_lines = [lines[0]] if lines else []
            body_start = 1
            if len(lines) > 1 and lines[0].strip().lower().startswith("procedure:") and lines[1].strip().lower().startswith("section:"):
                header_lines = [lines[0], lines[1]]
                body_start = 2
                if len(lines) > 2 and lines[2].strip().lower().startswith("path:"):
                    header_lines.append(lines[2])
                    body_start = 3
            header_block = "\n".join(header_lines).strip()
            parts = _split_to_limit("\n".join(lines[body_start:]), effective_max, header_block)
            chunk_parts = [
                (part, {**meta, "section_strategy": strategy, "split_part": idx, "is_split": True})
                for idx, part in enumerate(parts, start=1)
            ]
        else:
            chunk_parts = [(text, {**meta, "section_strategy": strategy, "is_split": False})]

        for part_text, part_meta in chunk_parts:
            if DOCX_SECTION_CHUNK_DEBUG and debug_emitted < 30:
                heading_path = part_meta.get("heading_path")
                hpath0 = heading_path[0] if isinstance(heading_path, (list, tuple)) and heading_path else None
                hpath_last = heading_path[-1] if isinstance(heading_path, (list, tuple)) and heading_path else None
                _log.info(
                    "DOCX_SECTION_CHUNK_DEBUG proc_header selected=%s section_heading=%s heading_path_first=%s heading_path_last=%s procedure_title=%s procedure_number=%s",
                    _select_procedure_title(part_meta),
                    part_meta.get("section_heading"),
                    hpath0,
                    hpath_last,
                    part_meta.get("procedure_title"),
                    part_meta.get("procedure_number"),
                )
                debug_emitted += 1
            part_text = _ensure_procedure_prefix(part_text, part_meta)
            include_figures = inline_placeholders or figure_chunks_enabled
            part_figures = _figures_for_part(part_text, figures, inline_placeholders) if include_figures else []
            chunk_local_index += 1
            part_meta["chunk_local_index"] = chunk_local_index
            if part_figures:
                part_meta["figure_ids"] = [f.get("figure_id") for f in part_figures if f.get("figure_id")]
                images_seen += len(part_figures)
                placeholders_count += sum(1 for _ in part_figures if inline_placeholders)
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
