import logging
import os
from time import perf_counter
from typing import Any, List, Tuple

from langchain_community.vectorstores.oraclevs import OracleVS
from langchain_community.vectorstores.utils import DistanceStrategy

from backend.core.ports.vector_store import VectorStorePort
from backend.providers.oci.embeddings_adapter import EmbeddingError


def _lazy_import_oracledb():
    try:
        import oracledb  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive
        raise ModuleNotFoundError(
            "The 'oracledb' package is required for Oracle vector retrieval. "
            "Install it via `pip install oracledb`."
        ) from exc
    return oracledb


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
        oracledb = _lazy_import_oracledb()
        params = {
            "user": user,
            "password": password,
            "dsn": dsn,
        }
        # Only connect AS SYSDBA/SYSOPER when explicitly requested or when using SYS.
        auth_mode_env = (os.getenv("ORACLE_AUTH_MODE") or "").strip().upper()
        if user and user.strip().upper() == "SYS":
            params["mode"] = getattr(oracledb, "AUTH_MODE_SYSDBA")
        elif auth_mode_env in {"SYSDBA", "SYSOPER"}:
            params["mode"] = getattr(oracledb, f"AUTH_MODE_{auth_mode_env}")

        self.conn = oracledb.connect(**params)
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
        try:
            raw_results = self.vs.similarity_search_with_score(query, k=k)
        except EmbeddingError as exc:
            logger.warning("Vector search skipped because embeddings are unavailable: %s", exc)
            return []
        except RuntimeError as exc:
            msg = str(exc)
            if "DPY-4031: vector cannot contain zero dimensions" in msg:
                logger.error(
                    "Vector search failed due to zero-dimension embedding (DPY-4031). Treating as no results. error=%s",
                    msg,
                )
                return []
            raise

        # Oracle VECTOR_DISTANCE returns a distance for both DOT and COSINE
        # (smaller = more similar). Keep ascending order for all metrics.
        order = "ASC"
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

        # Always sort by ascending distance (lower is better)
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
