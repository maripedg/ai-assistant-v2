from types import SimpleNamespace

import pytest

from backend.core.services.retrieval_service import RetrievalService


DEFAULT_CFG = {
    "retrieval": {
        "top_k": 3,
        "threshold_low": 0.4,
        "threshold_high": 0.8,
        "max_context_chars": 500,
        "dedupe_by": "doc_id",
    }
}


class StubVectorStore:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def similarity_search_with_score(self, question, k):
        self.calls.append((question, k))
        return self.results


class RecordingLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            return ""
        return self.responses.pop(0)


class FailingLLM:
    def generate(self, _prompt: str) -> str:
        raise AssertionError("LLM should not be invoked in this mode")


def make_doc(doc_id: str, text: str) -> SimpleNamespace:
    metadata = {"doc_id": doc_id}
    return SimpleNamespace(page_content=text, metadata=metadata)


def make_service(vector_results, primary_llm, fallback_llm, cfg=None):
    store = StubVectorStore(vector_results)
    service = RetrievalService(store, primary_llm, fallback_llm, cfg or DEFAULT_CFG)
    return service, store


def test_extractive_mode_when_score_above_high_threshold():
    docs = [
        (make_doc("doc-1", "Top relevant passage."), 0.95),
        (make_doc("doc-2", "Secondary passage."), 0.82),
    ]
    service, store = make_service(docs, FailingLLM(), FailingLLM())

    result = service.answer("Explain extractive mode")

    assert result["mode"] == "extractive"
    assert "Top relevant passage." in result["answer"]
    assert result["answer2"] is None
    assert result["answer3"] is None
    assert len(result["retrieved_chunks_metadata"]) == 2
    assert store.calls == [("Explain extractive mode", 3)]


def test_rag_mode_with_mid_score():
    docs = [
        (make_doc("doc-1", "Helpful snippet."), 0.65),
        (make_doc("doc-2", "Additional context."), 0.55),
    ]
    primary = RecordingLLM(["contextual answer", "direct answer", "enriched answer"])
    fallback = RecordingLLM(["unused"])
    service, _ = make_service(docs, primary, fallback)

    result = service.answer("Explain rag mode")

    assert result["mode"] == "rag"
    assert result["answer"] == "contextual answer"
    assert result["answer2"] == "direct answer"
    assert result["answer3"] == "enriched answer"
    assert len(primary.prompts) == 3  # rag prompt, question, enrichment
    assert not fallback.prompts  # fallback unused


def test_fallback_mode_when_score_below_low_threshold():
    docs = [
        (make_doc("doc-1", "Barely relevant."), 0.1),
    ]
    primary = FailingLLM()
    fallback = RecordingLLM(["fallback completion"])
    service, _ = make_service(docs, primary, fallback)

    result = service.answer("Explain fallback mode")

    assert result["mode"] == "fallback"
    assert result["answer"] == "fallback completion"
    assert result["answer2"] == "fallback completion"
    assert result["answer3"] is None
    assert fallback.prompts == ["Explain fallback mode"]
