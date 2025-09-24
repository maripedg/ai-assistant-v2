import os
from types import SimpleNamespace

import pytest

from backend.providers.oci.chat_model_chat import OciChatModelChat
from backend.providers.oci import chat_model as chat_model_module


@pytest.mark.skipif(os.environ.get("OCI_TESTS_DISABLED") == "1", reason="OCI tests disabled by env")
def test_chat_adapter_requires_ocid():
    with pytest.raises(ValueError):
        OciChatModelChat(
            endpoint="https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
            compartment_id="ocid1.compartment.oc1..example",
            model_id="cohere.command-english-v3.0",
            auth_file_location="/tmp/oci.conf",
            auth_profile="DEFAULT",
        )


def test_primary_alias_uses_langchain(monkeypatch):
    captured = {}

    class FakeOCIGenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def invoke(self, prompt: str) -> str:
            return "alias-response"

    monkeypatch.setattr(chat_model_module, "OCIGenAI", FakeOCIGenAI)

    model = chat_model_module.OciChatModel(
        model_id="cohere.command-english-v3.0",
        endpoint="https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
        compartment_id="ocid1.compartment.oc1..example",
    )

    assert model.generate("hello") == "alias-response"
    assert captured["model_id"] == "cohere.command-english-v3.0"


def test_primary_ocid_raises(monkeypatch):
    with pytest.raises(ValueError):
        chat_model_module.OciChatModel(
            model_id="ocid1.generativeaimodel.oc1..exampleunique",
            endpoint="https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
            compartment_id="ocid1.compartment.oc1..example",
            auth_file_location="/fake/path",
            auth_profile="DEFAULT",
        )
