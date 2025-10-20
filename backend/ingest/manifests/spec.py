"""Manifest schema validation and expansion.

Purpose
- Read a JSONL manifest, validate entries, and expand globs into absolute file paths.

Contract
- export: validate_and_expand_manifest(manifest_path: str) -> list[str]
"""

from __future__ import annotations

import json
import os
import glob
from pathlib import Path
from typing import List, Dict, Any


def _iter_entries(manifest_path: Path) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    with manifest_path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:  # noqa: BLE001
                raise ValueError(f"Invalid JSON at line {lineno}: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError(f"Manifest line {lineno} must be a JSON object")
            if not data.get("path"):
                raise ValueError(f"Manifest line {lineno} missing 'path'")
            entries.append(data)
    return entries


def _has_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in ("*", "?", "["))


def validate_and_expand_manifest(manifest_path: str) -> List[str]:
    """Return the list of resolved absolute file paths referenced by a manifest.

    Rules
    - Each JSONL object must have field `path` (string), optional `tags` (list), optional `content_type`.
    - Globs are expanded relative to the manifest directory if not absolute.
    - Non-existing files are filtered out; a warning list is printed when any are skipped.
    """
    mp = Path(manifest_path).expanduser().resolve()
    if not mp.exists():
        raise FileNotFoundError(f"Manifest not found: {mp}")

    entries = _iter_entries(mp)
    base_dir = mp.parent
    resolved: List[str] = []
    missing: List[str] = []

    for ent in entries:
        raw = ent["path"]
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (base_dir / p).resolve()
        pattern = str(p)

        if _has_glob(pattern):
            matches = sorted({os.path.abspath(m) for m in glob.glob(pattern, recursive=True)})
            for m in matches:
                if os.path.exists(m):
                    resolved.append(m)
                else:
                    missing.append(m)
            continue

        if os.path.exists(pattern):
            resolved.append(os.path.abspath(pattern))
        else:
            missing.append(pattern)

    if missing:
        print(f"[manifest] Skipped {len(missing)} missing file(s)")
    return resolved
