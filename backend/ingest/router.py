"""Loader router implementation.

Purpose
- Provide a single entrypoint to choose an appropriate loader based on file extension.

Contract
- export: route_and_load(path: str) -> list[dict]
- Items follow the common loader item shape: {"text": str, "metadata": dict}
"""

from __future__ import annotations

import importlib
import os
from typing import List, Dict


_EXT_TO_LOADER = {
    ".pdf": "pdf_loader",
    ".docx": "docx_loader",
    ".pptx": "pptx_loader",
    ".xlsx": "xlsx_loader",
    ".html": "html_loader",
    ".htm": "html_loader",
    ".txt": "txt_loader",
    ".md": "txt_loader",
}


def _resolve_loader_module(ext: str) -> str:
    name = _EXT_TO_LOADER.get(ext.lower(), "txt_loader")
    return f"backend.ingest.loaders.{name}"


def route_and_load(path: str) -> List[Dict]:
    """Select a loader by file extension, load items, and return them.

    - Resolves `path` to an absolute path
    - Chooses loader using a small extension→module mapping
    - Falls back to the text loader for unknown extensions
    - Wraps exceptions adding the resolved path for easier debugging
    """
    abs_path = os.path.abspath(path)
    _, ext = os.path.splitext(abs_path)
    module_path = _resolve_loader_module(ext)
    try:
        mod = importlib.import_module(module_path)
        return mod.load(abs_path)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to load file via {module_path} | path={abs_path}: {exc}") from exc
