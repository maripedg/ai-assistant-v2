from app.views.chat import __init__ as chat_view


def test_compute_confidence_buckets():
    decision = {"max_similarity": 0.82, "threshold_low": 0.25, "threshold_high": 0.6}
    result = chat_view._compute_confidence(decision, "rag")
    assert result["label"] == "High"
    assert 0.99 <= result["ratio"] <= 1.0


def test_compute_confidence_fallback_forces_low():
    decision = {"max_similarity": 0.9, "threshold_low": 0.2, "threshold_high": 0.6}
    result = chat_view._compute_confidence(decision, "fallback")
    assert result["label"] == "Low"
    assert result["ratio"] == 0.0


def test_filter_chunks_by_threshold():
    decision = {"threshold_low": 0.3, "max_similarity": 0.5}
    chunks = [{"score": 0.35}, {"score": 0.25}]
    filtered = chat_view._filter_evidence_chunks(chunks, decision, "rag")
    assert filtered == [chunks[0]]


def test_filter_hides_sources_on_fallback():
    decision = {"threshold_low": 0.2, "max_similarity": 0.1}
    chunks = [{"score": 0.9}]
    filtered = chat_view._filter_evidence_chunks(chunks, decision, "fallback")
    assert filtered == []


def test_select_answer_prefers_primary():
    payload = {"answer": "Primary", "answer2": "Secondary", "answer3": "Tertiary"}
    text, field = chat_view._select_answer_text(payload)
    assert text == "Primary"
    assert field == "answer"


def test_select_answer_falls_back_to_answer2():
    payload = {"answer": "   ", "answer2": "Secondary", "answer3": "Tertiary"}
    text, field = chat_view._select_answer_text(payload)
    assert text == "Secondary"
    assert field == "answer2"


def test_select_answer_placeholder_when_all_empty():
    payload = {"answer": "  ", "answer2": None, "answer3": ""}
    text, field = chat_view._select_answer_text(payload)
    assert text is None
    assert field == "none"


def test_fallback_mode_keeps_answer_text():
    payload = {"answer": "Backup answer."}
    text, field = chat_view._select_answer_text(payload)
    assert text == "Backup answer."
    assert field == "answer"
    decision = {"threshold_low": 0.5, "max_similarity": 0.1}
    chunks = [{"score": 0.95, "snippet": "Example"}]
    assert chat_view._filter_evidence_chunks(chunks, decision, "fallback") == []
