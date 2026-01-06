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


def test_image_placeholder_and_figure_chunk(monkeypatch):
    monkeypatch.setenv("DOCX_INLINE_FIGURE_PLACEHOLDERS", "1")
    monkeypatch.setenv("DOCX_FIGURE_CHUNKS", "1")
    items = [
        {
            "text": "1 Intro",
            "metadata": {
                "section_heading": "Intro",
                "heading_level_of_section": 1,
                "num_prefix": "1",
                "outline_level": 0,
                "doc_id": "doc",
            },
        },
        {"text": "Paragraph before", "metadata": {"doc_id": "doc"}},
        {"text": "doc_img_001", "metadata": {"block_type": "image", "figure_id": "doc_img_001", "image_ref": "doc/img_001.png", "doc_id": "doc"}},
        {"text": "Paragraph after", "metadata": {"doc_id": "doc"}},
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 512}, source_meta={"doc_id": "doc"})
    text_chunks = [c for c in chunks if (c.get("metadata") or {}).get("chunk_type") != "figure"]
    figure_chunks = [c for c in chunks if (c.get("metadata") or {}).get("chunk_type") == "figure"]

    assert any("[FIGURE:doc_img_001]" in c["text"] for c in text_chunks)
    assert figure_chunks
    fig_meta = figure_chunks[0]["metadata"]
    parent_idx = fig_meta.get("parent_chunk_local_index")
    assert parent_idx == text_chunks[0]["metadata"].get("chunk_local_index")
    assert fig_meta.get("parent_chunk_id") == f"doc_chunk_{parent_idx}"
    assert fig_meta.get("image_ref") == "doc/img_001.png"
    assert figure_chunks[0]["text"].startswith("Figure doc_img_001 for")


def test_no_placeholders_when_flags_disabled(monkeypatch):
    monkeypatch.setenv("DOCX_INLINE_FIGURE_PLACEHOLDERS", "0")
    monkeypatch.setenv("DOCX_FIGURE_CHUNKS", "0")
    items = [
        {
            "text": "1 Intro",
            "metadata": {
                "section_heading": "Intro",
                "heading_level_of_section": 1,
                "num_prefix": "1",
                "outline_level": 0,
                "doc_id": "doc",
            },
        },
        {"text": "Paragraph before", "metadata": {"doc_id": "doc"}},
        {"text": "doc_img_001", "metadata": {"block_type": "image", "figure_id": "doc_img_001", "image_ref": "doc/img_001.png", "doc_id": "doc"}},
        {"text": "Paragraph after", "metadata": {"doc_id": "doc"}},
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 512}, source_meta={"doc_id": "doc"})
    text = "\n".join(ch["text"] for ch in chunks)
    assert "[FIGURE:" not in text
    assert all((c.get("metadata") or {}).get("chunk_type") != "figure" for c in chunks)


def test_sop_boundaries_respected_even_with_nested_heading_levels():
    items = [
        {"text": "Document intro", "metadata": {"section_heading": "Document intro", "heading_level_of_section": 2}},
        {
            "text": "SOP 12: Connect to Testnap (CM-Batch)",
            "metadata": {"section_heading": "SOP 12: Connect to Testnap (CM-Batch)", "heading_level_of_section": 4, "num_prefix": "2.1", "outline_level": 3},
        },
        {"text": "Pre-check steps", "metadata": {}},
        {
            "text": "Validation checklist",
            "metadata": {"section_heading": "Validation checklist", "heading_level_of_section": 5, "num_prefix": "2.1.1", "outline_level": 4},
        },
        {"text": "Still in SOP12 body", "metadata": {}},
        {
            "text": "SOP 13 - Shutdown Procedure",
            "metadata": {"section_heading": "SOP 13 - Shutdown Procedure", "heading_level_of_section": 4, "num_prefix": "3.0", "outline_level": 3},
        },
        {"text": "SOP13 first step", "metadata": {}},
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 256}, source_meta={"doc_id": "doc"})
    headers = [ch["text"].splitlines()[0] for ch in chunks]
    assert any(h.startswith("Procedure: SOP12") for h in headers)
    assert any(h.startswith("Procedure: SOP13") for h in headers)
    sop12 = next(ch for ch in chunks if ch["text"].splitlines()[0].startswith("Procedure: SOP12"))
    sop13 = next(ch for ch in chunks if ch["text"].splitlines()[0].startswith("Procedure: SOP13"))
    assert "Still in SOP12 body" in sop12["text"]
    assert "Still in SOP12 body" not in sop13["text"]


def test_procedure_title_repeats_on_split(monkeypatch):
    monkeypatch.delenv("DOCX_INLINE_FIGURE_PLACEHOLDERS", raising=False)
    monkeypatch.delenv("DOCX_FIGURE_CHUNKS", raising=False)
    items = [
        {
            "text": "SOP 5: Long Procedure",
            "metadata": {"section_heading": "SOP 5: Long Procedure", "heading_level_of_section": 3, "num_prefix": "5", "outline_level": 2, "doc_id": "doc"},
        },
        {
            "text": "\n".join([f"Step {i} description with repeated guidance for operators" for i in range(1, 10)]),
            "metadata": {"doc_id": "doc"},
        },
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 24}, source_meta={"doc_id": "doc"})
    assert len(chunks) > 1
    assert all(ch["text"].splitlines()[0].startswith("Procedure: SOP5") for ch in chunks)
