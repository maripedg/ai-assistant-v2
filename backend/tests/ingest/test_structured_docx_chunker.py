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
