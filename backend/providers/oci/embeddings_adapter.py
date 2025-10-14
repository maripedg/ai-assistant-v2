# backend/providers/oci/embeddings_adapter.py
from __future__ import annotations

import inspect
import os
from typing import List
from langchain_core.embeddings import Embeddings
import oci

try:
    # Cliente real de LangChain para OCI GenAI
    from langchain_community.embeddings import OCIGenAIEmbeddings
except Exception as exc:  # pragma: no cover
    raise

class OCIEmbeddingsAdapter(Embeddings):
    """
    Adapter que implementa la interfaz de LangChain (Embeddings).
    Internamente delega en OCIGenAIEmbeddings y, si la versión lo permite,
    pasa input_type para mantener la asimetría query/document.
    """

    def __init__(
        self,
        model_id: str,
        service_endpoint: str,
        compartment_id: str,
        auth_file_location: str,
        auth_profile: str,
        doc_input_type: str = "search_document",
        query_input_type: str = "search_query",
    ) -> None:
        # Store config
        self._model_id = model_id
        self._endpoint = service_endpoint
        self._compartment_id = compartment_id
        self._doc_input_type = doc_input_type or "search_document"
        self._query_input_type = query_input_type or "search_query"
        # Ensure OCI SDK picks up the desired config file/profile as a baseline
        if auth_file_location:
            os.environ["OCI_CONFIG_FILE"] = auth_file_location
        if auth_profile:
            os.environ["OCI_CONFIG_PROFILE"] = auth_profile
        # Build OCI Generative AI client using explicit file+profile
        cfg = oci.config.from_file(
            file_location=os.environ.get("OCI_CONFIG_FILE"),
            profile_name=os.environ.get("OCI_CONFIG_PROFILE", "DEFAULT"),
        )
        self._client = oci.generative_ai_inference.GenerativeAiInferenceClient(
            config=cfg,
            retry_strategy=oci.retry.DEFAULT_RETRY_STRATEGY,
            timeout=(10, 240),
            service_endpoint=self._endpoint,
        )
        # SDK models import and signature detection
        from oci.generative_ai_inference import models as _models
        self._models = _models
        self._details_init_params = set(
            inspect.signature(_models.EmbedTextDetails.__init__).parameters.keys()
        )

    # Métodos esperados por LangChain:

    def embed_documents(self, texts: List[str], input_type: str | None = None) -> List[List[float]]:
        # Determine serving mode
        if self._model_id.startswith("ocid1.generativeaiendpoint"):
            serving_mode = self._models.DedicatedServingMode(endpoint_id=self._model_id)
        else:
            serving_mode = self._models.OnDemandServingMode(model_id=self._model_id)

        embeddings: List[List[float]] = []
        batch_size = 96
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            kwargs = {
                "serving_mode": serving_mode,
                "compartment_id": self._compartment_id,
                "inputs": chunk,
            }
            if "truncate" in self._details_init_params:
                kwargs["truncate"] = "END"
            # Allow explicit input_type override for compatibility with callers
            effective_it = input_type or self._doc_input_type
            if "input_type" in self._details_init_params and effective_it:
                kwargs["input_type"] = effective_it
            details = self._models.EmbedTextDetails(**kwargs)
            resp = self._client.embed_text(details)
            embeddings.extend(resp.data.embeddings)
        return embeddings

    def embed_query(self, text: str, input_type: str | None = None) -> List[float]:
        # Reuse documents path for a single query with query input_type
        if self._model_id.startswith("ocid1.generativeaiendpoint"):
            serving_mode = self._models.DedicatedServingMode(endpoint_id=self._model_id)
        else:
            serving_mode = self._models.OnDemandServingMode(model_id=self._model_id)
        kwargs = {
            "serving_mode": serving_mode,
            "compartment_id": self._compartment_id,
            "inputs": [text],
        }
        if "truncate" in self._details_init_params:
            kwargs["truncate"] = "END"
        effective_it = input_type or self._query_input_type
        if "input_type" in self._details_init_params and effective_it:
            kwargs["input_type"] = effective_it
        details = self._models.EmbedTextDetails(**kwargs)
        resp = self._client.embed_text(details)
        return resp.data.embeddings[0]
