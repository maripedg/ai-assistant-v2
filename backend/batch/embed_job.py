
"""Embedding batch job entry point."""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.app.deps import make_embeddings, settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


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
            for vector in vectors:
                meta = dict(vector["metadata"])
                index_name = meta.pop("index_name")
                hash_norm = meta.get("hash_norm")
                if dedupe and hash_norm:
                    cur.execute(
                        f"SELECT 1 FROM {self._table} WHERE index_name = :1 AND hash_norm = :2 FETCH FIRST 1 ROWS ONLY",
                        (index_name, hash_norm),
                    )
                    if cur.fetchone():
                        skipped += 1
                        continue

                metadata_json = json.dumps(meta, ensure_ascii=False)
                embedding_json = json.dumps(vector.get("embedding", []))
                cur.execute(
                    f"INSERT INTO {self._table} (index_name, doc_id, chunk_id, text, metadata_json, embedding_json, hash_norm) "
                    "VALUES (:1, :2, :3, :4, :5, :6, :7)",
                    (
                        index_name,
                        meta.get("doc_id"),
                        meta.get("chunk_id"),
                        vector["text"],
                        metadata_json,
                        embedding_json,
                        hash_norm,
                    ),
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

    if not dry_run:
        try:
            from backend.app.deps import make_vector_store
        except ModuleNotFoundError as exc:  # pragma: no cover - defensive
            raise ModuleNotFoundError(
                "The 'oracledb' package is required for DB writes. Install it with `pip install oracledb` or run with `--dry-run`."
            ) from exc

        try:
            vector_store = make_vector_store(embedder)
        except ModuleNotFoundError as exc:  # pragma: no cover - defensive
            raise ModuleNotFoundError(
                "The 'oracledb' package is required for DB writes. Install it with `pip install oracledb` or run with `--dry-run`."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Failed to initialize Oracle vector store") from exc

        conn = getattr(vector_store, "conn", None)
        if conn is None:
            raise RuntimeError("Oracle vector store did not expose a database connection")

        upserter.attach_connection(conn)

        from backend.providers.oracle_vs.index_admin import ensure_alias, ensure_index_table

        ensure_index_table(conn, index_name, profile_cfg.get("distance_metric", "dot_product"))
        if update_alias and alias_name:
            ensure_alias(conn, alias_name, index_name)
    elif update_alias and alias_name:
        logger.info("Skipping alias update during dry-run; alias remains unchanged")

    manifest_path = Path(manifest_path).resolve()
    manifest_entries = list(_iter_manifest(manifest_path))
    logger.info("Loaded %d manifest entries", len(manifest_entries))

    total_docs = 0
    total_chunks = 0
    inserted = 0
    skipped = 0
    errors = 0

    vector_buffer: List[Dict[str, Any]] = []
    manifest_root = manifest_path.parent

    for entry in manifest_entries:
        try:
            resolved_paths = _expand_entry_paths(entry.path, manifest_root)
        except FileNotFoundError as exc:
            errors += 1
            logger.error("Manifest entry path not found: %s", exc)
            continue

        if not resolved_paths:
            logger.warning("Manifest entry produced no matches: %s", entry.path)
            continue

        for match_idx, resolved_path in enumerate(resolved_paths, start=1):
            try:
                text = _load_document(resolved_path)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.exception("Failed to load %s: %s", resolved_path, exc)
                continue

            total_docs += 1
            doc_id = entry.doc_id or resolved_path.stem
            if entry.doc_id and len(resolved_paths) > 1:
                doc_id = f"{entry.doc_id}_{match_idx}"

            base_meta = {
                "source": str(resolved_path),
                "doc_id": doc_id,
                "profile": entry.profile or profile_name,
                "lang": entry.lang,
                "tags": entry.tags,
                "priority": entry.priority,
            }
            chunks = strategy.chunk(text, base_meta)
            for idx, chunk in enumerate(chunks, start=1):
                chunk_id = f"{doc_id}_chunk_{idx}"
                meta = _ensure_chunk_metadata(chunk, base_meta, chunk_id)
                meta["index_name"] = index_name
                if dedupe_enabled:
                    meta["hash_norm"] = _hash_normalize(chunk.text)
                vector_buffer.append({"text": chunk.text, "metadata": meta})
            total_chunks += len(chunks)

    if max_workers is not None and max_workers <= 0:
        raise ValueError("workers must be a positive integer")
    if max_workers is not None:
        logger.info("Using max_workers override: %d", max_workers)

    logger.info("Prepared %d chunks. Embedding in batches of %d", len(vector_buffer), batch_size)

    last_batch_time = 0.0
    for offset in range(0, len(vector_buffer), batch_size):
        if rate_interval:
            elapsed = time.time() - last_batch_time
            if elapsed < rate_interval:
                time.sleep(rate_interval - elapsed)
        batch = vector_buffer[offset : offset + batch_size]
        texts = [item["text"] for item in batch]
        embeddings = embedder.embed_documents(texts)
        for payload, embedding in zip(batch, embeddings):
            payload["embedding"] = embedding
        batch_inserted, batch_skipped = upserter.upsert_vectors(batch, dedupe_enabled, dry_run=dry_run)
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
