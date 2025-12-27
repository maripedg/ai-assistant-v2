"""DOCX loader using zipfile + XML (no external docx dependency).

Purpose
- Extract readable text blocks from `word/document.xml`. If `Heading1` paragraphs are present,
  split into sections by Heading1; otherwise return a single document item.

Contract
- export: load(path: str) -> list[dict]
"""

from typing import List, Dict
import os
import logging
from docx import Document
from docx.oxml.ns import qn
from backend.ingest.text_cleaner import clean_text
from backend.ingest.chunking.block_types import Block
from backend.ingest.chunking.block_cleaner import clean_blocks


def _extract_paragraph_number(paragraph) -> tuple[str | None, int | None]:
    """
    Best-effort extraction of paragraph numbering from w:numPr (list/outline numbers).
    Returns (number_str, level).
    """
    try:
        ppr = paragraph._p.pPr  # type: ignore[attr-defined]
    except Exception:
        return None, None
    if ppr is None or ppr.numPr is None:
        return None, None
    num_pr = ppr.numPr
    num_id = None
    ilvl = None
    try:
        num_id_el = num_pr.find(qn("w:numId"))
        ilvl_el = num_pr.find(qn("w:ilvl"))
        if num_id_el is not None and num_id_el.val is not None:
            num_id = int(num_id_el.val)
        if ilvl_el is not None and ilvl_el.val is not None:
            ilvl = int(ilvl_el.val)
    except Exception:
        return None, None
    if num_id is None:
        return None, ilvl

    # Try to resolve numbering level text if definitions exist
    number_text = None
    try:
        numbering = paragraph.part.numbering_part.numbering_definitions  # type: ignore[attr-defined]
        if numbering:
            num_def = numbering._numbering.get(num_id)  # type: ignore[attr-defined]
            if num_def:
                abstract_num_id = num_def.abstractNumId
                abstract_def = numbering._abstract_numbering[abstract_num_id]  # type: ignore[attr-defined]
                level = ilvl or 0
                lvl_def = abstract_def.levels[level]
                fmt = lvl_def.nfc
                start = lvl_def.start
                lvl_text = lvl_def.lvlText
                if fmt == "decimal" and lvl_text:
                    # Build a numeric prefix using level and start as a fallback
                    if "%" in lvl_text:
                        number_text = lvl_text.replace("%1", str(start))
                    else:
                        number_text = str(start)
    except Exception:
        number_text = None

    return number_text, ilvl


def _resolve_numbering_prefix(paragraph, fallback: str | None = None) -> tuple[str | None, int | None, int | None]:
    """
    Attempt to reconstruct the visible numbering prefix using numbering definitions.
    Returns (prefix, num_id, ilvl)
    """
    num_text, ilvl = _extract_paragraph_number(paragraph)
    num_id = None
    try:
        ppr = paragraph._p.pPr  # type: ignore[attr-defined]
        if ppr is None or ppr.numPr is None:
            return fallback, num_id, ilvl
        num_pr = ppr.numPr
        num_id_el = num_pr.find(qn("w:numId"))
        if num_id_el is not None and num_id_el.val is not None:
            num_id = int(num_id_el.val)
    except Exception:
        return fallback, num_id, ilvl

    if num_text and num_id is not None:
        return num_text, num_id, ilvl

    # Attempt to resolve using numbering definitions
    try:
        numbering = paragraph.part.numbering_part.numbering_definitions  # type: ignore[attr-defined]
        if not numbering or num_id is None:
            return fallback, num_id, ilvl
        num_def = numbering._numbering.get(num_id)  # type: ignore[attr-defined]
        if not num_def:
            return fallback, num_id, ilvl
        abstract_num_id = num_def.abstractNumId
        abstract_def = numbering._abstract_numbering.get(abstract_num_id)  # type: ignore[attr-defined]
        if not abstract_def:
            return fallback, num_id, ilvl
        level = ilvl or 0
        lvl_def = abstract_def.levels[level]
        start = int(lvl_def.start or 1)
        if level == 0:
            return str(start), num_id, ilvl
        # Build hierarchical prefix using start values of prior levels
        # We use simplistic counting: if prior levels unknown, default to 1
        parts = []
        for l in range(level + 1):
            if l == level:
                parts.append(str(start))
            else:
                parts.append("1")
        return ".".join(parts), num_id, ilvl
    except Exception:
        return fallback, num_id, ilvl


def load(path: str) -> List[Dict]:
    abs_path = os.path.abspath(path)
    items: List[Dict] = []
    loader_debug = (os.getenv("DOCX_LOADER_DEBUG") or "").lower() in {"1", "true", "yes", "on"}
    log = logging.getLogger(__name__)
    try:
        doc = Document(abs_path)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to parse DOCX: {abs_path}: {exc}") from exc

    blocks: List[Block] = []
    counters: Dict[int, List[int]] = {}
    base_meta = {
        "source": abs_path,
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    for p in doc.paragraphs:
        raw_text = p.text or ""
        text = raw_text.rstrip("\n")
        if not text.strip():
            continue
        style = getattr(p, "style", None)
        style_name = (style.name if style else "").strip().lower()
        if style_name.startswith("toc"):
            continue

        block_type = "paragraph"
        heading_level = None
        numbering_prefix = None
        resolved_prefix, num_id, outline_level = _resolve_numbering_prefix(p)
        computed_prefix = None

        if style_name.startswith("heading"):
            block_type = "heading"
            try:
                heading_level = int("".join(ch for ch in style_name if ch.isdigit()) or "1")
            except Exception:
                heading_level = 1
            import re

            m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)*)", text)
            if m:
                numbering_prefix = m.group(1)
            if not numbering_prefix:
                numbering_prefix = resolved_prefix
            # Ensure heading text carries numbering prefix visibly
            if numbering_prefix:
                num_clean = numbering_prefix.rstrip(".")
                num_re = re.compile(rf"^\s*{re.escape(num_clean)}[\s\.]", flags=re.IGNORECASE)
                if not num_re.match(text):
                    sep = ". " if "." not in num_clean else " "
                    text = f"{num_clean}{sep}{text}"
        elif "list" in style_name or "bullet" in style_name or "number" in style_name:
            block_type = "list"
        else:
            # try to detect numbering by content even if style not list
            import re

            if re.match(r"^\s*[\-\*\u2022]\s+", text) or re.match(r"^\s*\d+[\).]\s+", text):
                block_type = "list"

        if num_id is not None and outline_level is not None:
            if num_id not in counters:
                counters[num_id] = [0] * 9
            if outline_level >= len(counters[num_id]):
                counters[num_id].extend([0] * (outline_level + 1 - len(counters[num_id])))
            counters[num_id][outline_level] += 1
            for k in range(outline_level + 1, len(counters[num_id])):
                counters[num_id][k] = 0
            parts = [str(counters[num_id][i]) for i in range(outline_level + 1)]
            computed_prefix = ".".join(parts)
            if not numbering_prefix and computed_prefix:
                numbering_prefix = computed_prefix

        meta = dict(base_meta)
        if block_type == "heading":
            meta["heading_level"] = heading_level
            meta["heading_text"] = text
            if numbering_prefix:
                meta["numbering_prefix"] = numbering_prefix
                meta["num_prefix"] = numbering_prefix
        if resolved_prefix:
            meta["outline_number"] = resolved_prefix
        if outline_level is not None:
            meta["outline_level"] = outline_level
        if resolved_prefix is not None or outline_level is not None:
            meta["has_numpr"] = True
        blocks.append(Block(type=block_type, text=text, meta=meta))
        if loader_debug and block_type == "heading":
            log.info(
                "DOCX_LOADER_DEBUG heading text=%s style=%s level=%s num_prefix=%s outline=%s numId=%s ilvl=%s",
                (text[:80] + "...") if len(text) > 80 else text,
                style_name,
                heading_level,
                numbering_prefix,
                resolved_prefix,
                num_id,
                outline_level,
            )

    if not blocks:
        return items

    cleaned_blocks = clean_blocks("docx", blocks)

    heading_stack: List[Dict] = []
    current_item_lines: List[str] = []
    current_meta: Dict[str, object] = {}

    def _filtered_heading_stack() -> List[Dict]:
        # IMPORTANT: ignore level 0 “document title” headings so they don’t become the major section key
        # (e.g., “Standard Operating Procedure (SOP)”).
        return [h for h in heading_stack if int(h.get("level") or 0) >= 1]

    def flush_item():
        nonlocal current_item_lines, current_meta
        if not current_item_lines:
            return
        text_out = clean_text("\n".join(current_item_lines), preserve_tables=False)
        if text_out:
            items.append({"text": text_out, "metadata": dict(current_meta)})
        current_item_lines = []

    for blk in cleaned_blocks:
        if blk.type == "heading":
            level = int(blk.meta.get("heading_level") or 1)
            title = (blk.meta.get("heading_text") or blk.text or "").rstrip()

            while heading_stack and heading_stack[-1]["level"] >= level:
                heading_stack.pop()
            heading_stack.append(
                {"level": level, "title": title, "numbering": blk.meta.get("numbering_prefix")}
            )

            import re

            m_num = re.match(r"^\s*(?:sop[-\s]?(\d+)|(\d+))\b", title, flags=re.IGNORECASE)
            m_step = re.match(r"^\s*\d+[).]\s+", title)
            is_top_numeric = bool(m_num) and not bool(m_step)
            is_boundary = (level == 1) or is_top_numeric

            if is_boundary:
                flush_item()
                current_item_lines = []
                current_meta = dict(base_meta)

            if not current_item_lines and not current_meta:
                current_meta = dict(base_meta)

            current_item_lines.append(title)

            filtered = _filtered_heading_stack()
            current_meta["heading_path"] = [h["title"] for h in filtered]
            current_meta["section_heading"] = filtered[-1]["title"] if filtered else None
            current_meta["heading_level_of_section"] = filtered[-1]["level"] if filtered else None
            current_meta["numbering_prefix_of_section"] = filtered[-1].get("numbering") if filtered else None

        else:
            if not current_item_lines:
                current_meta = dict(base_meta)
                filtered = _filtered_heading_stack()
                current_meta["heading_path"] = [h["title"] for h in filtered]
                current_meta["section_heading"] = filtered[-1]["title"] if filtered else None
                current_meta["heading_level_of_section"] = filtered[-1]["level"] if filtered else None
                current_meta["numbering_prefix_of_section"] = filtered[-1].get("numbering") if filtered else None

            current_item_lines.append(blk.text)

    flush_item()
    return items
