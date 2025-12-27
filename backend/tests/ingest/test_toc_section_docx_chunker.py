from backend.ingest.chunking.toc_section_docx_chunker import chunk_docx_toc_sections


def test_num_prefix_major_boundaries():
    items = [
        {
            "text": "Standard Operating Procedure (SOP)",
            "metadata": {"section_heading": "Standard Operating Procedure (SOP)", "heading_level_of_section": 1},
        },
        {"text": "Preamble line 1", "metadata": {}},
        {"text": "Preamble line 2", "metadata": {}},
        {
            "text": "SOP1 - Restart",
            "metadata": {"section_heading": "SOP1 - Restart", "heading_level_of_section": 1, "num_prefix": "6", "outline_level": 0},
        },
        {
            "text": "Overview",
            "metadata": {"section_heading": "Overview", "heading_level_of_section": 2, "num_prefix": "6.1", "outline_level": 1},
        },
        {"text": "Step one", "metadata": {}},
        {
            "text": "SOP2 - Next",
            "metadata": {"section_heading": "SOP2 - Next", "heading_level_of_section": 1, "num_prefix": "7", "outline_level": 0},
        },
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 512}, source_meta={})
    assert len(chunks) == 3
    assert "SOP1" not in chunks[0]["text"]
    assert chunks[1]["text"].splitlines()[0].startswith("Section: 6")
    assert "Overview" in chunks[1]["text"]
    assert chunks[2]["text"].splitlines()[0].startswith("Section: 7")
