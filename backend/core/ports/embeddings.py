from abc import ABC, abstractmethod
from typing import List

class EmbeddingsPort(ABC):
    @abstractmethod
    def embed_documents(self, texts: List[str]) -> List[List[float]]: ...
    @abstractmethod
    def embed_query(self, text: str) -> List[float]: ...
