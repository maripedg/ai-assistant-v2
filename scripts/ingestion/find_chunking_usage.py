"""
Static usage map for ingestion/chunking modules.

Walks the repo, parses Python files via ast, and records imports/name references
to chunker modules. Outputs JSON to docs/engineering/ingestion_chunking_usage_map.json.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from typing import Dict, List, Set


CANDIDATES: Set[str] = {
    "backend.ingest.chunking",
    "backend.ingest.chunking.char_chunker",
    "backend.ingest.chunking.token_chunker",
    "backend.ingest.chunking.structured_docx_chunker",
    "backend.ingest.chunking.structured_pdf_chunker",
    "backend.ingest.chunking.toc_section_docx_chunker",
    "backend.ingest.loaders.chunking",
    "backend.ingest.loaders.chunking.char_chunker",
    "backend.ingest.loaders.chunking.token_chunker",
    "backend.ingest.loaders.chunking.structured_docx_chunker",
    "backend.ingest.loaders.chunking.structured_pdf_chunker",
    "backend.ingest.loaders.chunking.toc_section_docx_chunker",
    "backend.ingest.loaders.chunking.toc_utils",
}

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache"}


def _match_module(mod: str) -> str | None:
    for cand in CANDIDATES:
        if mod == cand or mod.startswith(f"{cand}."):
            return mod
    return None


def _analyze_file(path: Path) -> Dict[str, List[str]]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    imports: List[str] = []
    name_refs: List[str] = []
    alias_map: Dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                matched = _match_module(mod)
                if matched:
                    imports.append(matched)
                alias_name = alias.asname or mod.split(".")[-1]
                alias_map[alias_name] = mod
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            base_mod = node.module
            matched = _match_module(base_mod)
            if matched:
                imports.append(matched)
            for alias in node.names:
                alias_mod = f"{base_mod}.{alias.name}"
                alias_name = alias.asname or alias.name
                alias_map[alias_name] = alias_mod
        elif isinstance(node, ast.Name):
            alias_target = alias_map.get(node.id)
            if alias_target:
                matched = _match_module(alias_target)
                if matched:
                    name_refs.append(alias_target)
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                alias_target = alias_map.get(node.value.id)
                if alias_target:
                    full_attr = f"{alias_target}.{node.attr}"
                    matched = _match_module(full_attr)
                    if matched:
                        name_refs.append(full_attr)

    return {"imports": sorted(set(imports)), "name_refs": sorted(set(name_refs))}


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    output_path = repo_root / "docs" / "engineering" / "ingestion_chunking_usage_map.json"
    files_usage: Dict[str, Dict[str, List[str]]] = {}
    modules_usage: Dict[str, Dict[str, List[str]]] = {}

    for cand in CANDIDATES:
        modules_usage[cand] = {"imports": [], "name_refs": []}

    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            file_path = Path(dirpath) / filename
            try:
                usage = _analyze_file(file_path)
            except SyntaxError:
                continue
            if usage["imports"] or usage["name_refs"]:
                rel_path = str(file_path.relative_to(repo_root))
                files_usage[rel_path] = usage
                for mod in usage["imports"]:
                    modules_usage.setdefault(mod, {"imports": [], "name_refs": []})
                    modules_usage[mod]["imports"].append(rel_path)
                for mod in usage["name_refs"]:
                    modules_usage.setdefault(mod, {"imports": [], "name_refs": []})
                    modules_usage[mod]["name_refs"].append(rel_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump({"files": files_usage, "modules": modules_usage}, f, indent=2, sort_keys=True)

    print(f"Wrote usage map to {output_path}")


if __name__ == "__main__":
    main()
