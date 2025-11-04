import os
import oci
from langchain_community.embeddings import OCIGenAIEmbeddings

from backend.core.ports.embeddings import EmbeddingsPort

import logging
log = logging.getLogger(__name__)

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
        # LangChain embeddings do not require the low-level client; endpoint plus compartment is enough.
        self._emb = OCIGenAIEmbeddings(
            model_id=model_id,
            service_endpoint=endpoint,
            compartment_id=compartment_id,
        )
        # OCI GenAI differentiates document (search_document) vs query (search_query) vectors.
        # Keep the configured types explicit so document builds and query lookups stay aligned.
        self._doc_input_type = doc_input_type
        self._query_input_type = query_input_type

    def embed_documents(self, texts, input_type: str | None = None):
        """
        Embed document chunks. Default input_type is 'search_document'.
        """
        it = input_type or self._doc_input_type
        vecs = self._emb.embed_documents(texts, input_type=it)
        # Log audit (one-liner summary)
        try:
            log.info("EMBED_DOC count=%s input_type=%s", len(texts), it)
        except Exception:
            pass
        return vecs

    def embed_query(self, text, input_type: str | None = None):
        """
        Embed a user query. Default input_type is 'search_query'.
        """
        it = input_type or self._query_input_type
        vec = self._emb.embed_query(text, input_type=it)
        try:
            log.info("EMBED_QUERY len=%s input_type=%s", len(text or ""), it)
        except Exception:
            pass
        return vec
