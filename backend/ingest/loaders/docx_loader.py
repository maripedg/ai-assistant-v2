"""DOCX loader using python-docx + zipfile for image bytes.

Purpose
- Extract readable text blocks from `word/document.xml`. If `Heading1` paragraphs are present,
  split into sections by Heading1; otherwise return a single document item.

Contract
- export: load(path: str) -> list[dict]
"""

from typing import List, Dict
import os
from pathlib import Path, PurePosixPath
import logging
from zipfile import ZipFile, BadZipFile
import xml.etree.ElementTree as ET
import hashlib
from docx import Document
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from backend.ingest.text_cleaner import clean_text
from backend.ingest.chunking.block_types import Block
from backend.ingest.chunking.block_cleaner import clean_blocks

_NSMAP = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def _repo_root() -> Path:
    try:
        return Path(__file__).resolve().parents[3]
    except Exception:
        return Path.cwd()


def _assets_root() -> Path:
    raw = os.getenv("RAG_ASSETS_DIR")
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (_repo_root() / path).resolve()
        return path
    return _repo_root() / "data" / "rag-assets"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y", "t"}


def _iter_run_image_rids(run) -> list[str]:
    blips = []
    try:
        blips = run.element.xpath(".//*[local-name()='blip']")
    except Exception as exc:
        dbg = (os.getenv("DOCX_IMAGE_DEBUG") or "").lower() in {"1", "true", "yes", "on"}
        if dbg:
            logging.getLogger(__name__).debug("DOCX_IMAGE_DEBUG run blip xpath failed: %s", exc)
    rids: list[str] = []
    for blip in blips:
        rid = blip.get(qn("r:embed")) or blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
        if rid:
            rids.append(rid)
    return rids


def _normalize_target(target: str) -> str:
    if not target:
        return ""
    tgt = target.replace("\\", "/").lstrip("/")
    base = ["word"]
    for part in PurePosixPath(tgt).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if len(base) > 1:
                base.pop()
            continue
        base.append(part)
    return "/".join(base)


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


def _apply_heading_prefix(text: str, numbering_prefix: str | None) -> str:
    if not numbering_prefix:
        return text
    import re

    num_clean = numbering_prefix.rstrip(".")
    num_re = re.compile(rf"^\s*{re.escape(num_clean)}[\s\.]", flags=re.IGNORECASE)
    if num_re.match(text):
        return text
    sep = ". " if "." not in num_clean else " "
    return f"{num_clean}{sep}{text}"


def _strip_heading_meta(meta: Dict[str, object]) -> Dict[str, object]:
    downgraded = dict(meta)
    for key in ("heading_level", "heading_text"):
        downgraded.pop(key, None)
    return downgraded


def load(path: str) -> List[Dict]:
    abs_path = os.path.abspath(path)
    doc_id = Path(abs_path).stem
    items: List[Dict] = []
    extract_images = _env_flag("DOCX_EXTRACT_IMAGES", False)
    inline_flag = _env_flag("DOCX_INLINE_FIGURE_PLACEHOLDERS", False)
    figure_flag = _env_flag("DOCX_FIGURE_CHUNKS", False)
    emit_enabled = extract_images or inline_flag or figure_flag
    image_debug = _env_flag("DOCX_IMAGE_DEBUG", False)
    assets_root = _assets_root()
    loader_debug = (os.getenv("DOCX_LOADER_DEBUG") or "").lower() in {"1", "true", "yes", "on"}
    log = logging.getLogger(__name__)
    images_write_attempted = 0
    image_emit_attempted = 0
    image_emit_skipped = 0
    image_emit_skip_reason = ""
    blips_total = 0
    embed_rids_count = 0
    rels_mapped = 0
    images_written = 0
    image_blocks_emitted = 0
    failures = 0
    rid_to_target: Dict[str, str] = {}
    rid_payload: Dict[str, Dict[str, object]] = {}
    embed_order: List[str] = []
    rels_total = 0
    rels_hit = 0
    targets_resolved = 0
    zip_member_hit = 0
    zip_member_miss = 0
    bytes_read_ok = 0
    try:
        doc = Document(abs_path)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to parse DOCX: {abs_path}: {exc}") from exc

    blocks: List[Block] = []
    counters: Dict[int, List[int]] = {}
    base_meta = {
        "source": abs_path,
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc_id": doc_id,
    }
    figure_seq = 0
    doc_assets_dir = assets_root / doc_id if extract_images else None
    if emit_enabled:
        try:
            with ZipFile(abs_path, "r") as zf:
                try:
                    rels_xml = zf.read("word/_rels/document.xml.rels")
                    rel_root = ET.fromstring(rels_xml)
                    rel_nodes = rel_root.findall(".//{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
                    rels_total = len(rel_nodes)
                    for rel in rel_nodes:
                        rid = rel.attrib.get("Id")
                        target = rel.attrib.get("Target")
                        if rid and target:
                            rid_to_target[rid] = target
                    rels_mapped = len(rid_to_target)
                except KeyError:
                    rels_mapped = 0
                try:
                    doc_xml = zf.read("word/document.xml")
                    doc_root = ET.fromstring(doc_xml)
                    for blip in doc_root.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"):
                        blips_total += 1
                        rid = blip.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                        if rid:
                            embed_rids_count += 1
                            embed_order.append(rid)
                    seen_rids = set()
                    for rid in embed_order:
                        if rid in seen_rids:
                            continue
                        seen_rids.add(rid)
                        target = rid_to_target.get(rid)
                        if target:
                            targets_resolved += 1
                        else:
                            zip_member_miss += 1
                            failures += 1
                            rid_payload[rid] = {"target": None, "member": None, "data": None, "ext": ".img", "candidates": []}
                            continue
                        rels_hit += 1 if rid in rid_to_target else 0
                        candidates = []
                        raw_target = target
                        tgt = target.replace("\\", "/")
                        if tgt.startswith("/"):
                            tgt = tgt.lstrip("/")
                        if tgt.startswith("word/"):
                            candidates.append(tgt)
                            candidates.append(tgt[len("word/") :])
                        else:
                            candidates.append(f"word/{tgt}")
                            candidates.append(tgt)
                        try:
                            from urllib.parse import unquote
                            decoded = unquote(tgt)
                            if decoded != tgt:
                                if decoded.startswith("word/"):
                                    candidates.append(decoded)
                                else:
                                    candidates.append(f"word/{decoded}")
                        except Exception:
                            pass
                        # normalize ../
                        more_candidates = []
                        for cand in candidates:
                            parts = []
                            for part in PurePosixPath(cand).parts:
                                if part in ("", "."):
                                    continue
                                if part == "..":
                                    if parts:
                                        parts.pop()
                                    continue
                                parts.append(part)
                            norm = "/".join(parts)
                            if norm and norm not in more_candidates:
                                more_candidates.append(norm)
                        candidates = []
                        for cand in more_candidates:
                            if not cand.startswith("word/"):
                                candidates.append(f"word/{cand}")
                            candidates.append(cand)
                        chosen = None
                        for cand in candidates:
                            try:
                                zf.getinfo(cand)
                                chosen = cand
                                break
                            except KeyError:
                                continue
                        if not chosen:
                            zip_member_miss += 1
                            failures += 1
                            rid_payload[rid] = {"target": raw_target, "member": None, "data": None, "ext": ".img", "candidates": candidates}
                            if image_debug:
                                log.debug(
                                    "DOCX_IMAGE_DEBUG doc_id=%s rid=%s target=%s candidates=%s chosen=MISS",
                                    doc_id,
                                    rid,
                                    raw_target,
                                    candidates,
                                )
                            continue
                        zip_member_hit += 1
                        try:
                            data = zf.read(chosen)
                            if data:
                                bytes_read_ok += 1
                            else:
                                failures += 1
                            ext = Path(chosen).suffix or ".img"
                            rid_payload[rid] = {
                                "target": raw_target,
                                "member": chosen,
                                "data": data,
                                "ext": ext,
                                "candidates": candidates,
                            }
                            if image_debug:
                                log.debug(
                                    "DOCX_IMAGE_DEBUG doc_id=%s rid=%s target=%s candidates=%s chosen=%s bytes=%s",
                                    doc_id,
                                    rid,
                                    raw_target,
                                    candidates,
                                    chosen,
                                    len(data) if data else 0,
                                )
                        except Exception as exc:
                            failures += 1
                            rid_payload[rid] = {
                                "target": raw_target,
                                "member": chosen,
                                "data": None,
                                "ext": ".img",
                                "candidates": candidates,
                            }
                            if image_debug:
                                log.debug(
                                    "DOCX_IMAGE_DEBUG doc_id=%s rid=%s target=%s candidates=%s read_failed=%s",
                                    doc_id,
                                    rid,
                                    raw_target,
                                    candidates,
                                    exc,
                                )
                except KeyError:
                    pass
        except (BadZipFile, FileNotFoundError):
            pass

    def _iter_block_items(parent):
        if isinstance(parent, _Cell):
            parent_elm = parent._tc
        else:
            parent_elm = parent.element.body if hasattr(parent, "element") else None
        if parent_elm is None:
            return
        for child in parent_elm.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                table = Table(child, parent)
                for row in table.rows:
                    for cell in row.cells:
                        yield from _iter_block_items(cell)

    for p in _iter_block_items(doc):
        raw_text = p.text or ""
        text = raw_text.rstrip("\n")
        run_rids = []
        if emit_enabled:
            for run in p.runs:
                run_rids.extend(_iter_run_image_rids(run))
        has_image = emit_enabled and bool(run_rids)
        if not text.strip() and not has_image:
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
        elif "list" in style_name or "bullet" in style_name or "number" in style_name:
            block_type = "list"
        else:
            import re

            if re.match(r"^\s*[\-\*\u2022]\s+", text) or re.match(r"^\s*\d+[\).]\s+", text):
                block_type = "list"

        heading_text_value = text
        if block_type == "heading" and numbering_prefix:
            heading_text_value = _apply_heading_prefix(text, numbering_prefix)

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
            meta["heading_text"] = heading_text_value
            if numbering_prefix:
                meta["numbering_prefix"] = numbering_prefix
                meta["num_prefix"] = numbering_prefix
        if resolved_prefix:
            meta["outline_number"] = resolved_prefix
        if outline_level is not None:
            meta["outline_level"] = outline_level
        if resolved_prefix is not None or outline_level is not None:
            meta["has_numpr"] = True

        text_for_block = heading_text_value if block_type == "heading" else text

        if not has_image or not extract_images:
            blocks.append(Block(type=block_type, text=text_for_block, meta=meta))
            if loader_debug and block_type == "heading":
                log.info(
                    "DOCX_LOADER_DEBUG heading text=%s style=%s level=%s num_prefix=%s outline=%s numId=%s ilvl=%s",
                    (text_for_block[:80] + "...") if len(text_for_block) > 80 else text_for_block,
                    style_name,
                    heading_level,
                    numbering_prefix,
                    resolved_prefix,
                    num_id,
                    outline_level,
                )
            continue

        text_fragments: List[str] = []
        buffer = ""
        for run in p.runs:
            run_text = (run.text or "")
            run_rid_list = _iter_run_image_rids(run)
            if run_text:
                buffer += run_text
            if run_rid_list:
                if buffer:
                    text_fragments.append(buffer)
                    buffer = ""
            for rid in run_rid_list:
                figure_seq += 1
                payload = rid_payload.get(rid) or {}
                ext = payload.get("ext") or ".img"
                fname = f"img_{figure_seq:03d}{ext}"
                figure_id = f"{doc_id}_img_{figure_seq:03d}"
                image_ref = f"{doc_id}/{fname}"
                img_meta = dict(meta)
                data = payload.get("data")
                zip_member = payload.get("member")
                target_rel = payload.get("target")
                candidates = payload.get("candidates") or []
                if not emit_enabled:
                    image_emit_skipped += 1
                    image_emit_skip_reason = image_emit_skip_reason or "flags_disabled"
                    continue
                if not data:
                    failures += 1
                    if image_debug:
                        log.debug(
                            "DOCX_IMAGE_DEBUG doc_id=%s figure_id=%s rid=%s zip_member=%s data_missing=True",
                            doc_id,
                            figure_id,
                            rid,
                            zip_member,
                        )
                    continue
                image_emit_attempted += 1
                sha = hashlib.sha256(data).hexdigest()
                img_meta.update(
                    {
                        "block_type": "image",
                        "figure_id": figure_id,
                        "image_ref": image_ref,
                        "rid": rid,
                        "ext": ext,
                        "bytes_len": len(data),
                        "sha256": sha,
                    }
                )
                data = payload.get("data")
                if extract_images and doc_assets_dir is not None and data:
                    target = doc_assets_dir / fname
                    target.parent.mkdir(parents=True, exist_ok=True)
                    images_write_attempted += 1
                    try:
                        target.write_bytes(data)  # type: ignore[arg-type]
                        images_written += 1
                        img_meta["asset_path"] = str(target)
                    except Exception as exc:  # noqa: BLE001
                        failures += 1
                        if image_debug:
                            log.debug(
                                "DOCX_IMAGE_DEBUG doc_id=%s figure_id=%s rid=%s write_failed=%s target=%s",
                                doc_id,
                                figure_id,
                                rid,
                                exc,
                                target,
                            )
                        continue
                blocks.append(Block(type="image", text=figure_id, meta=img_meta))
                image_blocks_emitted += 1
                if image_debug:
                    log.debug(
                        "DOCX_IMAGE_DEBUG doc_id=%s rid=%s target=%s candidates=%s chosen=%s out=%s bytes=%s",
                        doc_id,
                        rid,
                        target_rel,
                        candidates,
                        zip_member,
                        img_meta.get("asset_path", ""),
                        len(data),
                    )
        if buffer:
            text_fragments.append(buffer)

        heading_applied = False
        for idx, frag in enumerate(text_fragments):
            frag_text = frag.rstrip("\n")
            if not frag_text.strip():
                continue
            if block_type == "heading" and not heading_applied:
                frag_text = _apply_heading_prefix(frag_text, numbering_prefix)
                heading_applied = True
            frag_meta = meta if block_type != "heading" or idx == 0 else _strip_heading_meta(meta)
            blocks.append(Block(type=block_type, text=frag_text, meta=frag_meta))

        if loader_debug and block_type == "heading":
            log.info(
                "DOCX_LOADER_DEBUG heading text=%s style=%s level=%s num_prefix=%s outline=%s numId=%s ilvl=%s",
                (heading_text_value[:80] + "...") if len(heading_text_value) > 80 else heading_text_value,
                style_name,
                heading_level,
                numbering_prefix,
                resolved_prefix,
                num_id,
                outline_level,
            )

    log.info(
        "DOCX_IMAGE_FLAGS doc_id=%s extract_images=%s inline_placeholders=%s figure_chunks=%s rag_assets_dir=%s",
        doc_id,
        extract_images,
        inline_flag,
        figure_flag,
        str(assets_root) if extract_images else "",
    )
    if emit_enabled:
        assets_dir_str = str(doc_assets_dir) if doc_assets_dir is not None else ""
        log.info(
            "DOCX_IMAGES_SUMMARY doc_id=%s blips_total=%s embed_rids=%s rels_total=%s rels_mapped=%s rels_hit=%s targets_resolved=%s zip_member_hit=%s zip_member_miss=%s bytes_read_ok=%s images_write_attempted=%s images_written=%s image_emit_attempted=%s image_blocks_emitted=%s image_emit_skipped=%s image_emit_skip_reason=%s failures=%s assets_dir=%s source=%s",
            doc_id,
            blips_total,
            embed_rids_count,
            rels_total,
            rels_mapped,
            rels_hit,
            targets_resolved,
            zip_member_hit,
            zip_member_miss,
            bytes_read_ok,
            images_write_attempted,
            images_written,
            image_emit_attempted,
            image_blocks_emitted,
            image_emit_skipped,
            image_emit_skip_reason or "",
            failures,
            assets_dir_str,
            abs_path,
        )
    else:
        log.info(
            "DOCX_IMAGES_SUMMARY doc_id=%s blips_total=%s embed_rids=%s rels_total=%s rels_mapped=%s rels_hit=%s targets_resolved=%s zip_member_hit=%s zip_member_miss=%s bytes_read_ok=%s images_write_attempted=%s images_written=%s image_emit_attempted=%s image_blocks_emitted=%s image_emit_skipped=%s image_emit_skip_reason=%s failures=%s assets_dir=%s source=%s",
            doc_id,
            blips_total,
            embed_rids_count,
            rels_total,
            rels_mapped,
            rels_hit,
            targets_resolved,
            zip_member_hit,
            zip_member_miss,
            bytes_read_ok,
            images_write_attempted,
            images_written,
            image_emit_attempted,
            image_blocks_emitted,
            image_emit_skipped or (embed_rids_count if not emit_enabled else 0),
            image_emit_skip_reason or ("flags_disabled" if not emit_enabled else ""),
            failures,
            "",
            abs_path,
        )

    if not blocks:
        return items

    cleaned_blocks = clean_blocks("docx", blocks)

    heading_stack: List[Dict] = []
    current_item_lines: List[str] = []
    current_meta: Dict[str, object] = {}

    def _filtered_heading_stack() -> List[Dict]:
        # IMPORTANT: ignore level 0 document title headings so they don't become the major section key
        return [h for h in heading_stack if int(h.get("level") or 0) >= 1]

    def _apply_heading_context(meta: Dict[str, object]) -> Dict[str, object]:
        filtered = _filtered_heading_stack()
        meta["heading_path"] = [h["title"] for h in filtered]
        meta["section_heading"] = filtered[-1]["title"] if filtered else None
        meta["heading_level_of_section"] = filtered[-1]["level"] if filtered else None
        meta["numbering_prefix_of_section"] = filtered[-1].get("numbering") if filtered else None
        return meta

    def flush_item():
        nonlocal current_item_lines, current_meta
        if not current_item_lines:
            return
        text_out = clean_text("\n".join(current_item_lines), preserve_tables=False)
        if text_out:
            items.append({"text": text_out, "metadata": dict(current_meta)})
        current_item_lines = []

    for blk in cleaned_blocks:
        if blk.meta.get("block_type") == "image" or blk.type == "image":
            flush_item()
            image_meta = _apply_heading_context(dict(base_meta))
            image_meta.update(blk.meta)
            items.append({"text": blk.text, "metadata": image_meta})
            continue

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

            current_meta = _apply_heading_context(current_meta)

        else:
            if not current_item_lines:
                current_meta = _apply_heading_context(dict(base_meta))

            current_item_lines.append(blk.text)

    flush_item()
    return items
