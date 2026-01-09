
"""Embedding batch job entry point."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.app import config as app_config
from backend.app.deps import make_embeddings, settings as deps_settings
from backend.ingest.manifests.spec import validate_and_expand_manifest
from backend.ingest.router import route_and_load
from backend.ingest.normalizer import normalize_metadata
from backend.ingest.chunking.char_chunker import chunk_text
from backend.ingest.chunking.token_chunker import chunk_text_by_tokens
from backend.ingest.chunking.structured_docx_chunker import chunk_structured_docx_items
from backend.ingest.chunking.structured_pdf_chunker import chunk_structured_pdf_items
from backend.ingest.chunking.toc_section_docx_chunker import chunk_docx_toc_sections

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)
PIPELINE_CHUNK_DEBUG = (os.getenv("PIPELINE_CHUNK_DEBUG") or "").lower() in {"1", "true", "on", "yes"}
USE_TOC_SECTION_DOCX_CHUNKER = (os.getenv("USE_TOC_SECTION_DOCX_CHUNKER") or "").lower() in {"1", "true", "on", "yes"}
CHUNKING_DIAGNOSTIC = (os.getenv("CHUNKING_DIAGNOSTIC") or "").lower() in {"1", "true", "on", "yes"}


# --- Sanitizer (optional import)
try:
    from backend.common.sanitizer import sanitize_if_enabled  # (text, doc_id) -> (text, counters)
except Exception:  # pragma: no cover
    sanitize_if_enabled = None


@dataclass(frozen=True)
class EmbeddingJobSummary:
    """Lightweight container for embed job results."""

    docs: int
    chunks: int
    inserted: int
    skipped: int
    errors: int
    dry_run: bool
    evaluation: Optional[Dict[str, Any]] = None
    # Token-limit handling counters
    errors_token_limit: int = 0
    token_limit_splits: int = 0
    token_limit_truncations: int = 0
    skipped_token_limit: int = 0
    embedding_summary: Optional[Dict[str, Any]] = None


def format_summary(summary: EmbeddingJobSummary) -> str:
    """Return a printable one-line summary of job statistics."""

    base = (
        f"docs={summary.docs} chunks={summary.chunks} inserted={summary.inserted} "
        f"skipped={summary.skipped} errors={summary.errors} dry_run={summary.dry_run}"
    )
    if summary.evaluation and isinstance(summary.evaluation, dict):
        hit_rate = summary.evaluation.get("hit_rate")
        mrr = summary.evaluation.get("mrr")
        if hit_rate is not None and mrr is not None:
            base += f" eval_hit_rate={hit_rate:.3f} eval_mrr={mrr:.3f}"
        elif "error" in summary.evaluation:
            base += f" eval_error={summary.evaluation['error']}"
    base += (
        f" Token-limit: splits={summary.token_limit_splits} truncations={summary.token_limit_truncations}"
        f" skipped={summary.skipped_token_limit} provider_errors={summary.errors_token_limit}"
    )
    if summary.embedding_summary:
        emb = summary.embedding_summary
        base += (
            " EmbeddingStats"
            f" prepared={emb.get('prepared')}"
            f" embedded={emb.get('embedded')}"
            f" saved={emb.get('saved')}"
            f" skipped={emb.get('skipped')}"
            f" failed_batches={emb.get('failed_batches')}"
        )
    return base

PDF_EXTENSIONS = {".pdf"}


@dataclass
class SimpleChunk:
    text: str
    metadata: Dict[str, Any]


def _effective_max_tokens(chunker_cfg: Dict[str, Any], profile_cfg: Dict[str, Any]) -> int:
    profile_limit = int(profile_cfg.get("max_input_tokens", 512) or 512)
    safety = int(chunker_cfg.get("token_safety_margin", 64) or 64)
    base_limit = max(1, profile_limit - safety)
    raw_max = chunker_cfg.get("max_tokens")
    if raw_max is not None:
        try:
            raw_int = int(raw_max)
            if raw_int > 0:
                return max(1, min(raw_int, base_limit))
        except Exception:
            pass
    return base_limit


class _DummyEmbedder:
    def embed_documents(self, texts: Iterable[str]) -> List[List[float]]:
        return [[0.0] for _ in texts]

class ManifestEntry:
    """Represents a single manifest line."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self.path: str = data.get("path")
        if not self.path:
            raise ValueError("Manifest entry missing 'path'")
        self.doc_id: Optional[str] = data.get("doc_id")
        self.profile: Optional[str] = data.get("profile")
        self.tags: List[str] = data.get("tags") or []
        self.lang: Optional[str] = data.get("lang")
        self.priority: Optional[int] = data.get("priority")


def _lazy_import_oracledb():
    try:
        import oracledb  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive
        raise ModuleNotFoundError(
            "The 'oracledb' package is required for Oracle vector operations. "
            "Install it via `pip install oracledb`."
        ) from exc
    return oracledb


class OracleVSUpserter:
    """Light wrapper for inserting vectors into Oracle."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self._table = config["table"]
        self._config = {
            "dsn": config["dsn"],
            "user": config["user"],
            "password": config["password"],
        }
        self._conn = None

    def _get_connection(self):
        if self._conn is not None:
            return self._conn
        if not self._config:
            raise RuntimeError("Oracle connection is not configured")
        oracledb = _lazy_import_oracledb()
        self._conn = oracledb.connect(
            user=self._config["user"],
            password=self._config["password"],
            dsn=self._config["dsn"],
        )
        return self._conn

    @property
    def connection(self):
        return self._get_connection()

    def attach_connection(self, conn) -> None:
        self._conn = conn
        self._config = None

    def set_target_table(self, table_name: str) -> None:
        """Force writes to the given physical table (e.g., MY_DEMO_V1)."""
        self._table = table_name

    def upsert_vectors(
        self,
        vectors: Iterable[Dict[str, Any]],
        dedupe: bool,
        dry_run: bool,
    ) -> Tuple[int, int]:
        """Insert vectors into Oracle, skipping duplicates when requested."""

        inserted = 0
        skipped = 0

        if dry_run:
            for vector in vectors:
                hash_norm = vector["metadata"].get("hash_norm")
                skipped += 1 if dedupe and hash_norm else 0
            return inserted, skipped

        conn = self._get_connection()
        with conn.cursor() as cur:
            oracledb = _lazy_import_oracledb()
            clob_type = getattr(oracledb, 'DB_TYPE_CLOB', None)
            for vector in vectors:
                meta = dict(vector["metadata"])
                hash_norm = meta.get("hash_norm")
                metric = (meta.get("distance_metric") or "dot_product").lower()
                if dedupe and hash_norm:
                    cur.execute(
                        f"SELECT 1 FROM {self._table} WHERE HASH_NORM = :1 FETCH FIRST 1 ROWS ONLY",
                        (hash_norm,),
                    )
                    if cur.fetchone():
                        skipped += 1
                        continue

                metadata_json = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
                embedding_json = json.dumps(vector.get("embedding", []), separators=(",", ":"))

                binds = {
                    "hash_norm": hash_norm,
                    "metric": metric,
                }

                if clob_type is not None:
                    text_clob = cur.var(clob_type)
                    text_clob.setvalue(0, vector["text"])
                    meta_clob = cur.var(clob_type)
                    meta_clob.setvalue(0, metadata_json)
                    emb_clob = cur.var(clob_type)
                    emb_clob.setvalue(0, embedding_json)
                    binds.update({
                        "text": text_clob,
                        "metadata_json": meta_clob,
                        "embedding_json": emb_clob,
                    })
                else:
                    binds.update({
                        "text": vector["text"],
                        "metadata_json": metadata_json,
                        "embedding_json": embedding_json,
                    })

                cur.execute(
                    f"INSERT INTO {self._table} (ID, TEXT, METADATA, EMBEDDING, HASH_NORM, DISTANCE_METRIC) "
                    f"VALUES (SYS_GUID(), :text, :metadata_json, TO_VECTOR(:embedding_json), :hash_norm, :metric)",
                    binds,
                )
                inserted += 1
            self._conn.commit()
        return inserted, skipped


def _hash_normalize(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def _build_strategy(profile_name: str, app_settings: Dict[str, Any]):
    try:
        from backend.core.embeddings import embedding_strategy as strategy_module

        builder = getattr(strategy_module, "build_strategy", None)
        if builder is not None:
            return builder(profile_name, app_settings)
    except ModuleNotFoundError:
        strategy_module = None

    profile_cfg = app_settings.get("embeddings", {}).get("profiles", {}).get(profile_name, {}) or {}
    chunk_cfg = profile_cfg.get("chunker", {}) or {}

    chunk_size = int(chunk_cfg.get("size", 2000) or 2000)
    overlap = chunk_cfg.get("overlap", 0)
    if isinstance(overlap, float):
        overlap = int(chunk_size * overlap)
    overlap = int(overlap or 0)
    chunker_type = (chunk_cfg.get("type") or "char").lower()

    separator = chunk_cfg.get("separator")
    if not separator and chunker_type == "char":
        separator = None

    class _SimpleEmbeddingStrategy:
        def chunk(self, text: str, metadata: Dict[str, Any]) -> List[SimpleChunk]:
            if chunker_type == "char":
                return self._chunk_chars(text)
            return self._chunk_chars(text)

        def _chunk_chars(self, text: str) -> List[SimpleChunk]:
            result: List[SimpleChunk] = []
            if separator:
                segments = text.split(separator)
                buffer = []
                current_length = 0
                for seg in segments:
                    seg_with_sep = seg if not buffer else separator + seg
                    if current_length + len(seg_with_sep) > chunk_size and buffer:
                        chunk_text = "".join(buffer)
                        result.append(SimpleChunk(chunk_text, {}))
                        buffer = [seg]
                        current_length = len(seg)
                    else:
                        buffer.append(seg_with_sep if buffer else seg)
                        current_length += len(seg_with_sep)
                if buffer:
                    result.append(SimpleChunk("".join(buffer), {}))
            else:
                start = 0
                text_length = len(text)
                while start < text_length:
                    end = min(start + chunk_size, text_length)
                    chunk_text = text[start:end]
                    result.append(SimpleChunk(chunk_text, {}))
                    if overlap > 0:
                        start = max(end - overlap, start + 1)
                    else:
                        start = end

            filtered = [chunk for chunk in result if chunk.text.strip()]
            return filtered or [SimpleChunk(text, {})]

    return _SimpleEmbeddingStrategy()


def _load_pdf(path: Path) -> str:
    try:
        from PyPDF2 import PdfReader  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive
        raise ModuleNotFoundError(
            "PyPDF2 is required to read PDF documents. Install it via `pip install PyPDF2`."
        ) from exc

    reader = PdfReader(str(path))
    content = []
    for page in reader.pages:
        text = page.extract_text() or ""
        content.append(text)
    return "\n".join(content)


def _load_document(path: Path) -> str:
    if path.suffix.lower() in PDF_EXTENSIONS:
        return _load_pdf(path)
    return path.read_text(encoding="utf-8")


def _iter_manifest(manifest_path: Path) -> Iterable[ManifestEntry]:
    with manifest_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield ManifestEntry(json.loads(line))


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in ("*", "?", "["))


def _expand_entry_paths(entry_path: str, manifest_root: Path) -> List[Path]:
    raw_path = Path(entry_path).expanduser()
    if not raw_path.is_absolute():
        raw_path = manifest_root / raw_path
    raw_path = raw_path.resolve()
    pattern = str(raw_path)

    if _has_glob(pattern):
        matches = {Path(p).resolve() for p in glob.glob(pattern, recursive=True)}
        return [p for p in sorted(matches) if p.exists()]

    if not raw_path.exists():
        raise FileNotFoundError(f"Manifest path not found: {raw_path}")
    return [raw_path]


def _ensure_chunk_metadata(chunk: Any, base: Dict[str, Any], chunk_id: str) -> Dict[str, Any]:
    meta = dict(chunk.metadata)
    meta.update({
        "source": base["source"],
        "doc_id": base["doc_id"],
        "chunk_id": chunk_id,
        "lang": base.get("lang"),
        "tags": base.get("tags"),
        "priority": base.get("priority"),
        "profile": base.get("profile"),
    })
    if "page" not in meta:
        meta["page"] = base.get("page")
    return meta


def _load_yaml(path: Path) -> Any:
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive
        raise ModuleNotFoundError(
            "PyYAML is required to read golden query files. Install it via `pip install pyyaml`."
        ) from exc

    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _load_golden_queries(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Golden queries file not found: {path}")

    data = _load_yaml(path) or {}

    if isinstance(data, dict):
        raw_entries = data.get("queries") or []
    elif isinstance(data, list):
        raw_entries = data
    else:
        raise ValueError("Golden queries YAML must be a list or contain a 'queries' list")

    queries: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw_entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Golden query entry #{idx} must be a mapping")

        query_text = entry.get("query")
        if not query_text:
            raise ValueError(f"Golden query entry #{idx} missing 'query'")

        expect_ids = entry.get("expect_doc_ids") or entry.get("doc_ids") or []
        if isinstance(expect_ids, str):
            expect_ids = [expect_ids]
        expect_phrases = entry.get("expect_phrases") or entry.get("phrases") or []
        if isinstance(expect_phrases, str):
            expect_phrases = [expect_phrases]

        entry_top_k = entry.get("top_k")
        if entry_top_k is not None:
            try:
                entry_top_k = int(entry_top_k)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Golden query entry #{idx} has invalid top_k: {entry_top_k}") from exc
            if entry_top_k <= 0:
                raise ValueError(f"Golden query entry #{idx} top_k must be positive")

        queries.append(
            {
                "query": str(query_text),
                "expect_doc_ids": [str(doc_id) for doc_id in expect_ids if doc_id],
                "expect_phrases": [str(phrase) for phrase in expect_phrases if phrase],
                "top_k": entry_top_k,
            }
        )

    if not queries:
        raise ValueError("Golden queries file is empty")

    return queries


def _evaluate_golden_queries(golden_path: Path, alias_name: str) -> Dict[str, Any]:
    queries = _load_golden_queries(golden_path)

    retrieval_cfg = deps_settings.app.get("retrieval", {}) or {}
    default_top_k = retrieval_cfg.get("top_k")
    try:
        default_top_k_int = int(default_top_k)
    except (TypeError, ValueError):
        default_top_k_int = 10
    if default_top_k_int <= 0:
        default_top_k_int = 10

    embeddings = make_embeddings()

    try:
        from backend.app.deps import make_vector_store
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive
        raise ModuleNotFoundError(
            "The 'oracledb' package is required to evaluate golden queries. Install it with `pip install oracledb`."
        ) from exc

    try:
        vector_store = make_vector_store(embeddings)
    except ModuleNotFoundError as exc:  # pragma: no cover - defensive
        raise ModuleNotFoundError(
            "The 'oracledb' package is required to evaluate golden queries. Install it with `pip install oracledb`."
        ) from exc

    eligible = 0
    hits = 0
    mrr_sum = 0.0
    details: List[Dict[str, Any]] = []

    for item in queries:
        top_k = item.get("top_k") or default_top_k_int
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = default_top_k_int
        if top_k <= 0:
            top_k = default_top_k_int

        results = vector_store.similarity_search_with_score(item["query"], k=top_k)
        expected_ids = item["expect_doc_ids"]

        matched_rank: Optional[int] = None
        matched_doc_id: Optional[str] = None

        if expected_ids:
            eligible += 1
            for rank, (doc, _score) in enumerate(results, start=1):
                metadata = dict(getattr(doc, "metadata", {}) or {})
                doc_id = str(metadata.get("doc_id") or metadata.get("chunk_id") or "")
                if doc_id and doc_id in expected_ids:
                    matched_rank = rank
                    matched_doc_id = doc_id
                    break
            if matched_rank is not None:
                hits += 1
                mrr_sum += 1.0 / matched_rank

        phrase_hit = False
        phrases = item["expect_phrases"]
        if phrases:
            for doc, _score in results:
                text = (getattr(doc, "page_content", "") or "").lower()
                if not text:
                    continue
                if any(phrase.lower() in text for phrase in phrases):
                    phrase_hit = True
                    break

        details.append(
            {
                "query": item["query"],
                "top_k": top_k,
                "expected_ids": expected_ids,
                "matched_rank": matched_rank,
                "matched_doc_id": matched_doc_id,
                "phrase_hit": phrase_hit,
            }
        )

    hit_rate = (hits / eligible) if eligible else 0.0
    mrr = (mrr_sum / eligible) if eligible else 0.0

    hit_pct = f"{hit_rate:.1%}" if eligible else "n/a"
    mrr_fmt = f"{mrr:.3f}" if eligible else "n/a"

    print(
        "[eval] alias={alias} queries={total} doc_hit={hits}/{eligible} ({hit_pct}) mrr={mrr_fmt}".format(
            alias=alias_name,
            total=len(queries),
            hits=hits,
            eligible=eligible,
            hit_pct=hit_pct,
            mrr_fmt=mrr_fmt,
        )
    )

    for detail in details:
        rank_display = detail["matched_rank"] if detail["matched_rank"] is not None else "miss"
        print(
            "[eval] query={query!r} matched_doc={doc} rank={rank} phrase_hit={phrase}".format(
                query=detail["query"],
                doc=detail["matched_doc_id"] or "-",
                rank=rank_display,
                phrase="Y" if detail["phrase_hit"] else "N",
            )
        )

    return {
        "alias": alias_name,
        "queries_total": len(queries),
        "eligible_for_doc_metrics": eligible,
        "doc_hits": hits,
        "hit_rate": hit_rate,
        "mrr": mrr,
        "details": details,
    }


def run_embed_job(
    manifest_path: str,
    profile_name: Optional[str],
    domain_key: Optional[str] = None,
    dry_run: bool = False,
    update_alias: bool = False,
    batch_size_override: Optional[int] = None,
    max_workers: Optional[int] = None,
    evaluate_path: Optional[str] = None,
) -> EmbeddingJobSummary:
    app_settings = deps_settings.app
    embeddings_cfg = app_settings.get("embeddings", {}) or {}
    profile_name = profile_name or embeddings_cfg.get("active_profile")
    if not profile_name:
        raise ValueError("No embedding profile specified")

    profiles = embeddings_cfg.get("profiles", {}) or {}
    profile_cfg = profiles.get(profile_name)
    if not isinstance(profile_cfg, dict):
        raise ValueError(f"Embedding profile '{profile_name}' not defined")

    alias_cfg = embeddings_cfg.get("alias", {}) or {}
    domains_cfg = embeddings_cfg.get("domains") or {}

    if domain_key:
        domain_cfg = domains_cfg.get(domain_key)
        if not isinstance(domain_cfg, dict):
            raise ValueError(f"embeddings.domains.{domain_key} not found or invalid")
        index_name = domain_cfg.get("index_name")
        alias_name = domain_cfg.get("alias_name")
        missing: List[str] = []
        if not index_name:
            missing.append("index_name")
        if not alias_name:
            missing.append("alias_name")
        if missing:
            raise ValueError(
                f"embeddings.domains.{domain_key} missing required key(s): {', '.join(missing)}"
            )
        logger.info("Using domain override: domain_key=%s index_name=%s alias_name=%s", domain_key, index_name, alias_name)
    else:
        index_name = profile_cfg.get("index_name")
        if not index_name:
            raise ValueError(f"Embedding profile '{profile_name}' missing index_name")
        alias_name = alias_cfg.get("name")
    raw_batch = getattr(app_config, "EMBED_BATCH_SIZE", 32) or 32
    effective_batch = max(1, int(raw_batch))
    raw_workers = getattr(app_config, "EMBED_WORKERS", 1) or 1
    effective_workers = max(1, int(raw_workers))
    raw_rate = getattr(app_config, "EMBED_RATE_LIMIT_PER_MIN", None)
    effective_rate_limit = int(raw_rate) if raw_rate and int(raw_rate) > 0 else None
    if batch_size_override is not None and batch_size_override != effective_batch:
        logger.info(
            "Ignoring CLI batch_size_override=%s; using EMBED_BATCH_SIZE=%s from env",
            batch_size_override,
            effective_batch,
        )
    if max_workers is not None and max_workers != effective_workers:
        logger.info(
            "Ignoring CLI workers override=%s; using EMBED_WORKERS=%s from env",
            max_workers,
            effective_workers,
        )
    batch_size = effective_batch
    dedupe_cfg = embeddings_cfg.get("dedupe", {}) or {}
    dedupe_enabled = bool(dedupe_cfg.get("by_hash", False))
    logger.info(
        "Embedding config: batch_size=%s | workers_hint=%s | rate_limit_per_min=%s",
        batch_size,
        effective_workers,
        effective_rate_limit or "disabled",
    )
    logger.info(
        "Worker pools not implemented; running single-threaded despite EMBED_WORKERS hint=%s",
        effective_workers,
    )

    strategy = _build_strategy(profile_name, app_settings)
    try:
        embedder = make_embeddings()
    except Exception as exc:  # noqa: BLE001
        if not dry_run:
            raise
        logger.warning("Embeddings unavailable (%s); using dummy vectors for dry-run", exc)
        embedder = _DummyEmbedder()
    if hasattr(embedder, "configure_batching"):
        try:
            embedder.configure_batching(
                batch_size=batch_size,
                rate_limit_per_min=effective_rate_limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to configure embedding adapter batching controls: %s", exc)
    oraclevs_cfg = deps_settings.providers.get("oraclevs")
    if not isinstance(oraclevs_cfg, dict):
        raise ValueError("providers.oraclevs configuration missing")
    upserter = OracleVSUpserter(oraclevs_cfg)
    conn = None
    if not dry_run:
        # Use the upserter's native Oracle connection targeting the physical table.
        try:
            conn = upserter.connection
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Failed to open Oracle connection for ingestion") from exc
    elif update_alias and alias_name:
        logger.info("Skipping alias update during dry-run; alias remains unchanged")

    manifest_path = Path(manifest_path).resolve()
    # Use new ingestion manifest expansion
    try:
        resolved_files = validate_and_expand_manifest(str(manifest_path))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to read manifest {manifest_path}: {exc}") from exc
    logger.info("Loaded %d files from manifest", len(resolved_files))

    total_docs = 0
    total_chunks = 0
    inserted = 0
    skipped = 0
    errors = 0
    embedding_prepared = 0
    embedding_embedded = 0
    embedding_failed_batches = 0

    vector_buffer: List[Dict[str, Any]] = []
    # Per content_type counters
    content_counts: Dict[str, int] = {k: 0 for k in ("pdf", "docx", "pptx", "xlsx", "html", "txt")}

    for filepath in resolved_files:
        path_obj = Path(filepath)
        total_docs += 1
        doc_id_base = path_obj.stem
        try:
            items = route_and_load(filepath)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.exception("Failed to load %s: %s", filepath, exc)
            continue

        normalized_items: List[Dict[str, Any]] = []
        for item_idx, raw_item in enumerate(items, start=1):
            try:
                norm = normalize_metadata(raw_item)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning("Invalid metadata for %s item %d: %s", filepath, item_idx, exc)
                continue

            # Cleaning before sanitization
            from backend.ingest.text_cleaner import clean_text  # local import to avoid startup cost elsewhere
            preserve = False
            ctype_for_clean = str(norm["metadata"].get("content_type", "")).lower()
            if "spreadsheet" in ctype_for_clean or "xlsx" in ctype_for_clean:
                preserve = True
            text = clean_text(norm.get("text") or "", preserve_tables=preserve)
            if not text:
                continue

            # Sanitization per item
            if sanitize_if_enabled is not None:
                try:
                    text, _san_counts = sanitize_if_enabled(text, doc_id_base)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Sanitizer failed for %s (%s); continuing without changes", filepath, exc)
                else:
                    if _san_counts:
                        print(f"[sanitizer:{os.path.basename(filepath)}] {_san_counts}")

            norm["text"] = text
            normalized_items.append(norm)

        # Build chunker selection
        chunker_cfg = profile_cfg.get("chunker", {}) or {}
        chunker_type = (chunker_cfg.get("type") or "char").lower()
        effective_max = _effective_max_tokens(chunker_cfg, profile_cfg)
        if CHUNKING_DIAGNOSTIC:
            logger.info(
                "CHUNKING_DIAGNOSTIC chunker_type=%s effective_max=%s profile=%s toc_section_docx=%s",
                chunker_type,
                effective_max,
                profile_name,
                USE_TOC_SECTION_DOCX_CHUNKER,
            )

        local_idx_to_chunk_id: Dict[int, str] = {}

        def _append_chunks(chunks_in: List[Dict[str, Any]], base_ct: str, starting_idx: int = 1) -> int:
            nonlocal total_chunks
            appended = 0
            for idx, chunk in enumerate(chunks_in, start=starting_idx):
                ctext = (chunk.get("text") or "").strip()
                if not ctext:
                    continue
                cmeta_raw = dict(chunk.get("metadata") or {})
                ct_key_local = base_ct
                low_ct = str(cmeta_raw.get("content_type") or base_ct or "").lower()
                if "pdf" in low_ct:
                    ct_key_local = "pdf"
                elif "presentation" in low_ct or "ppt" in low_ct:
                    ct_key_local = "pptx"
                elif "spreadsheet" in low_ct or "xlsx" in low_ct:
                    ct_key_local = "xlsx"
                elif "html" in low_ct:
                    ct_key_local = "html"
                elif "wordprocessingml" in low_ct or "docx" in low_ct:
                    ct_key_local = "docx"
                elif "plain" in low_ct or "markdown" in low_ct or "txt" in low_ct:
                    ct_key_local = "txt"
                if ct_key_local:
                    content_counts[ct_key_local] = content_counts.get(ct_key_local, 0) + 1

                chunk_id = f"{doc_id_base}_chunk_{len(vector_buffer) + 1}"
                local_idx = cmeta_raw.get("chunk_local_index") if isinstance(cmeta_raw.get("chunk_local_index"), int) else None
                meta_out = dict(cmeta_raw)
                meta_out.update(
                    {
                        "doc_id": doc_id_base,
                        "chunk_id": chunk_id,
                        "profile": profile_name,
                        "index_name": index_name,
                        "distance_metric": profile_cfg.get("distance_metric", "dot_product"),
                    }
                )
                if local_idx is not None and (meta_out.get("chunk_type") or "text") != "figure":
                    local_idx_to_chunk_id[local_idx] = chunk_id
                parent_local_idx = meta_out.get("parent_chunk_local_index") or cmeta_raw.get("parent_chunk_local_index")
                if parent_local_idx and parent_local_idx in local_idx_to_chunk_id:
                    meta_out["parent_chunk_id"] = local_idx_to_chunk_id[parent_local_idx]
                if dedupe_enabled:
                    meta_out["hash_norm"] = _hash_normalize(ctext)
                vector_buffer.append({"text": ctext, "metadata": meta_out})
                appended += 1
            total_chunks += appended
            return appended

        def _strip_repeated_doc_title_prefix(chunks_in: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], str | None]:
            if len(chunks_in) < 2:
                return chunks_in, None
            first_lines: List[str] = []
            for ch in chunks_in:
                lines = (ch.get("text") or "").splitlines()
                if not lines or not lines[0].strip():
                    return chunks_in, None
                first_lines.append(lines[0].strip())
            candidate = first_lines[0]
            if not all(fl == candidate for fl in first_lines):
                return chunks_in, None
            cleaned: List[Dict[str, Any]] = []
            for idx, ch in enumerate(chunks_in):
                lines = (ch.get("text") or "").splitlines()
                body = "\n".join(lines[1:]).strip()
                meta = dict(ch.get("metadata") or {})
                meta["doc_title"] = candidate
                cleaned.append({"text": body if idx > 0 else ch.get("text") or "", "metadata": meta})
            return cleaned, candidate

        handled_indices: set[int] = set()

        # Structured chunking branches
        if chunker_type == "structured_pdf":
            pdf_items = [
                it
                for it in normalized_items
                if "pdf" in str(it["metadata"].get("content_type", "")).lower()
            ]
            if pdf_items:
                struct_chunks = chunk_structured_pdf_items(pdf_items, chunker_cfg, effective_max)
                if PIPELINE_CHUNK_DEBUG:
                    logger.info(
                        "PIPELINE_CHUNK_DEBUG structured_pdf items=%d chunks=%d",
                        len(pdf_items),
                        len(struct_chunks or []),
                    )
                _append_chunks(struct_chunks, "pdf")
                handled_indices.update({id(it) for it in pdf_items})

        if chunker_type == "structured_docx":
            docx_items = [
                it
                for it in normalized_items
                if (
                    "wordprocessingml" in str(it["metadata"].get("content_type", "")).lower()
                    or "docx" in str(it["metadata"].get("content_type", "")).lower()
                )
            ]
            if docx_items:
                if USE_TOC_SECTION_DOCX_CHUNKER:
                    toc_cfg = dict(chunker_cfg or {})
                    toc_cfg["effective_max_tokens"] = effective_max
                    struct_chunks = chunk_docx_toc_sections(docx_items, cfg=toc_cfg, source_meta={})
                    if PIPELINE_CHUNK_DEBUG:
                        logger.info(
                            "PIPELINE_CHUNK_DEBUG structured_docx items=%d chunks=%d DOCX chunker selected: toc_section_docx_chunker",
                            len(docx_items),
                            len(struct_chunks or []),
                        )
                else:
                    struct_chunks = chunk_structured_docx_items(docx_items, chunker_cfg, effective_max)
                    if PIPELINE_CHUNK_DEBUG:
                        logger.info(
                            "PIPELINE_CHUNK_DEBUG structured_docx items=%d chunks=%d DOCX chunker selected: structured_docx_chunker",
                            len(docx_items),
                            len(struct_chunks or []),
                        )
                doc_title_removed = None
                if struct_chunks:
                    struct_chunks, doc_title_removed = _strip_repeated_doc_title_prefix(struct_chunks)
                if PIPELINE_CHUNK_DEBUG:
                    if doc_title_removed:
                        logger.info("PIPELINE_CHUNK_DEBUG docx doc_title prefix removed from chunks: %s", doc_title_removed)
                    else:
                        logger.info("PIPELINE_CHUNK_DEBUG docx doc_title prefix not applied to chunks")
                _append_chunks(struct_chunks, "docx")
                handled_indices.update({id(it) for it in docx_items})

        # Fallback to existing fixed chunking for remaining items
        for item_idx, norm in enumerate(normalized_items, start=1):
            if id(norm) in handled_indices:
                continue
            meta = dict(norm["metadata"])
            text = norm.get("text") or ""
            chunks_text: List[str] = []
            if chunker_type == "tokens":
                max_tokens = int(chunker_cfg.get("size", 900) or 900)
                ov = float(chunker_cfg.get("overlap", 0.15) or 0.0)
                chunks_text = chunk_text_by_tokens(text, max_tokens=max_tokens, overlap=ov)
            else:
                size = int(chunker_cfg.get("size", 2000) or 2000)
                ov = int(chunker_cfg.get("overlap", 100) or 0)
                chunks_text = chunk_text(text, size=size, overlap=ov)

            ct_key = None
            if isinstance(meta.get("content_type"), str):
                low = meta["content_type"].lower()
                if "pdf" in low:
                    ct_key = "pdf"
                elif "presentation" in low or "ppt" in low:
                    ct_key = "pptx"
                elif "spreadsheet" in low or "xlsx" in low:
                    ct_key = "xlsx"
                elif "html" in low:
                    ct_key = "html"
                elif "wordprocessingml" in low or "docx" in low:
                    ct_key = "docx"
                elif "plain" in low or "markdown" in low or "txt" in low:
                    ct_key = "txt"
            if ct_key:
                content_counts[ct_key] = content_counts.get(ct_key, 0) + len(chunks_text)

            for idx, ctext in enumerate(chunks_text, start=1):
                chunk_id = f"{doc_id_base}_chunk_{item_idx}_{idx}"
                meta_out = dict(meta)
                meta_out.update(
                    {
                        "doc_id": doc_id_base,
                        "chunk_id": chunk_id,
                        "profile": profile_name,
                        "index_name": index_name,
                        "distance_metric": profile_cfg.get("distance_metric", "dot_product"),
                    }
                )
                if dedupe_enabled:
                    meta_out["hash_norm"] = _hash_normalize(ctext)
                vector_buffer.append({"text": ctext, "metadata": meta_out})
            total_chunks += len(chunks_text)

    if max_workers is not None and max_workers <= 0:
        raise ValueError("workers must be a positive integer")
    if max_workers is not None:
        logger.info("Using max_workers override: %d", max_workers)

    logger.info("Prepared %d chunks. Embedding in batches of %d", len(vector_buffer), batch_size)

    ensured_table = False
    logged_target_table = False
    for offset in range(0, len(vector_buffer), batch_size):
        batch = vector_buffer[offset : offset + batch_size]
        # Filter out empty/whitespace-only texts to avoid OCI 400 errors
        non_empty_idx = [i for i, item in enumerate(batch) if (item.get("text") or "").strip()]
        if not non_empty_idx:
            # Nothing to embed in this batch
            continue
        texts = [batch[i]["text"] for i in non_empty_idx]
        prepared = len(texts)
        embedding_prepared += prepared
        ok_vecs: List[List[float]] = []
        out_map: List[int] = []
        try:
            embeddings_result = embedder.embed_documents(texts, input_type="search_document")
            if isinstance(embeddings_result, tuple) and len(embeddings_result) == 2:
                ok_vecs = list(embeddings_result[0] or [])
                raw_map = embeddings_result[1]
                if isinstance(raw_map, dict):
                    out_map = [raw_map[k] for k in sorted(raw_map.keys())]
                elif isinstance(raw_map, list):
                    out_map = list(raw_map)
                else:
                    out_map = list(range(len(ok_vecs)))
            else:
                ok_vecs = list(embeddings_result or [])
                out_map = list(range(len(ok_vecs)))
        except Exception:
            logger.exception("Embedding job crashed at adapter level")
            ok_vecs = []
            out_map = []
            if prepared:
                embedding_failed_batches += 1

        embedded_count = len(ok_vecs)
        embedding_embedded += embedded_count
        # Ensure physical table exists with proper embedding dimension, once we know it
        if not dry_run and not ensured_table:
            if not ok_vecs:
                continue
            # Find first non-empty embedding to determine dimension
            dim = 0
            for _vec in ok_vecs:
                if isinstance(_vec, list) and len(_vec) > 0:
                    dim = len(_vec)
                    break
            if dim == 0:
                # No valid vectors in this batch; skip table ensure for now
                continue
            from backend.providers.oracle_vs.index_admin import ensure_alias, ensure_index_table
            ensure_index_table(conn, index_name, profile_cfg.get("distance_metric", "dot_product"), dim=dim)
            ensured_table = True
            # Ensure the upserter targets the physical table for all inserts
            upserter.set_target_table(index_name)

        if not logged_target_table and not dry_run:
            logger.debug("Upserting into physical table: %s", index_name)
            logged_target_table = True
        # Attach embeddings back to the corresponding payloads
        if out_map:
            for vector, local_idx in zip(ok_vecs, out_map):
                if not isinstance(local_idx, int):
                    continue
                if not isinstance(vector, list) or not vector:
                    continue
                if 0 <= local_idx < len(non_empty_idx):
                    batch_idx = non_empty_idx[local_idx]
                    batch[batch_idx]["embedding"] = vector
        else:
            for idx, embedding in zip(non_empty_idx, ok_vecs):
                batch[idx]["embedding"] = embedding
        # Only upsert items that actually have embeddings
        # Only upsert items with a non-empty embedding vector
        upsert_batch = [item for item in batch if ("embedding" in item and isinstance(item["embedding"], list) and len(item["embedding"]) > 0)]
        if not upsert_batch:
            continue
        batch_inserted, batch_skipped = upserter.upsert_vectors(upsert_batch, dedupe_enabled, dry_run=dry_run)
        inserted += batch_inserted
        skipped += batch_skipped
        logger.info(
            "Processed batch %d/%d",
            (offset // batch_size) + 1,
            (len(vector_buffer) + batch_size - 1) // batch_size,
        )

    evaluation_metrics: Optional[Dict[str, Any]] = None
    if evaluate_path:
        if not alias_name:
            raise ValueError("embeddings.alias.name must be configured for evaluation")
        try:
            evaluation_metrics = _evaluate_golden_queries(Path(evaluate_path), alias_name)
        except Exception as exc:  # noqa: BLE001
            evaluation_metrics = {
                "error": str(exc),
            }
            logger.exception("Golden query evaluation failed: %s", exc)

    # Collect token-limit counters from adapter if available
    tl_errors = int(getattr(embedder, "errors_token_limit", 0) or 0)
    tl_splits = int(getattr(embedder, "token_limit_splits", 0) or 0)
    tl_truncs = int(getattr(embedder, "token_limit_truncations", 0) or 0)
    tl_skipped = int(getattr(embedder, "skipped_token_limit", 0) or 0)

    embedding_skipped = max(0, embedding_prepared - embedding_embedded)
    embedding_saved = inserted if not dry_run else 0
    embedding_summary = {
        "prepared": embedding_prepared,
        "embedded": embedding_embedded,
        "saved": embedding_saved,
        "skipped": embedding_skipped,
        "failed_batches": embedding_failed_batches,
    }

    summary = EmbeddingJobSummary(
        docs=total_docs,
        chunks=total_chunks,
        inserted=inserted if not dry_run else 0,
        skipped=skipped,
        errors=errors,
        dry_run=dry_run,
        evaluation=evaluation_metrics,
        errors_token_limit=tl_errors,
        token_limit_splits=tl_splits,
        token_limit_truncations=tl_truncs,
        skipped_token_limit=tl_skipped,
        embedding_summary=embedding_summary,
    )
    logger.info(
        "Job summary: docs=%d chunks=%d inserted=%d skipped=%d errors=%d dry_run=%s",
        summary.docs,
        summary.chunks,
        summary.inserted,
        summary.skipped,
        summary.errors,
        summary.dry_run,
    )
    # Log per content type counters
    try:
        logger.info("Chunk counts by type: %s", {k: v for k, v in content_counts.items() if v})
    except Exception:
        pass
    # Log token-limit counters
    try:
        logger.info(
            "Token-limit counters: splits=%d truncations=%d skipped=%d provider_errors=%d",
            tl_splits,
            tl_truncs,
            tl_skipped,
            tl_errors,
        )
    except Exception:
        pass
    # Optional: include PDF OCR counters if available
    try:
        from backend.ingest.loaders import pdf_loader as _pdf_loader  # type: ignore

        logger.info(
            "PDF counters: pages_total=%s pages_ocr=%s pages_native_empty=%s",
            getattr(_pdf_loader, "pages_total", "-"),
            getattr(_pdf_loader, "pages_ocr", "-"),
            getattr(_pdf_loader, "pages_native_empty", "-"),
        )
    except Exception:
        pass

    # Update alias only after successful inserts
    if not dry_run and update_alias and alias_name and ensured_table:
        from backend.providers.oracle_vs.index_admin import ensure_alias
        ensure_alias(conn, alias_name, index_name)

    return summary


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Embed manifest job")
    parser.add_argument("--manifest", required=True, help="Path to the manifest JSONL file")
    parser.add_argument("--profile", help="Embedding profile name override")
    parser.add_argument("--domain-key", dest="domain_key", help="Override embedding target via embeddings.domains.<key>")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without DB writes")
    parser.add_argument(
        "--update-alias",
        action="store_true",
        help="Refresh alias/synonym to point at the active index",
    )
    parser.add_argument("--batch-size", type=int, dest="batch_size", help="Override batch size")
    parser.add_argument("--workers", type=int, help="Override worker count")
    parser.add_argument(
        "--evaluate",
        dest="evaluate_path",
        help="Path to golden queries YAML for retrieval spot-checks",
    )
    return parser


if __name__ == "__main__":
    cli = _build_cli()
    args = cli.parse_args()
    summary = run_embed_job(
        manifest_path=args.manifest,
        profile_name=args.profile,
        domain_key=args.domain_key,
        dry_run=args.dry_run,
        update_alias=args.update_alias,
        batch_size_override=args.batch_size,
        max_workers=args.workers,
        evaluate_path=args.evaluate_path,
    )
    print(f"Job summary: {format_summary(summary)}")
