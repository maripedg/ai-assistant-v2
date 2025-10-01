"""OCI GenAI embeddings client wrapper."""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from langchain_community.embeddings import OCIGenAIEmbeddings


class OciEmbeddingsClient:
    """Thin wrapper around `OCIGenAIEmbeddings` exposing explicit modes."""

    def __init__(
        self,
        *,
        model_id: str,
        endpoint: str,
        compartment_id: str,
        auth_file: Optional[str],
        auth_profile: Optional[str],
        doc_input_type: str = "search_document",
        query_input_type: str = "search_query",
    ) -> None:
        self._doc_input_type = doc_input_type
        self._query_input_type = query_input_type
        # `embed_documents` and `embed_query` select the proper internal API based
        # on `input_type`. We still provide the mode explicitly to avoid accidental
        # mismatches when document vectors are pre-computed with a specific profile.
        self._client = OCIGenAIEmbeddings(
            model_id=model_id,
            service_endpoint=endpoint,
            compartment_id=compartment_id,
            auth_file_location=auth_file,
            auth_profile=auth_profile,
        )

    def embed_docs(self, texts: Iterable[str]) -> List[List[float]]:
        texts = list(texts)
        if not texts:
            return []
        return self._client.embed_documents(texts, input_type=self._doc_input_type)

    def embed_queries(self, texts: Iterable[str]) -> List[List[float]]:
        results: List[List[float]] = []
        for text in texts:
            # `embed_query` returns a single vector, so we loop while still
            # flagging the query mode explicitly.
            vector = self._client.embed_query(text, input_type=self._query_input_type)
            results.append(vector)
        return results


def build_embeddings_client(settings: Dict[str, any]) -> OciEmbeddingsClient:
    providers = settings.get("providers", {}) or {}
    oci_cfg = providers.get("oci", {}) or {}
    emb_cfg = oci_cfg.get("embeddings", {})
    if not isinstance(emb_cfg, dict):
        raise ValueError("providers.oci.embeddings configuration is required")

    required = ["model_id", "endpoint", "compartment_id", "auth_file", "auth_profile"]
    missing = [key for key in required if not emb_cfg.get(key)]
    if missing:
        raise ValueError(f"providers.oci.embeddings missing required keys: {', '.join(missing)}")

    return OciEmbeddingsClient(
        model_id=emb_cfg["model_id"],
        endpoint=emb_cfg["endpoint"],
        compartment_id=emb_cfg["compartment_id"],
        auth_file=emb_cfg.get("auth_file"),
        auth_profile=emb_cfg.get("auth_profile"),
        doc_input_type=emb_cfg.get("documents_input_type", "search_document"),
        query_input_type=emb_cfg.get("queries_input_type", "search_query"),
    )
