from pathlib import Path

from backend.ingest.loaders import docx_loader
from backend.ingest.loaders.chunking.toc_section_docx_chunker import chunk_docx_toc_sections


def test_toc_hierarchy_sop4_sections(monkeypatch):
    doc_path = Path("data/docs/docx/ITC_Salam_MS_BRM_MonthlyBillRun_v1.0.docx")
    if not doc_path.exists():
        return
    monkeypatch.setenv("DOCX_INLINE_FIGURE_PLACEHOLDERS", "1")

    items = docx_loader.load(str(doc_path))
    chunks = chunk_docx_toc_sections(items, cfg={"effective_max_tokens": 512}, source_meta={"doc_id": "doc"})
    sop4_chunks = [ch for ch in chunks if (ch.get("text") or "").startswith("Procedure: 4")]

    assert any("Import the master billing Individual sheet" in ch["text"] for ch in sop4_chunks)
    assert any("Import the master billing consolidated sheet" in ch["text"] for ch in sop4_chunks)
    assert any("Merge the data with the common table" in ch["text"] for ch in sop4_chunks)
    assert all(ch["text"].splitlines()[0].startswith("Procedure: 4") for ch in sop4_chunks)
    assert all(any(line.startswith("Path:") for line in ch["text"].splitlines()) for ch in sop4_chunks)
    assert any("[FIGURE:" in ch["text"] for ch in sop4_chunks)
