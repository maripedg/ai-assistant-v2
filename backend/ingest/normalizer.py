"""Metadata normalizer implementation.

Purpose
- Normalize/standardize loader metadata before chunking/embedding.

Contract
- export: normalize_metadata(item: dict) -> dict
- export: infer_content_type_from_ext(path: str) -> str
"""

from __future__ import annotations

import os
from typing import Dict, Optional


_ALLOWED_TYPES = {"pdf", "docx", "pptx", "xlsx", "html", "txt"}
_MIME_TO_TYPE = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/html": "html",
    "text/plain": "txt",
    "text/markdown": "txt",
}


def infer_content_type_from_ext(path: str) -> str:
    """Infer simplified content type token from path extension.

    Returns one of: pdf, docx, pptx, xlsx, html, txt.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext == ".pptx":
        return "pptx"
    if ext == ".xlsx":
        return "xlsx"
    if ext in {".html", ".htm"}:
        return "html"
    return "txt"


def _normalize_content_type(raw: Optional[str], source_path: str) -> str:
    # Map MIME to simplified types
    if raw:
        t = _MIME_TO_TYPE.get(raw.lower()) or raw.lower()
        if t in _ALLOWED_TYPES:
            return t
    # Fallback to extension
    t = infer_content_type_from_ext(source_path)
    if t in _ALLOWED_TYPES:
        return t
    raise ValueError(f"Unsupported content_type for source={source_path!r}")


def _require_int(meta: Dict, key: str) -> int:
    if key not in meta or not isinstance(meta[key], int):
        raise ValueError(f"metadata.{key} must be int")
    return meta[key]


def _require_str(meta: Dict, key: str) -> str:
    if key not in meta or not isinstance(meta[key], str):
        raise ValueError(f"metadata.{key} must be str")
    return meta[key]


def normalize_metadata(item: Dict) -> Dict:
    """Return a normalized copy of a loader item.

    Ensures:
    - metadata.source is absolute
    - metadata.content_type is one of allowed tokens
    - adds metadata.chunk_id (None placeholder)
    - leaves metadata.lang as None if missing
    - validates type-specific keys
    """
    text = item.get("text", "")
    meta = dict(item.get("metadata") or {})

    if "source" not in meta or not isinstance(meta["source"], str):
        raise ValueError("metadata.source is required and must be a string path")
    source_abs = os.path.abspath(meta["source"])
    meta["source"] = source_abs

    # content_type normalization
    meta["content_type"] = _normalize_content_type(meta.get("content_type"), source_abs)

    # chunk_id placeholder
    meta.setdefault("chunk_id", None)

    # lang optional
    if "lang" not in meta or meta.get("lang") in ("", None):
        meta["lang"] = None

    ctype = meta["content_type"]
    if ctype == "pdf":
        # page (int) required, has_ocr (bool) default False
        _require_int(meta, "page")
        if not isinstance(meta.get("has_ocr", False), bool):
            meta["has_ocr"] = False
    elif ctype == "pptx":
        _require_int(meta, "slide_number")
        if not isinstance(meta.get("has_notes", False), bool):
            meta["has_notes"] = False
    elif ctype == "xlsx":
        _require_str(meta, "sheet_name")
        _require_int(meta, "n_rows")
        _require_int(meta, "n_cols")
    elif ctype == "html":
        # canonical_url optional str; section_path required
        if "canonical_url" in meta and meta["canonical_url"] is not None and not isinstance(meta["canonical_url"], str):
            raise ValueError("metadata.canonical_url must be str if provided")
        _require_str(meta, "section_path")
    else:
        # txt/docx: no strict extra requirements beyond presence of source/content_type
        pass

    return {"text": text, "metadata": meta}
