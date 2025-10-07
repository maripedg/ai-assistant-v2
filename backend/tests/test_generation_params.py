import types

import pytest

from backend.app import deps as app_deps


class FakeChat:
    def __init__(self, *, endpoint, compartment_id, model_id, auth_file_location, auth_profile, **gen_kwargs):  # noqa: D401
        self.endpoint = endpoint
        self.compartment_id = compartment_id
        self.model_id = model_id
        self.auth_file_location = auth_file_location
        self.auth_profile = auth_profile
        # capture only supported keys
        self.gen = {k: v for k, v in gen_kwargs.items() if v is not None}

    def generate(self, prompt: str) -> str:  # pragma: no cover - trivial
        return "ok"


@pytest.fixture
def fake_settings(monkeypatch):
    class S:
        app = {}
        providers = {}

    s = S()
    monkeypatch.setattr(app_deps, "settings", s, raising=True)
    return s


def _base_oci():
    return {
        "endpoint": "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
        "compartment_id": "ocid1.compartment.oc1..example",
        "auth_file": "/tmp/oci",
        "auth_profile": "DEFAULT",
    }


def test_defaults_when_params_missing(monkeypatch, fake_settings):
    fake_settings.providers = {
        "oci": {
            "llm_primary": {**_base_oci(), "model_id": "ocid1.generativeaimodel.oc1..p1"},
            "llm_fallback": {**_base_oci(), "model_id": "ocid1.generativeaimodel.oc1..f1"},
        }
    }

    monkeypatch.setattr(
        "backend.providers.oci.chat_model_chat.OciChatModelChat",
        FakeChat,
        raising=True,
    )

    p = app_deps.make_chat_model_primary()
    f = app_deps.make_chat_model_fallback()

    assert isinstance(p, FakeChat) and isinstance(f, FakeChat)
    assert p.gen == {}
    assert f.gen == {}


def test_non_default_values_applied_and_clamped(monkeypatch, fake_settings):
    fake_settings.providers = {
        "oci": {
            "llm_primary": {
                **_base_oci(),
                "model_id": "ocid1.generativeaimodel.oc1..p1",
                "temperature": 0.2,
                "top_p": 0.9,
                "max_tokens": 256,
                "top_k": 1,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.0,
            },
            "llm_fallback": {
                **_base_oci(),
                "model_id": "ocid1.generativeaimodel.oc1..f1",
                "temperature": 0.7,
                "top_p": 1.5,  # will be clamped to 1.0
            },
        }
    }

    monkeypatch.setattr(
        "backend.providers.oci.chat_model_chat.OciChatModelChat",
        FakeChat,
        raising=True,
    )

    p = app_deps.make_chat_model_primary()
    f = app_deps.make_chat_model_fallback()

    assert p.gen.get("temperature") == 0.2
    assert p.gen.get("top_p") == 0.9
    assert p.gen.get("max_tokens") == 256
    assert p.gen.get("top_k") == 1
    assert p.gen.get("frequency_penalty") == 0.5
    assert p.gen.get("presence_penalty") == 0.0

    assert f.gen.get("temperature") == 0.7
    assert f.gen.get("top_p") == 1.0


def test_fallback_params_independent_from_primary(monkeypatch, fake_settings):
    fake_settings.providers = {
        "oci": {
            "llm_primary": {
                **_base_oci(),
                "model_id": "ocid1.generativeaimodel.oc1..p1",
                "temperature": 0.1,
            },
            "llm_fallback": {
                **_base_oci(),
                "model_id": "ocid1.generativeaimodel.oc1..f1",
                "temperature": 0.9,
            },
        }
    }

    monkeypatch.setattr(
        "backend.providers.oci.chat_model_chat.OciChatModelChat",
        FakeChat,
        raising=True,
    )

    p = app_deps.make_chat_model_primary()
    f = app_deps.make_chat_model_fallback()
    assert p.gen.get("temperature") == 0.1
    assert f.gen.get("temperature") == 0.9


def test_env_overrides_take_precedence(monkeypatch, fake_settings):
    fake_settings.providers = {
        "oci": {
            "llm_primary": {
                **_base_oci(),
                "model_id": "ocid1.generativeaimodel.oc1..p1",
                "temperature": 0.1,  # should be overridden
            },
            "llm_fallback": {
                **_base_oci(),
                "model_id": "ocid1.generativeaimodel.oc1..f1",
                "top_p": 0.2,  # should be overridden
            },
        }
    }

    monkeypatch.setenv("OCI_LLM_PRIMARY_TEMPERATURE", "0.6")
    monkeypatch.setenv("OCI_LLM_FALLBACK_TOP_P", "0.95")

    monkeypatch.setattr(
        "backend.providers.oci.chat_model_chat.OciChatModelChat",
        FakeChat,
        raising=True,
    )

    p = app_deps.make_chat_model_primary()
    f = app_deps.make_chat_model_fallback()
    assert p.gen.get("temperature") == 0.6
    assert f.gen.get("top_p") == 0.95
