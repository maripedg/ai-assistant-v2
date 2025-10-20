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
# Ingestion & Manifests

## Purpose
Describe how content is ingested, chunked, embedded, and inserted into Oracle Vector Search; define the manifest format.

## Components / Architecture
- CLI entrypoint: `backend/batch/cli.py`
- Job implementation: `backend/batch/embed_job.py`
- Strategy hook (optional): `backend/core/embeddings/embedding_strategy.py`
- Providers: OracleVS admin helpers `backend/providers/oracle_vs/index_admin.py`

## Parameters & Env
- Uses `backend/config/app.yaml` → `embeddings.active_profile` and `profiles.*` for chunker and `distance_metric`.
- Uses `backend/config/providers.yaml` for DB connection and OCI settings.
- Sanitizer can run pre‑embedding (see [Sanitization](./SANITIZATION.md)).

## Manifest Schema
Newline‑delimited JSON (JSONL). Minimal field:

```json
{ "path": "C:/path/to/files/*.pdf" }
```

Optional fields per line (if supported by your pipeline):
- `doc_id` (string), `profile` (string), `tags` (string[]), `lang` (string), `priority` (int)

Example from repo:

```json
{ "path": "C:/Users/Mario Pedraza/Desktop/Development/ai-assistant-v2/data/docs/*.md" }
```

## Examples
Run the embed job (creates/validates table, embeds, inserts, and updates alias if requested):

```bash
python -m backend.batch.cli embed \
  --manifest backend/ingest/examples/my_pdfs.jsonl \
  --profile legacy_profile \
  --update-alias
``;

## Ops Notes
- The job skips empty/whitespace‑only texts to prevent 400s from the embed API.
- The alias view is recreated with `JSON_SERIALIZE(METADATA RETURNING CLOB)` for OracleVS compatibility.
- Ensure DB credentials and `OCI_CONFIG_*` are valid before running.

## Supported Types
The ingestion loaders produce items with the following metadata keys. Chunkers are applied after sanitization:

| Type | Loader | Required metadata | Notes |
| ---- | ------ | ----------------- | ----- |
| pdf  | pdf_loader | `source` (abs path), `content_type="application/pdf"`, `page` (int), `has_ocr` (bool) | One item per page; OCR optional and only for pages with no text |
| docx | docx_loader | `source`, `content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"` | Sections split by Heading1 if present; otherwise one item |
| pptx | pptx_loader | `source`, `content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation"`, `slide_number` (int), `has_notes` (bool) | One item per slide; notes appended when available |
| xlsx | xlsx_loader | `source`, `content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"`, `sheet_name` (str), `n_rows` (int), `n_cols` (int) | One item per sheet; concise text summary, not full data dump |
| html | html_loader | `source`, `content_type="text/html"`, `section_path` (e.g., `h1>h2`) | Split by top‑level sections where possible |
| txt  | txt_loader  | `source`, `content_type="text/plain"` | Single item or paragraph blocks if large |

Chunkers
- Char: `char_chunker` with `size` and `overlap` (profile‑driven).
- Tokens: `token_chunker` with `max_tokens` and fractional `overlap` (profile‑driven).

Manifest
- JSONL lines support `path` (file or glob), optional `tags`, optional `content_type` hint. Globs are expanded relative to the manifest file. Missing files are skipped with a warning.

## Cleaning Policy
Before sanitization and chunking, every loader’s text passes through a deterministic cleaner:
- Unicode NFC normalization
- Removal of invisible chars (zero width); NBSP mapped to space; soft hyphen removed
- Ligature replacement (ﬁ→fi, ﬂ→fl)
- Line ending normalization; trailing space trim; collapse multiple spaces (not newlines)
- Safe de‑hyphenation at line breaks (avoids true hyphenated terms)
- Optional header/footer de‑dup (conservative heuristic)
- Table‑preserving mode (for XLSX summaries) keeps row structure
- Noise blocks filtered (<10 alphabetic chars unless heading‑like)

Order in pipeline: loader → CLEAN → sanitize → chunk → embed

## See also
- [Embedding & Retrieval](./EMBEDDING_AND_RETRIEVAL.md)
- [Runbook](./RUNBOOK.md)
