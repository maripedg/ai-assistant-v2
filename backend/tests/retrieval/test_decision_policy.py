from types import SimpleNamespace

from backend.core.services.retrieval_service import RetrievalService


class DummyVS:
    def __init__(self, scores, distance):
        # scores: list of floats (raw)
        self._scores = scores
        self._distance_label = distance

    def similarity_search_with_score(self, question, k):
        docs = [SimpleNamespace(page_content="", metadata={}) for _ in self._scores]
        return list(zip(docs, self._scores))


class DummyLLM:
    def generate(self, prompt: str) -> str:
        return "ok"


def make_service(raw_scores, cfg):
    vs = DummyVS(raw_scores, cfg.get("retrieval", {}).get("distance", "dot_product"))
    llm = DummyLLM()
    return RetrievalService(vs, llm, llm, cfg)


def run_case(raw_scores, retrieval_cfg):
    svc = make_service(raw_scores, {"retrieval": retrieval_cfg})
    result = svc.answer("q")
    return result["mode"], result["decision_explain"]


def test_raw_dot_thresholds():
    cfg = {
        "distance": "dot_product",
        "score_mode": "raw",
        "thresholds": {"raw_dot_low": -0.50, "raw_dot_high": -0.20},
    }
    # A: raw dot = -0.10 -> rag
    mode, dec = run_case([-0.10], cfg)
    assert mode == "rag"
    assert dec["threshold_low"] == -0.50 and dec["threshold_high"] == -0.20
    # B: raw dot = -0.30 -> hybrid
    mode, dec = run_case([-0.30], cfg)
    assert mode == "hybrid"
    # C: raw dot = -0.60 -> fallback
    mode, dec = run_case([-0.60], cfg)
    assert mode == "fallback"


def test_normalized_thresholds():
    cfg = {
        "distance": "dot_product",
        "score_mode": "normalized",
        "thresholds": {"low": 0.20, "high": 0.45},
    }
    # D: normalized 0.50 -> rag (use raw 0.0 maps to 0.5 for dot)
    mode, dec = run_case([0.0], cfg)
    assert mode == "rag"
    # E: normalized 0.30 -> hybrid (use raw -0.4 maps to 0.3)
    mode, dec = run_case([-0.4], cfg)
    assert mode == "hybrid"
    # F: normalized 0.10 -> fallback (use raw -0.8 maps to 0.1)
    mode, dec = run_case([-0.8], cfg)
    assert mode == "fallback"

