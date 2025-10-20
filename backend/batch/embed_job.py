
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

from backend.app.deps import make_embeddings, settings
from backend.ingest.manifests.spec import validate_and_expand_manifest
from backend.ingest.router import route_and_load
from backend.ingest.normalizer import normalize_metadata
from backend.ingest.chunking.char_chunker import chunk_text
from backend.ingest.chunking.token_chunker import chunk_text_by_tokens

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


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
    return base

PDF_EXTENSIONS = {".pdf"}


@dataclass
class SimpleChunk:
    text: str
    metadata: Dict[str, Any]


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

    retrieval_cfg = settings.app.get("retrieval", {}) or {}
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
    dry_run: bool = False,
    update_alias: bool = False,
    batch_size_override: Optional[int] = None,
    max_workers: Optional[int] = None,
    evaluate_path: Optional[str] = None,
) -> EmbeddingJobSummary:
    app_settings = settings.app
    embeddings_cfg = app_settings.get("embeddings", {}) or {}
    profile_name = profile_name or embeddings_cfg.get("active_profile")
    if not profile_name:
        raise ValueError("No embedding profile specified")

    profiles = embeddings_cfg.get("profiles", {}) or {}
    profile_cfg = profiles.get(profile_name)
    if not isinstance(profile_cfg, dict):
        raise ValueError(f"Embedding profile '{profile_name}' not defined")

    index_name = profile_cfg.get("index_name")
    if not index_name:
        raise ValueError(f"Embedding profile '{profile_name}' missing index_name")

    alias_cfg = embeddings_cfg.get("alias", {}) or {}
    alias_name = alias_cfg.get("name")
    batching_cfg = embeddings_cfg.get("batching", {}) or {}
    batch_size = int(batching_cfg.get("batch_size", 32))
    if batch_size_override is not None:
        if batch_size_override <= 0:
            raise ValueError("batch_size must be a positive integer")
        batch_size = batch_size_override
    rate_limit = batching_cfg.get("rate_limit_per_min")
    rate_interval = 60.0 / float(rate_limit) if rate_limit else 0.0
    dedupe_cfg = embeddings_cfg.get("dedupe", {}) or {}
    dedupe_enabled = bool(dedupe_cfg.get("by_hash", False))

    strategy = _build_strategy(profile_name, app_settings)
    try:
        embedder = make_embeddings()
    except Exception as exc:  # noqa: BLE001
        if not dry_run:
            raise
        logger.warning("Embeddings unavailable (%s); using dummy vectors for dry-run", exc)
        embedder = _DummyEmbedder()
    oraclevs_cfg = settings.providers.get("oraclevs")
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

            meta = dict(norm["metadata"])
            ctype_simple = meta.get("content_type")
            if isinstance(ctype_simple, str):
                # Map common MIME to simple type keywords
                if ctype_simple.startswith("application/") or ctype_simple.startswith("text/"):
                    # leave; normalizer maps to MIME; counters use keyword below
                    pass
            # Build chunker selection
            chunker_cfg = profile_cfg.get("chunker", {}) or {}
            chunks_text: List[str] = []
            if (chunker_cfg.get("type") or "char").lower() == "tokens":
                max_tokens = int(chunker_cfg.get("size", 900) or 900)
                ov = float(chunker_cfg.get("overlap", 0.15) or 0.0)
                chunks_text = chunk_text_by_tokens(text, max_tokens=max_tokens, overlap=ov)
            else:
                size = int(chunker_cfg.get("size", 2000) or 2000)
                ov = int(chunker_cfg.get("overlap", 100) or 0)
                chunks_text = chunk_text(text, size=size, overlap=ov)

            # Counters by simplified type token
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

    last_batch_time = 0.0
    ensured_table = False
    logged_target_table = False
    for offset in range(0, len(vector_buffer), batch_size):
        if rate_interval:
            elapsed = time.time() - last_batch_time
            if elapsed < rate_interval:
                time.sleep(rate_interval - elapsed)
        batch = vector_buffer[offset : offset + batch_size]
        # Filter out empty/whitespace-only texts to avoid OCI 400 errors
        non_empty_idx = [i for i, item in enumerate(batch) if (item.get("text") or "").strip()]
        if not non_empty_idx:
            # Nothing to embed in this batch
            continue
        texts = [batch[i]["text"] for i in non_empty_idx]
        embeddings = embedder.embed_documents(texts, input_type="search_document")
        # Ensure physical table exists with proper embedding dimension, once we know it
        if not dry_run and not ensured_table:
            if not embeddings:
                continue
            dim = len(embeddings[0])
            from backend.providers.oracle_vs.index_admin import ensure_alias, ensure_index_table
            ensure_index_table(conn, index_name, profile_cfg.get("distance_metric", "dot_product"), dim=dim)
            ensured_table = True
            # Ensure the upserter targets the physical table for all inserts
            upserter.set_target_table(index_name)

        if not logged_target_table and not dry_run:
            logger.debug("Upserting into physical table: %s", index_name)
            logged_target_table = True
        # Attach embeddings back to the corresponding payloads
        for idx, embedding in zip(non_empty_idx, embeddings):
            batch[idx]["embedding"] = embedding
        # Only upsert items that actually have embeddings
        upsert_batch = [item for item in batch if "embedding" in item]
        if not upsert_batch:
            continue
        batch_inserted, batch_skipped = upserter.upsert_vectors(upsert_batch, dedupe_enabled, dry_run=dry_run)
        inserted += batch_inserted
        skipped += batch_skipped
        last_batch_time = time.time()
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

    summary = EmbeddingJobSummary(
        docs=total_docs,
        chunks=total_chunks,
        inserted=inserted if not dry_run else 0,
        skipped=skipped,
        errors=errors,
        dry_run=dry_run,
        evaluation=evaluation_metrics,
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
        dry_run=args.dry_run,
        update_alias=args.update_alias,
        batch_size_override=args.batch_size,
        max_workers=args.workers,
        evaluate_path=args.evaluate_path,
    )
    print(f"Job summary: {format_summary(summary)}")
