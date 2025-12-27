# Regression tests for structured DOCX chunking
from backend.ingest.chunking.structured_docx_chunker import chunk_structured_docx_items


def test_structured_docx_groups_paragraph_and_bullets_and_drops_noise():
    items = [
        {
            "text": (
                "Confidential - Oracle Restricted\n"
                "Introduction\n"
                "This is intro line 1.\n"
                "This is intro line 2.\n"
                "- bullet one\n"
                "- bullet two\n"
                "\n"
                "Table of Contents\n"
                "Section A ..... 3"
            ),
            "metadata": {
                "source": "/tmp/doc.docx",
                "content_type": "docx",
                "section_heading": "Introduction",
                "heading_path": ["Introduction"],
            },
        },
        {
            "text": (
                "Confidential - Oracle Restricted\n"
                "Section One\n"
                "Paragraph A line 1\n"
                "Paragraph A line 2\n"
                "- item a"
            ),
            "metadata": {
                "source": "/tmp/doc.docx",
                "content_type": "docx",
                "section_heading": "Section One",
                "heading_path": ["Section One"],
            },
        },
    ]

    chunker_cfg = {
        "type": "structured_docx",
        "drop_toc": True,
        "drop_repeated_headers_footers": True,
        "drop_admin_sections": True,
        "token_safety_margin": 16,
    }
    chunks = chunk_structured_docx_items(items, chunker_cfg, effective_max_tokens=48)

    texts = [c["text"] for c in chunks]
    # TOC and repeated header should be removed
    assert all("Confidential - Oracle Restricted" not in t for t in texts)
    assert all("Section A" not in t for t in texts)
    # Bullets should be grouped with the paragraph
    assert any("- bullet one" in t and "This is intro line 2." in t for t in texts)
    # Metadata should carry heading path
    meta = chunks[0]["metadata"]
    assert meta.get("heading_path") == ["Introduction"]
    assert meta.get("section_heading") == "Introduction"


def test_structured_docx_keeps_heading_with_body_and_repeats_on_split():
    items = [
        {
            "text": (
                "Section Alpha\n"
                "First line of body.\n"
                "Second line continues description.\n"
                "Third line adds more detail for testing splits.\n"
                "Fourth line to ensure we exceed token limit."
            ),
            "metadata": {
                "source": "/tmp/doc.docx",
                "content_type": "docx",
                "section_heading": "Section Alpha",
                "heading_path": ["Section Alpha"],
            },
        }
    ]
    chunker_cfg = {
        "type": "structured_docx",
        "drop_toc": True,
        "drop_repeated_headers_footers": True,
        "token_safety_margin": 16,
    }
    chunks = chunk_structured_docx_items(items, chunker_cfg, effective_max_tokens=20)

    assert chunks, "expected chunks returned"
    # No heading-only chunks
    assert all(len(ch["text"].splitlines()) > 1 for ch in chunks)
    # Heading is included in every chunk
    assert all(ch["text"].splitlines()[0].strip().startswith("Section Alpha") for ch in chunks)
    # Body present alongside heading
    assert any("First line of body." in ch["text"] for ch in chunks)


def test_structured_docx_overused_headings_fall_back_to_block_accumulation():
    text = "\n".join(
        [
            "Heading One",
            "Content A line",
            "Heading Two",
            "Content B line",
            "Heading Three",
            "Content C line",
        ]
    )
    items = [{"text": text, "metadata": {"source": "/tmp/doc.docx", "content_type": "docx"}}]
    chunks = chunk_structured_docx_items(items, {"drop_toc": True}, effective_max_tokens=64)

    assert len(chunks) <= 2
    assert all(len(ch["text"].splitlines()) > 1 for ch in chunks)


def test_structured_docx_strong_numbered_headings_create_blocks():
    text = "\n".join(
        [
            "7. SOP2 - Restart",
            "Step one for SOP2",
            "Step two for SOP2",
            "8. SOP3 - Validate",
            "Step one for SOP3",
            "Step two for SOP3",
        ]
    )
    items = [{"text": text, "metadata": {"source": "/tmp/doc.docx", "content_type": "docx"}}]
    chunks = chunk_structured_docx_items(items, {"drop_toc": True}, effective_max_tokens=24)

    assert len(chunks) >= 2
    assert chunks[0]["text"].splitlines()[0].startswith("7.")
    assert chunks[1]["text"].splitlines()[0].startswith("8.")


def test_structured_docx_sop_section_keeps_block_and_repeats_heading_on_split():
    text = "\n".join(
        [
            "8. SOP3 - Restart of SPM in AIA (AIASessionPoolManager)",
            "8.1 Overview",
            "This section explains the restart process.",
            "8.2 Definitions",
            "SPM: Session Pool Manager component.",
            "- Step one: check logs.",
            "- Step two: restart service.",
            "- Step three: validate health.",
            "9. SOP4 - Next Section",
            "Follow-on content.",
        ]
    )
    items = [{"text": text, "metadata": {"source": "/tmp/doc.docx", "content_type": "docx"}}]
    chunks = chunk_structured_docx_items(items, {"drop_toc": True}, effective_max_tokens=32)

    # No heading-only chunks
    assert all(len(ch["text"].splitlines()) > 1 for ch in chunks)
    # All 8.* content stays together before 9.*
    assert chunks[0]["text"].splitlines()[0].startswith("8.")
    assert all("SOP3" in ch["text"] for ch in chunks if ch["text"].startswith("8."))
    # Splits (if any) keep heading prefix
    sop_chunks = [ch for ch in chunks if ch["text"].startswith("8.")]
    assert all(ch["text"].splitlines()[0].startswith("8.") for ch in sop_chunks)
