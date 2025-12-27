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


def test_num_prefix_major_uses_numbering_prefix_of_section_and_separates_majors():
    items = []
    for i in range(1, 6):
        items.append(
            {
                "text": f"{i}. Heading {i}",
                "metadata": {"section_heading": f"Heading {i}", "heading_level_of_section": 1, "numbering_prefix_of_section": str(i)},
            }
        )
        items.append({"text": f"Body for {i}", "metadata": {}})
    items.extend(
        [
            {
                "text": "SOP1 - Restart",
                "metadata": {
                    "section_heading": "SOP1 - Restart",
                    "heading_level_of_section": 1,
                    "numbering_prefix_of_section": "6",
                    "outline_level": 0,
                },
            },
            {
                "text": "Overview",
                "metadata": {
                    "section_heading": "Overview",
                    "heading_level_of_section": 2,
                    "numbering_prefix_of_section": "6.1",
                    "outline_level": 1,
                },
            },
            {"text": "Step one", "metadata": {}},
            {
                "text": "SOP2 - Next",
                "metadata": {
                    "section_heading": "SOP2 - Next",
                    "heading_level_of_section": 1,
                    "numbering_prefix_of_section": "7",
                    "outline_level": 0,
                },
            },
        ]
    )

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 512}, source_meta={})
    titles = [ch["text"].splitlines()[0] for ch in chunks]
    assert any(t.startswith("Section: 5") for t in titles)
    assert any(t.startswith("Section: 6") for t in titles)
    assert any(t.startswith("Section: 7") for t in titles)
    section5 = [ch for ch in chunks if ch["text"].startswith("Section: 5")][0]
    section6 = [ch for ch in chunks if ch["text"].startswith("Section: 6")][0]
    assert "SOP1" not in section5["text"]
    assert "Overview" in section6["text"]
