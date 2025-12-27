"""Example runner for toc_section_docx_chunker."""

import sys
import os

from backend.ingest.loaders.docx_loader import load
from backend.ingest.chunking.toc_section_docx_chunker import chunk_docx_toc_sections


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python run_toc_section_docx_chunker.py <path.docx>")
        return
    path = sys.argv[1]
    items = load(path)
    cfg = {"effective_max_tokens": int(os.getenv("MAX_TOKENS") or 512)}
    chunks = chunk_docx_toc_sections(items, cfg=cfg, source_meta={"source": path})
    for idx, ch in enumerate(chunks[:3], start=1):
        print(f"--- Chunk {idx} ---")
        print("meta:", {k: ch["metadata"].get(k) for k in ["section_strategy", "section_number", "section_title", "is_split", "split_part"]})
        lines = (ch["text"] or "").splitlines()
        print("lines:", len(lines), "tokens~", len(ch["text"]) // 4)
        for ln in lines[:5]:
            print(ln)
        print()


if __name__ == "__main__":
    main()
