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
