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
        {
            "text": "Overview",
            "metadata": {"section_heading": "Overview", "heading_level_of_section": 2, "num_prefix": "7.1", "outline_level": 1},
        },
        {"text": "Step two", "metadata": {}},
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 512}, source_meta={})
    assert len(chunks) == 3
    proc6 = next(ch for ch in chunks if ch["text"].splitlines()[1].startswith("Section: Overview"))
    proc7 = next(ch for ch in chunks if ch["text"].splitlines()[1].startswith("Section: Overview"))
    assert proc6["text"].splitlines()[0].startswith("Procedure:")
    assert proc7["text"].splitlines()[0].startswith("Procedure:")


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
            {
                "text": "Overview",
                "metadata": {
                    "section_heading": "Overview",
                    "heading_level_of_section": 2,
                    "numbering_prefix_of_section": "7.1",
                    "outline_level": 1,
                },
            },
            {"text": "Step one", "metadata": {}},
        ]
    )

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 512}, source_meta={})
    section6 = [ch for ch in chunks if "Overview" in ch["text"]][0]
    assert section6["text"].splitlines()[1].startswith("Section: Overview")


def test_step_level_heading3_chunks_and_section_normalization():
    items = [
        {
            "text": "SOP4: Import Master Billing Sheet PINBO schema",
            "metadata": {"section_heading": "SOP4: Import Master Billing Sheet PINBO schema", "heading_level_of_section": 1, "num_prefix": "4"},
        },
        {
            "text": "4.1 Prepare data",
            "metadata": {"section_heading": "Prepare data", "heading_level_of_section": 2, "num_prefix": "4.1"},
        },
        {
            "text": "4.1.1 Load the sheet",
            "metadata": {"section_heading": "Load the sheet", "heading_level_of_section": 3, "num_prefix": "4.1.1"},
        },
        {"text": "Step A body", "metadata": {}},
        {
            "text": "4.1.2 Validate the sheet",
            "metadata": {"section_heading": "Validate the sheet", "heading_level_of_section": 3, "num_prefix": "4.1.2"},
        },
        {"text": "Step B body", "metadata": {}},
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 512}, source_meta={"doc_id": "doc"})
    proc_chunks = [ch for ch in chunks if ch["text"].splitlines()[0].startswith("Procedure:")]
    assert len(proc_chunks) == 2
    step_411 = next(ch for ch in proc_chunks if "4.1.1 Load the sheet" in ch["text"])
    step_412 = next(ch for ch in proc_chunks if "4.1.2 Validate the sheet" in ch["text"])
    assert step_411["text"].splitlines()[1].startswith("Section: 4.1.1")
    assert step_412["text"].splitlines()[1].startswith("Section: 4.1.2")
    assert "Path:" in step_411["text"]
    assert "Path:" in step_412["text"]


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
    assert any(h.startswith("Procedure: SOP 12") for h in headers)
    assert any(h.startswith("Procedure: SOP 13") for h in headers)
    sop12 = next(ch for ch in chunks if ch["text"].splitlines()[0].startswith("Procedure: SOP 12"))
    sop13 = next(ch for ch in chunks if ch["text"].splitlines()[0].startswith("Procedure: SOP 13"))
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
    assert all(ch["text"].splitlines()[0].startswith("Procedure:") for ch in chunks)


def test_toc_section_admin_sections_filtered_by_heading():
    items = [
        {
            "text": "Document Control\nOwner: Ops\nVersion: 1.0",
            "metadata": {"section_heading": "Document Control", "heading_level_of_section": 1},
        },
        {
            "text": "Version History\nv1.0 Initial release",
            "metadata": {"section_heading": "Version History", "heading_level_of_section": 1},
        },
        {
            "text": "Procedure\nStep 1: Do the thing\nStep 2: Verify",
            "metadata": {"section_heading": "Procedure", "heading_level_of_section": 1},
        },
    ]

    cfg = {
        "effective_max_tokens": 256,
        "drop_admin_sections": True,
        "admin_sections": {
            "enabled": True,
            "match_mode": "heading_regex",
            "heading_regex": [r"(?i)^document control$", r"(?i)^version history$"],
            "stop_excluding_after_heading_regex": [r"(?i)^procedure$"],
        },
    }
    chunks = chunk_docx_toc_sections(items, cfg=cfg, source_meta={})
    text = "\n".join(ch["text"] for ch in chunks)
    assert "Document Control" not in text
    assert "Version History" not in text
    assert "Procedure" in text


def test_toc_section_numeric_major_boundaries_without_sop(monkeypatch):
    monkeypatch.setenv("DOCX_INLINE_FIGURE_PLACEHOLDERS", "1")
    items = [
        {
            "text": "4 Import Master Billing Sheet PINBO schema",
            "metadata": {
                "section_heading": "Import Master Billing Sheet PINBO schema",
                "heading_level_of_section": 1,
                "numbering_prefix_of_section": "4",
                "outline_level": 0,
                "doc_id": "doc",
            },
        },
        {
            "text": "4.1.1 Import the master billing Individual sheet in PINBO schema",
            "metadata": {
                "section_heading": "Import the master billing Individual sheet in PINBO schema",
                "heading_level_of_section": 2,
                "numbering_prefix_of_section": "4.1.1",
                "outline_level": 1,
                "doc_id": "doc",
            },
        },
        {
            "text": "doc_img_001",
            "metadata": {"block_type": "image", "figure_id": "doc_img_001", "image_ref": "doc/img_001.png", "doc_id": "doc"},
        },
        {
            "text": "5 Master Billing Data Validations—Monthly Table",
            "metadata": {
                "section_heading": "Master Billing Data Validations—Monthly Table",
                "heading_level_of_section": 1,
                "numbering_prefix_of_section": "5",
                "outline_level": 0,
                "doc_id": "doc",
            },
        },
        {
            "text": "5.1 Validate Billing Method",
            "metadata": {
                "section_heading": "Validate Billing Method",
                "heading_level_of_section": 2,
                "numbering_prefix_of_section": "5.1",
                "outline_level": 1,
                "doc_id": "doc",
            },
        },
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 256}, source_meta={"doc_id": "doc"})
    headers = [ch["text"].splitlines()[0] for ch in chunks]
    assert any(h.startswith("Procedure 4:") for h in headers)
    assert any(h.startswith("Procedure 5:") for h in headers)
    proc4 = next(ch for ch in chunks if "4.1.1 Import the master billing Individual sheet" in ch["text"])
    proc5 = next(ch for ch in chunks if "5.1 Validate Billing Method" in ch["text"])
    assert proc4["text"].splitlines()[1].startswith("Section: 4.1.1")
    assert proc5["text"].splitlines()[1].startswith("Section: 5.1")
    assert "[FIGURE:doc_img_001]" in proc4["text"]


def test_numeric_subheadings_any_depth_boundaries():
    items = [
        {
            "text": "4 Title",
            "metadata": {"section_heading": "Title", "heading_level_of_section": 1, "num_prefix": "4"},
        },
        {
            "text": "4.1 Step A",
            "metadata": {"section_heading": "Step A", "heading_level_of_section": 2, "num_prefix": "4.1"},
        },
        {"text": "Body A", "metadata": {}},
        {
            "text": "4.1.1 Step A.1",
            "metadata": {"section_heading": "Step A.1", "heading_level_of_section": 3, "num_prefix": "4.1.1"},
        },
        {"text": "Body A.1", "metadata": {}},
        {
            "text": "4.2 Step B",
            "metadata": {"section_heading": "Step B", "heading_level_of_section": 2, "num_prefix": "4.2"},
        },
        {"text": "Body B", "metadata": {}},
        {
            "text": "5 Title",
            "metadata": {"section_heading": "Title", "heading_level_of_section": 1, "num_prefix": "5"},
        },
    ]

    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 256}, source_meta={"doc_id": "doc"})
    proc4_chunks = [ch for ch in chunks if ch["text"].splitlines()[0].startswith("Procedure 4:")]
    assert len(proc4_chunks) == 2
    assert any("4.1.1 Step A.1" in ch["text"] for ch in proc4_chunks)
    assert any("4.2 Step B" in ch["text"] for ch in proc4_chunks)
