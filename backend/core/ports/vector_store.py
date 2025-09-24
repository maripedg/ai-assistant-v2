from abc import ABC, abstractmethod
from typing import Any, List, Tuple

class VectorStorePort(ABC):
    @abstractmethod
    def similarity_search_with_score(self, query: str, k: int) -> List[Tuple[Any, float]]: ...
