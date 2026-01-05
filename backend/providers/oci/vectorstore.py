import json
import logging
import os
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

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
DEBUG_RETRIEVAL_METADATA = (os.getenv("DEBUG_RETRIEVAL_METADATA") or "false").lower() in {"1", "true", "yes", "on"}


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

    def similarity_search_with_score(self, query: str, k: int, target_view: Optional[str] = None) -> List[Tuple[Any, float]]:
        start = perf_counter()
        original_table = getattr(self.vs, "table_name", None)
        if target_view:
            try:
                setattr(self.vs, "table_name", target_view)
            except Exception:
                pass
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
        finally:
            if target_view and original_table:
                try:
                    setattr(self.vs, "table_name", original_table)
                except Exception:
                    pass

        # Oracle VECTOR_DISTANCE returns a distance for both DOT and COSINE
        # (smaller = more similar). Keep ascending order for all metrics.
        order = "ASC"
        enriched: List[Tuple[Any, float]] = []
        if DEBUG_RETRIEVAL_METADATA:
            logger.debug("DEBUG_METADATA raw_results_count=%d", len(raw_results or []))
        for idx, (doc, score) in enumerate(raw_results):
            if DEBUG_RETRIEVAL_METADATA and idx < 2:
                raw_meta = getattr(doc, "metadata", None)
                meta_kind = type(raw_meta).__name__
                preview = ""
                meta_keys = []
                if isinstance(raw_meta, str):
                    preview = raw_meta[:200]
                elif isinstance(raw_meta, dict):
                    meta_keys = list(raw_meta.keys())
                logger.debug(
                    "DEBUG_METADATA raw_result idx=%d type=%s meta_type=%s meta_keys=%s meta_preview=%s",
                    idx,
                    type(doc).__name__,
                    meta_kind,
                    meta_keys,
                    preview,
                )
            # existing enrichment below
        for doc, score in raw_results:
            raw_meta = getattr(doc, "metadata", None)
            metadata: Dict[str, Any] = {}
            if isinstance(raw_meta, dict):
                metadata.update(raw_meta)
            elif isinstance(raw_meta, str):
                try:
                    parsed = json.loads(raw_meta)
                    if isinstance(parsed, dict):
                        metadata.update(parsed)
                except Exception:  # noqa: BLE001
                    pass
            extra_meta = getattr(doc, "METADATA", None) or getattr(doc, "metadata_json", None)
            if isinstance(extra_meta, str):
                try:
                    parsed_extra = json.loads(extra_meta)
                    if isinstance(parsed_extra, dict):
                        metadata.update(parsed_extra)
                except Exception:  # noqa: BLE001
                    pass
            elif isinstance(extra_meta, dict):
                metadata.update(extra_meta)
            raw_score = float(score)
            metadata["raw_score"] = raw_score
            metadata.setdefault("source", metadata.get("source") or "")
            metadata.setdefault("chunk_id", metadata.get("chunk_id") or "")
            metadata.setdefault("doc_id", metadata.get("doc_id") or "")
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
