"""Demo for procedure-aware DOCX chunk packing."""

from backend.ingest.chunking.structured_docx_chunker import chunk_structured_docx_items


def main() -> None:
    text = "\n".join(
        [
            "7. SOP2 - Restart",
            "7.1 Overview",
            "Overview details line one.",
            "7.2 Steps",
            "1. Validate status",
            "2. Restart service",
            "3. Confirm health",
            "7.3 Notes:",
            "Operational notes go here.",
            "8. SOP3 - Validate",
            "8.1 Overview",
            "Validation description.",
        ]
    )
    items = [{"text": text, "metadata": {"source": "/tmp/demo.docx", "content_type": "docx", "heading_path": ["7. SOP2 - Restart"]}}]
    cfg = {"drop_toc": True}
    chunks = chunk_structured_docx_items(items, cfg, effective_max_tokens=24)
    for idx, ch in enumerate(chunks, start=1):
        meta = ch["metadata"]
        print(f"--- Chunk {idx} ---")
        print("section_range:", meta.get("section_range"))
        print("is_split:", meta.get("is_split"), "split_reason:", meta.get("split_reason"))
        print(ch["text"])
        print()


if __name__ == "__main__":
    main()
