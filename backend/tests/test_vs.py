from types import SimpleNamespace

from backend.providers.oci.vectorstore import OracleVSStore
from langchain_community.vectorstores.oraclevs import _coerce_metadata_value


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


def test_coerce_metadata_value_parses_json_strings():
    raw = '{"doc_id":"doc-42","chunk_id":"chunk-7","source":"/tmp/doc"}'
    meta = _coerce_metadata_value(raw, lambda v: v)
    assert meta["doc_id"] == "doc-42"
    assert meta["chunk_id"] == "chunk-7"
    assert meta["source"] == "/tmp/doc"


def test_coerce_metadata_value_handles_invalid_input():
    meta = _coerce_metadata_value("not-json", lambda v: v)
    assert meta == {}
