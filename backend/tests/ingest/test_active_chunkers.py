from backend.ingest.chunking.char_chunker import chunk_text
from backend.ingest.chunking.token_chunker import chunk_text_by_tokens
from backend.ingest.chunking.structured_docx_chunker import chunk_structured_docx_items
from backend.ingest.chunking.structured_pdf_chunker import chunk_structured_pdf_items


def test_active_chunkers_importable_and_callable():
    chunks = chunk_text("abcdefgh", size=3, overlap=0)
    assert len(chunks) == 3
    token_chunks = chunk_text_by_tokens("one two three four", max_tokens=2, overlap=0.0)
    assert token_chunks
    assert all(isinstance(c, str) for c in token_chunks)
    assert chunk_structured_docx_items([], {}, effective_max_tokens=16) == []
    assert chunk_structured_pdf_items([], {}, effective_max_tokens=16) == []
