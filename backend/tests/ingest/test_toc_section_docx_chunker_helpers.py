from backend.ingest.loaders.chunking import toc_section_docx_chunker


def test_extract_numeric_heading_prefix():
    assert toc_section_docx_chunker._extract_numeric_heading_prefix("4 Title") == "4"
    assert toc_section_docx_chunker._extract_numeric_heading_prefix("4.1 Title") == "4.1"
    assert toc_section_docx_chunker._extract_numeric_heading_prefix("4.1.2 Title") == "4.1.2"
    assert toc_section_docx_chunker._extract_numeric_heading_prefix("SOP4: Title") is None


def test_chunk_docx_toc_sections_minimal():
    items = [
        {"text": "1 Intro", "metadata": {"section_heading": "Intro", "heading_level_of_section": 1, "num_prefix": "1"}},
        {"text": "Body line", "metadata": {}},
    ]
    chunks = toc_section_docx_chunker.chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 64}, source_meta={"doc_id": "doc"})
    assert chunks


def test_procedure_prefix_uses_section_heading_or_path():
    meta = {
        "section_heading": "Child Step",
        "heading_path": ["Parent Proc", "Child Step"],
        "procedure_title": "Parent Proc",
        "procedure_number": "7",
    }
    text = "Section: Child Step\nBody line"
    out = toc_section_docx_chunker._ensure_procedure_prefix(text, meta)
    assert out.splitlines()[0] == "Procedure 7: Child Step"

    meta_no_section = {
        "section_heading": "",
        "heading_path": ["Parent Proc", "Child Step"],
        "procedure_title": "Parent Proc",
        "procedure_number": "7",
    }
    out_no_section = toc_section_docx_chunker._ensure_procedure_prefix(text, meta_no_section)
    assert out_no_section.splitlines()[0] == "Procedure 7: Child Step"
