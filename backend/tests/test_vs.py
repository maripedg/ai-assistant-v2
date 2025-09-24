from types import SimpleNamespace

from backend.providers.oci.vectorstore import OracleVSStore


class DummyVectorClient:
    def __init__(self, results):
        self._results = results
        self.seen = []

    def similarity_search_with_score(self, query, k):
        self.seen.append((query, k))
        return self._results[:k]


def make_store(results):
    store = OracleVSStore.__new__(OracleVSStore)
    store.vs = DummyVectorClient(results)
    store._distance_label = "dot_product"
    return store


def test_similarity_search_returns_requested_k_and_scores_between_zero_and_one():
    dummy_docs = [
        (SimpleNamespace(id=i), score) for i, score in enumerate([0.9, 0.5, 0.1, 0.0, 1.0])
    ]
    store = make_store(dummy_docs)

    result = store.similarity_search_with_score("unit test query", k=3)

    assert len(result) == 3
    assert all(0.0 <= float(score) <= 1.0 for _, score in result)
    assert store.vs.seen == [("unit test query", 3)]
