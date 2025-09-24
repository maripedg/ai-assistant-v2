import logging
import oracledb
from time import perf_counter
from typing import List, Tuple, Any
from langchain_community.vectorstores.oraclevs import OracleVS
from langchain_community.vectorstores.utils import DistanceStrategy
from core.ports.vector_store import VectorStorePort

logger = logging.getLogger(__name__)


class OracleVSStore(VectorStorePort):
    def __init__(self, dsn: str, user: str, password: str, table: str,
                 embeddings, distance: str = "dot_product"):
        self.conn = oracledb.connect(user=user, password=password, dsn=dsn, mode=oracledb.AUTH_MODE_SYSDBA)
        strategy = DistanceStrategy.DOT_PRODUCT if distance == "dot_product" else DistanceStrategy.COSINE
        self._distance_label = distance
        self.vs = OracleVS(embedding_function=embeddings, client=self.conn,
                           table_name=table, distance_strategy=strategy)

    def similarity_search_with_score(self, query: str, k: int) -> List[Tuple[Any, float]]:
        start = perf_counter()
        results = self.vs.similarity_search_with_score(query, k=k)
        elapsed_ms = (perf_counter() - start) * 1000.0
        scores = [float(score) for _, score in results]
        preview = query if len(query) <= 120 else f"{query[:117]}..."
        logger.debug(
            "OCI vector search | query=%r | top_k=%d | strategy=%s | elapsed_ms=%.2f | scores=%s",
            preview,
            k,
            self._distance_label,
            elapsed_ms,
            scores,
        )
        return results
