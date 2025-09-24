import pytest

from backend.app.routers import health


def test_healthz_reports_reasons(monkeypatch):
    responses = {
        "embeddings": {"info": "emb info", "is_up": False, "reason": "ValueError"},
        "llm_primary": {"info": "primary info", "is_up": True, "reason": None},
        "llm_fallback": {"info": "fallback info", "is_up": False, "reason": "Timeout"},
    }

    def fake_health_probe(section: str):
        return responses[section]

    monkeypatch.setattr(health, "health_probe", fake_health_probe)

    result = health.healthz()

    assert result["services"]["embeddings"].startswith("down (ValueError")
    assert result["services"]["llm_primary"] == "up"
    assert result["services"]["llm_fallback"].startswith("down (Timeout")
    assert result["ok"] is False
