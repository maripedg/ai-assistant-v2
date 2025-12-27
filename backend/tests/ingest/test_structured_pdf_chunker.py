# Regression tests for structured PDF chunking
from backend.ingest.chunking.structured_pdf_chunker import chunk_structured_pdf_items


def test_structured_pdf_removes_headers_and_rebuilds_paragraphs():
    items = [
        {
            "text": (
                "Confidential - Oracle Restricted\n"
                "SECTION ONE\n"
                "This is line 1\n"
                "continues here\n"
                "- bullet a\n"
                "Footer 1"
            ),
            "metadata": {"source": "/tmp/file.pdf", "content_type": "pdf", "page": 1},
        },
        {
            "text": (
                "Confidential - Oracle Restricted\n"
                "SECTION TWO\n"
                "Second page line\n"
                "wraps around\n"
                "Footer 1"
            ),
            "metadata": {"source": "/tmp/file.pdf", "content_type": "pdf", "page": 2},
        },
    ]
    chunker_cfg = {
        "type": "structured_pdf",
        "drop_repeated_headers_footers": True,
        "drop_toc": True,
        "token_safety_margin": 16,
    }
    chunks = chunk_structured_pdf_items(items, chunker_cfg, effective_max_tokens=64)

    texts = [c["text"] for c in chunks]
    # Repeated header/footer removed
    assert all("Confidential - Oracle Restricted" not in t for t in texts)
    assert all("Footer 1" not in t for t in texts)
    # Paragraphs reconstructed (wrapped line joined)
    assert any("This is line 1 continues here" in t for t in texts)
    # Bullets attached to preceding paragraph
    assert any("- bullet a" in t for t in texts)
    # Page metadata preserved
    assert any(c["metadata"].get("page_start") == 1 for c in chunks)
    assert any(c["metadata"].get("page_start") == 2 for c in chunks)


def test_structured_pdf_keeps_heading_with_body_and_repeats_on_split():
    items = [
        {
            "text": (
                "HEADER\n"
                "Procedure Steps\n"
                "Step one description goes here and keeps going.\n"
                "Step two continues the procedure details for testing."
            ),
            "metadata": {"source": "/tmp/file.pdf", "content_type": "pdf", "page": 1, "section_heading": "Procedure Steps"},
        }
    ]
    chunker_cfg = {
        "type": "structured_pdf",
        "drop_repeated_headers_footers": True,
        "drop_toc": True,
        "token_safety_margin": 16,
    }
    chunks = chunk_structured_pdf_items(items, chunker_cfg, effective_max_tokens=18)

    assert chunks
    # heading included and not alone
    assert all(ch["text"].splitlines()[0].strip().startswith("Procedure Steps") for ch in chunks)
    assert all(len(ch["text"].splitlines()) > 1 for ch in chunks)


def test_structured_pdf_overused_headings_reduce_micro_chunks():
    text = "\n".join(
        [
            "Heading A",
            "Line a1",
            "Heading B",
            "Line b1",
            "Heading C",
            "Line c1",
        ]
    )
    items = [{"text": text, "metadata": {"source": "/tmp/file.pdf", "content_type": "pdf", "page": 1}}]
    chunks = chunk_structured_pdf_items(items, {"drop_toc": True}, effective_max_tokens=64)

    assert len(chunks) <= 2
    assert all("Heading A" in ch["text"] and "Line a1" in ch["text"] for ch in chunks)


def test_structured_pdf_strong_numbered_headings_boundary():
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
    items = [{"text": text, "metadata": {"source": "/tmp/file.pdf", "content_type": "pdf", "page": 1}}]
    chunks = chunk_structured_pdf_items(items, {"drop_toc": True}, effective_max_tokens=24)

    assert len(chunks) >= 2
    assert chunks[0]["text"].splitlines()[0].startswith("7.")
    assert chunks[1]["text"].splitlines()[0].startswith("8.")
