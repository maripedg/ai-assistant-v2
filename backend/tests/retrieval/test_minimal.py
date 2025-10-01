from types import SimpleNamespace

from backend.core.services.retrieval_service import RetrievalService


class DummyVS:
    def __init__(self, scores, distance="dot_product"):
        self._scores = scores
        self._distance_label = distance

    def similarity_search_with_score(self, question, k):
        docs = [SimpleNamespace(page_content="text" * 150, metadata={}) for _ in self._scores]
        return list(zip(docs, self._scores))


class DummyLLM:
    def generate(self, prompt: str) -> str:
        return "answer"


def make_service(max_score, low=0.2, high=0.5):
    cfg = {
        "retrieval": {
            "distance": "dot_product",
            "score_mode": "normalized",
            "thresholds": {"low": low, "high": high},
            "short_query": {"max_tokens": 2, "threshold_low": low, "threshold_high": high},
            "hybrid": {"max_context_chars": 8000, "max_chunks": 6, "min_tokens_per_chunk": 10},
            "top_k": 2,
        },
        "prompts": {
            "hybrid": {"system": "HYBRID"},
            "rag": {"system": "RAG"},
            "fallback": {"system": "FALLBACK"},
        },
    }
    vs = DummyVS([max_score])
    llm = DummyLLM()
    return RetrievalService(vs, llm, llm, cfg)


def get_mode(score):
    svc = make_service(score)
    result = svc.answer("test question")
    return result["decision_explain"]["mode"]


def test_decision_rag():
    assert get_mode(0.60) == "rag"


def test_decision_hybrid():
    assert get_mode(0.30) == "hybrid"


def test_decision_fallback():
    assert get_mode(0.10) == "fallback"
