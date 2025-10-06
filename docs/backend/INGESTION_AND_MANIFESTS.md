# Ingestion & Manifests

Ingestion is orchestrated by [backend/batch/embed_job.py](../../backend/batch/embed_job.py) and exposed through the CLI in [backend/batch/cli.py](../../backend/batch/cli.py). Documents listed in manifest files are chunked, optionally sanitized, embedded via OCI, and persisted in Oracle vector tables.

## Manifest Format
- Specification lives in [backend/ingest/MANIFEST_SPEC.md](../../backend/ingest/MANIFEST_SPEC.md).
- Each manifest is JSON Lines: one JSON object per document. Example:
  ```json
  {"path": "./data/standard/customer_journey.md", "profile": "standard_profile", "tags": ["customer", "journey"], "lang": "en", "priority": 6}
  {"path": "C:/docs/*.pdf", "doc_id": "support_pack", "priority": 9}
  ```
- Fields:
  - `path` (required): absolute or relative path; glob patterns expand recursively.
  - `doc_id`: overrides the generated identifier. When globbing, suffix `_N` is appended per match.
  - `profile`: embedding profile override; defaults to `embeddings.active_profile` from [config/app.yaml](../../backend/config/app.yaml).
  - `tags`, `lang`, `priority`: copied into chunk metadata for downstream filtering.
  - Additional keys are preserved in metadata but ignored by the core pipeline.

## Embed Job Workflow
1. **Configuration lookup** – Loads `settings.app['embeddings']` to resolve the active profile, physical index name, batching options, dedupe policy, and alias metadata.
2. **Strategy selection** – Attempts to import `backend.core.embeddings.embedding_strategy.build_strategy`. If absent, a character-based chunker is generated from profile settings (size, overlap, separator).
3. **Manifest expansion** – `_iter_manifest` yields `ManifestEntry` objects; `_expand_entry_paths` resolves relative paths and globs, raising on missing files.
4. **Document loading** – `_load_document` uses `Path.read_text()` for text assets or `PyPDF2` for PDFs.
5. **Sanitization (optional)** – When `backend.common.sanitizer.sanitize_if_enabled` is available and `SANITIZE_ENABLED` is not `off`, text is scrubbed before chunking. Audit counters print to stdout and `sanitizer.log` when redactions occur.
6. **Chunking & metadata** – The strategy produces `SimpleChunk` entries. `_ensure_chunk_metadata` merges manifest metadata, adds `chunk_id`, `index_name`, `distance_metric`, and optional dedupe hash.
7. **Batch embedding** – `make_embeddings()` builds an OCI embeddings client; batches are sized via `embeddings.batching.batch_size` (CLI `--batch-size` override). Optional `rate_limit_per_min` throttles requests.
8. **Oracle upsert** – `OracleVSUpserter.upsert_vectors()` inserts rows into the physical table (`index_name`). On the first non-empty batch it ensures the table exists via `ensure_index_table()` and binds `TO_VECTOR(:embedding_json)` payloads. Dedupe by `hash_norm` skips duplicates when enabled.
9. **Alias management** – On successful inserts and when `--update-alias` is used, `ensure_alias()` recreates the view defined in `embeddings.alias.name`, pointing it to the current physical table.
10. **Summary & evaluation** – Returns `EmbeddingJobSummary` with counts. If `--evaluate` points to YAML such as [backend/ingest/golden_queries.yaml](../../backend/ingest/golden_queries.yaml), retrieval quality is measured via hit rate and MRR using the freshly populated alias.

## CLI Usage
```bash
python -m backend.batch.cli embed \
  --manifest backend/ingest/examples/manifest_standard.jsonl \
  --profile standard_profile \
  --update-alias \
  --evaluate backend/ingest/golden_queries.yaml
```
Arguments:
- `--dry-run`: run chunking and embedding (with dummy vectors if OCI is unavailable) without DB writes or alias changes.
- `--batch-size` / `--workers`: override batching configuration. (`--workers` is validated but currently not used for parallelism.)
- `--evaluate`: compute golden-query metrics after ingestion.

## Golden Query Evaluation
- Loader `_load_golden_queries()` accepts list or `queries` field with `query`, optional `expect_doc_ids`, optional `expect_phrases`, and optional `top_k` per query.
- `_evaluate_golden_queries()` reuses `make_vector_store()` to query the alias view, scoring:
  - **hit rate**: proportion of queries whose expected doc IDs appear in the top `k`.
  - **MRR**: mean reciprocal rank of the first matching doc.
  - **phrase hit**: boolean flag indicating whether any expected phrase appears in the returned snippets.
- Metrics are printed (`[eval] ...`) and attached to the job summary.

## Operational Considerations
- Always run embed jobs from an environment where the OCI and Oracle credentials match those used by the API. `backend.app.deps` assumes consistent configuration.
- Large manifests can exhaust memory because `vector_buffer` collects all chunks before batching. Split manifests when indexing massive corpora.
- When dedupe is enabled (`embeddings.dedupe.by_hash=true`), sanitization must produce stable text (e.g., consistent placeholder hashing) to avoid over-suppressing legitimate variants.

## TODO
- Implement token-based chunking for the `standard_profile` (`chunker.type: tokens`) to honour profile intentions.
- Wire `--workers` into actual parallel execution if ingestion throughput becomes a bottleneck.
