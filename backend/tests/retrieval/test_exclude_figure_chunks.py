from types import SimpleNamespace

from backend.core.services.retrieval_service import RetrievalService


class DummyVS:
    def similarity_search_with_score(self, question, k, **kwargs):
        text_doc = SimpleNamespace(
            page_content="Text chunk content with steps.",
            metadata={"chunk_id": "text1", "doc_id": "doc-text", "chunk_type": "text"},
        )
        fig_doc = SimpleNamespace(
            page_content="Figure doc content",
            metadata={"chunk_id": "fig1", "doc_id": "doc-fig", "chunk_type": "figure"},
        )
        return [(text_doc, 0.9), (fig_doc, 0.8)]


class DummyLLM:
    def generate(self, prompt: str) -> str:
        return "answer"


def _make_service():
    cfg = {
        "retrieval": {
            "distance": "cosine",
            "score_mode": "normalized",
            "thresholds": {"low": 0.0, "high": 0.1},
            "short_query": {"max_tokens": 3, "threshold_low": 0.0, "threshold_high": 0.1},
            "hybrid": {
                "max_context_chars": 8000,
                "max_chunks": 6,
                "min_tokens_per_chunk": 0,
                "min_similarity_for_hybrid": 0.0,
                "min_chunks_for_hybrid": 0,
                "min_total_context_chars": 10,
                "exclude_chunk_types_from_llm": ["figure"],
            },
            "top_k": 4,
        },
        "prompts": {"hybrid": {"system": ""}, "rag": {"system": ""}, "fallback": {"system": ""}},
    }
    return RetrievalService(DummyVS(), DummyLLM(), DummyLLM(), cfg)


def test_figure_chunks_excluded_from_context():
    svc = _make_service()
    result = svc.answer("test question")
    used = result["used_chunks"]
    metas = result["retrieved_chunks_metadata"]

    assert any(m.get("chunk_type") == "figure" for m in metas)
    assert all(c.get("chunk_id") != "fig1" for c in used)
    assert any(c.get("chunk_id") == "text1" for c in used)
    assert result["mode"] != "fallback"
