import logging
from time import perf_counter
from typing import Any, List, Tuple

import oracledb
from langchain_community.vectorstores.oraclevs import OracleVS
from langchain_community.vectorstores.utils import DistanceStrategy

from core.ports.vector_store import VectorStorePort


logger = logging.getLogger(__name__)


class OracleVSStore(VectorStorePort):
    def __init__(
        self,
        dsn: str,
        user: str,
        password: str,
        table: str,
        embeddings,
        distance: str = "dot_product",
    ):
        self.conn = oracledb.connect(
            user=user,
            password=password,
            dsn=dsn,
            mode=oracledb.AUTH_MODE_SYSDBA,
        )
        strategy = (
            DistanceStrategy.DOT_PRODUCT
            if distance == "dot_product"
            else DistanceStrategy.COSINE
        )
        self._distance_label = distance
        self.vs = OracleVS(
            embedding_function=embeddings,
            client=self.conn,
            table_name=table,
            distance_strategy=strategy,
        )

    def similarity_search_with_score(self, query: str, k: int) -> List[Tuple[Any, float]]:
        start = perf_counter()
        raw_results = self.vs.similarity_search_with_score(query, k=k)

        order = "DESC" if self._distance_label == "dot_product" else "ASC"
        enriched: List[Tuple[Any, float]] = []
        for doc, score in raw_results:
            metadata = dict(getattr(doc, "metadata", {}) or {})
            raw_score = float(score)
            metadata["raw_score"] = raw_score
            metadata.setdefault("source", metadata.get("source") or "")
            metadata.setdefault("chunk_id", metadata.get("chunk_id") or "")
            preview_text = (getattr(doc, "page_content", "") or "")[:400]
            metadata["text_preview"] = preview_text.replace("\n", " ").strip()
            doc.metadata = metadata
            enriched.append((doc, raw_score))

        if order == "DESC":
            enriched.sort(key=lambda item: item[1], reverse=True)
        else:
            enriched.sort(key=lambda item: item[1])

        elapsed_ms = (perf_counter() - start) * 1000.0
        preview = query if len(query) <= 120 else f"{query[:117]}..."
        top_meta = [
            (
                (doc.metadata or {}).get("source", ""),
                (doc.metadata or {}).get("chunk_id", ""),
                float(score),
            )
            for doc, score in enriched[:3]
        ]
        logger.debug(
            "OCI vector search | metric=%s | order=%s | top_k=%d | top3=%s | elapsed_ms=%.2f | query=%r",
            self._distance_label,
            order,
            k,
            top_meta,
            elapsed_ms,
            preview,
        )
        return enriched
