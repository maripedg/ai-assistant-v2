import os
import oci
from langchain_community.embeddings import OCIGenAIEmbeddings

from core.ports.embeddings import EmbeddingsPort

class OciEmbeddings(EmbeddingsPort):
    def __init__(self, model_id: str, endpoint: str, compartment_id: str,
                 auth_mode: str = "config_file", config_path: str = "~/.oci/config", profile: str = "DEFAULT"):
        # Para embeddings LangChain no hace falta el cliente low-level; basta con endpoint + compartment.
        self._emb = OCIGenAIEmbeddings(
            model_id=model_id,
            service_endpoint=endpoint,
            compartment_id=compartment_id
        )

    def embed_documents(self, texts):
        return self._emb.embed_documents(texts)

    def embed_query(self, text):
        return self._emb.embed_query(text)
