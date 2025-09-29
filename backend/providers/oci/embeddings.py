import os
import oci
from langchain_community.embeddings import OCIGenAIEmbeddings

from core.ports.embeddings import EmbeddingsPort


class OciEmbeddings(EmbeddingsPort):
    def __init__(
        self,
        model_id: str,
        endpoint: str,
        compartment_id: str,
        auth_mode: str = "config_file",
        config_path: str = "~/.oci/config",
        profile: str = "DEFAULT",
        doc_input_type: str = "search_document",
        query_input_type: str = "search_query",
    ) -> None:
        # Para embeddings LangChain no hace falta el cliente low-level; basta con endpoint + compartment.
        self._emb = OCIGenAIEmbeddings(
            model_id=model_id,
            service_endpoint=endpoint,
            compartment_id=compartment_id,
        )
        # OCI GenAI differentiates document (search_document) vs query (search_query) vectors.
        # Keep the configured types explicit so document builds and query lookups stay aligned.
        self._doc_input_type = doc_input_type
        self._query_input_type = query_input_type

    def embed_documents(self, texts):
        # Explicitly tag document embeddings so they are generated with the search_document profile.
        return self._emb.embed_documents(texts, input_type=self._doc_input_type)

    def embed_query(self, text):
        # Queries must use search_query to stay aligned with document vectors created as search_document.
        return self._emb.embed_query(text, input_type=self._query_input_type)
