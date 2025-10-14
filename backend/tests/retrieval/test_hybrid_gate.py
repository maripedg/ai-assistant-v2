from types import SimpleNamespace

from backend.core.services.retrieval_service import RetrievalService


class DummyVS:
    def __init__(self, scores, text_len=50):
        self._scores = scores
        self._text = "x" * text_len

    def similarity_search_with_score(self, question, k):
        docs = [SimpleNamespace(page_content=self._text, metadata={}) for _ in self._scores]
        return list(zip(docs, self._scores))


class LLMEchoNoContext:
    def __init__(self, token):
        self.token = token

    def generate(self, prompt: str) -> str:
        return self.token


class LLMStatic:
    def __init__(self, text):
        self.text = text

    def generate(self, prompt: str) -> str:
        return self.text


def make_service(max_score, hybrid_gate, text_len=400):
    cfg = {
        "retrieval": {
            "distance": "dot_product",
            "score_mode": "normalized",
            "thresholds": {"low": 0.0, "high": 1.0},  # force hybrid for any score in [0,1)
            "short_query": {"max_tokens": 2, "threshold_low": 0.0, "threshold_high": 1.0},
            "hybrid": {
                "max_context_chars": 8000,
                "max_chunks": 6,
                "min_tokens_per_chunk": 10,
                **hybrid_gate,
            },
            "top_k": 2,
        },
        "prompts": {
            "hybrid": {"system": "HYBRID"},
            "rag": {"system": "RAG"},
            "fallback": {"system": "FALLBACK"},
            "no_context_token": "__NO_CONTEXT__",
        },
    }
    vs = DummyVS([max_score], text_len=text_len)
    return cfg, vs


def test_gate_min_similarity_triggers_fallback():
    cfg, vs = make_service(0.10, {"min_similarity_for_hybrid": 0.5})
    svc = RetrievalService(vs, LLMStatic("answer"), LLMStatic("fb"), cfg)
    result = svc.answer("q")
    assert result["mode"] == "fallback"
    assert result["decision_explain"]["reason"] == "gate_failed_min_similarity"


def test_gate_min_chunks_triggers_fallback():
    # Make text too short so no chunks pass min_tokens_per_chunk -> 0 used_chunks
    cfg, vs = make_service(0.30, {"min_chunks_for_hybrid": 1}, text_len=5)
    svc = RetrievalService(vs, LLMStatic("answer"), LLMStatic("fb"), cfg)
    result = svc.answer("q")
    assert result["mode"] == "fallback"
    assert result["decision_explain"]["reason"] == "gate_failed_min_chunks"


def test_gate_min_total_context_chars_triggers_fallback():
    # Ensure a chunk exists but require too many chars
    cfg, vs = make_service(0.40, {"min_total_context_chars": 5000}, text_len=200)
    svc = RetrievalService(vs, LLMStatic("answer"), LLMStatic("fb"), cfg)
    result = svc.answer("q")
    assert result["mode"] == "fallback"
    assert result["decision_explain"]["reason"] == "gate_failed_min_context"


def test_no_context_token_enforces_fallback():
    cfg, vs = make_service(0.60, {})
    primary = LLMEchoNoContext("__NO_CONTEXT__")
    fallback = LLMStatic("fallback answer")
    svc = RetrievalService(vs, primary, fallback, cfg)
    result = svc.answer("q")
    assert result["mode"] == "fallback"
    assert result["answer"] == "fallback answer"
    assert result["decision_explain"]["reason"] == "llm_no_context_token"

