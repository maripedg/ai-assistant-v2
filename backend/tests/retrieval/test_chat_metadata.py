from types import SimpleNamespace

from backend.core.services.retrieval_service import RetrievalService


class StubVS:
    def __init__(self, docs):
        self.docs = docs

    def similarity_search_with_score(self, question, k):
        return self.docs


class StubLLM:
    def __init__(self, text="answer"):
        self.text = text
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.text


def make_service():
    meta = {
        "doc_id": "doc-123",
        "source": "test-source",
        "chunk_id": "chunk-abc",
        "section_heading": "Intro",
        "heading_path": "Intro>Details",
    }
    doc = SimpleNamespace(page_content="paragraph " * 50, metadata=meta)
    docs = [(doc, 0.9)]
    cfg = {
        "retrieval": {
            "distance": "cosine",
            "score_mode": "normalized",
            "thresholds": {"low": 0.0, "high": 0.0},
            "hybrid": {"max_context_chars": 8000, "max_chunks": 6, "min_tokens_per_chunk": 1},
            "top_k": 3,
            "dedupe_by": "doc_id",
        },
        "prompts": {"hybrid": {"system": ""}, "rag": {"system": ""}, "fallback": {"system": ""}},
    }
    llm = StubLLM()
    return RetrievalService(StubVS(docs), llm, llm, cfg)


def test_chat_response_carries_chunk_metadata():
    service = make_service()
    result = service.answer("sample question")

    metas = result["retrieved_chunks_metadata"]
    assert metas and metas[0]["chunk_id"] == "chunk-abc"
    assert metas[0]["source"] == "test-source"
    assert metas[0]["section_heading"] == "Intro"
    assert metas[0]["heading_path"] == "Intro>Details"

    used = result["used_chunks"]
    assert used and used[0]["chunk_id"] == "chunk-abc"
    assert used[0]["source"] == "test-source"


def test_build_metas_parses_string_metadata():
    service = make_service()
    raw_meta = '{"doc_id": "doc-xyz", "source": "string-source", "chunk_id": "chunk-xyz"}'
    doc = SimpleNamespace(page_content="text block", metadata=raw_meta)
    metas = service._build_metas([(doc, 0.5)])
    assert metas[0]["chunk_id"] == "chunk-xyz"
    assert metas[0]["source"] == "string-source"
    assert metas[0]["doc_id"] == "doc-xyz"
