"""Embedding strategy interfaces and factory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol

from backend.core.services.retrieval_service import RetrievalService


@dataclass
class Chunk:
    text: str
    metadata: Dict[str, Any]


@dataclass
class Vector:
    embedding: List[float]
    metadata: Dict[str, Any]


class EmbeddingStrategy(Protocol):
    """Defines how to chunk, embed, and post-process document metadata."""

    def chunk(self, text: str, metadata: Dict[str, Any]) -> List[Chunk]:
        """Split text into chunks according to the profile's chunker settings."""

    def embed_documents(self, chunks: Iterable[Chunk]) -> List[Vector]:
        """Embed each chunk in document mode (search_document)."""

    def build_metadata(self, chunk: Chunk) -> Dict[str, Any]:
        """Filter/enrich metadata for persistence in the vector index."""


# ... rest of file omitted for brevity
