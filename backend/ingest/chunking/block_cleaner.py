from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

from backend.ingest.chunking.block_types import Block
from backend.ingest.chunking import toc_utils

DEBUG_CLEAN = (os.getenv("BLOCK_CLEAN_DEBUG") or "").lower() in {"1", "true", "yes", "on"}


def _is_numbered_list(line: str) -> bool:
    return bool(re.match(r"^\s*\d+[\).]\s+", line))


def _normalize_line(line: str) -> str:
    return " ".join((line or "").strip().split())


def _strip_toc(blocks: List[Block]) -> List[Block]:
    all_lines: List[str] = []
    boundaries: List[Tuple[int, int]] = []
    cursor = 0
    for blk in blocks:
        lines = blk.text.splitlines()
        start = cursor
        all_lines.extend(lines)
        cursor += len(lines)
        boundaries.append((start, cursor))

    kept_lines = toc_utils.strip_toc_region(all_lines, {"toc_stop_on_heading": True})
    keep_mask = [False] * len(all_lines)
    remaining: Dict[str, List[int]] = defaultdict(list)
    for idx, line in enumerate(all_lines):
        remaining[line].append(idx)
    for line in kept_lines:
        ids = remaining.get(line, [])
        if ids:
            keep_mask[ids.pop(0)] = True

    cleaned: List[Block] = []
    for (start, end), blk in zip(boundaries, blocks):
        lines_out = [all_lines[i] for i in range(start, end) if keep_mask[i]]
        txt = "\n".join(lines_out).strip()
        if txt:
            cleaned.append(Block(type=blk.type, text=txt, meta=dict(blk.meta)))
    return cleaned


def _detect_repeated(lines_by_page: Dict[int, List[str]], threshold: float) -> set:
    counts: Dict[str, int] = Counter()
    pages_seen: Dict[str, set] = defaultdict(set)
    for page, lines in lines_by_page.items():
        for ln in lines:
            norm = _normalize_line(ln)
            if not norm:
                continue
            if _is_numbered_list(norm):
                continue
            counts[norm] += 1
            pages_seen[norm].add(page)
    total_pages = len(lines_by_page)
    repeated: set = set()
    for ln, c in counts.items():
        pages = len(pages_seen.get(ln, set()))
        if total_pages and pages / total_pages >= threshold:
            repeated.add(ln)
    return repeated


def _remove_repeated(blocks: List[Block], mode: str) -> tuple[List[Block], List[str]]:
    if not blocks or mode != "pdf":
        return blocks, []
    lines_by_page: Dict[int, List[str]] = defaultdict(list)
    lines_global: List[str] = []
    for blk in blocks:
        page = blk.meta.get("page") if mode == "pdf" else 0
        for ln in blk.text.splitlines():
            lines_by_page[page].append(ln)
            lines_global.append(ln)
    repeated = _detect_repeated(lines_by_page, 0.6 if mode == "pdf" else 0.6)

    boilerplate_re = re.compile(r"(page\s+\d+\s+of\s+\d+|confidential|restricted|internal use only)", re.IGNORECASE)
    boiler_counts = Counter([_normalize_line(ln) for ln in lines_global if boilerplate_re.search(ln)])
    for ln, cnt in boiler_counts.items():
        if cnt > 1:
            repeated.add(ln)

    cleaned: List[Block] = []
    removed: List[str] = []
    for blk in blocks:
        kept_lines = []
        for ln in blk.text.splitlines():
            norm = _normalize_line(ln)
            if norm and norm in repeated:
                removed.append(ln)
                continue
            kept_lines.append(ln)
        txt = "\n".join([ln for ln in kept_lines if ln.strip()]).strip()
        if txt:
            cleaned.append(Block(type=blk.type, text=txt, meta=dict(blk.meta)))
    return cleaned, removed


def clean_blocks(mode: str, blocks: Iterable[Block]) -> List[Block]:
    mode = (mode or "docx").lower()
    blist = list(blocks)
    removed_counts: Dict[str, int] = {}
    removed_samples: Dict[str, List[str]] = {}

    before_lines = sum(len(blk.text.splitlines()) for blk in blist)
    blist = _strip_toc(blist)
    after_toc_lines = sum(len(blk.text.splitlines()) for blk in blist)
    removed_counts["toc_lines"] = max(0, before_lines - after_toc_lines)
    removed_samples["toc"] = []

    before_lines = after_toc_lines
    blist, rep_removed = _remove_repeated(blist, mode)
    after_repeated_lines = sum(len(blk.text.splitlines()) for blk in blist)
    removed_counts["repeated_lines"] = max(0, before_lines - after_repeated_lines)
    removed_samples["repeated"] = [ln[:200] for ln in rep_removed[:5]]

    # whitespace normalization
    normalized: List[Block] = []
    for blk in blist:
        lines = [ln.rstrip() for ln in blk.text.splitlines()]
        compact: List[str] = []
        last_blank = False
        for ln in lines:
            if not ln.strip():
                if last_blank:
                    continue
                last_blank = True
                continue
            last_blank = False
            compact.append(ln)
        txt = "\n".join(compact).strip("\n")
        if txt:
            normalized.append(Block(type=blk.type, text=txt, meta=dict(blk.meta)))
    blist = normalized

    removed_counts["empty_blocks"] = max(0, after_repeated_lines - sum(len(blk.text.splitlines()) for blk in blist))

    if DEBUG_CLEAN and blist:
        blk0 = blist[0]
        meta = blk0.meta.setdefault("debug_clean", {})
        meta["removed_counts"] = removed_counts
        meta["removed_samples"] = removed_samples

    return blist
