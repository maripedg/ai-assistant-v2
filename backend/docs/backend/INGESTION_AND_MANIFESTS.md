# Ingestion & Manifests
Last updated: 2025-11-07

The ingestion pipeline moves documents from ad‑hoc uploads to Oracle Vector Search tables that the chat API reads via an alias view.

## Upload ➜ Job ➜ Alias Flow
1. **Upload** – `POST /api/v1/uploads` accepts a single file (`multipart/form-data`) plus optional `source`, `tags`, and `lang_hint`. Files are saved under the staging directory defined by `STAGING_DIR`.
2. **Staging metadata** – [backend/app/services/ingest.py](../../backend/app/services/ingest.py) stores upload metadata (`upload_id`, filename, size, `content_type`, checksum) in `uploads.json`.
3. **Job creation** – `POST /api/v1/ingest/jobs` receives `upload_ids`, `profile`, optional tags/lang/priority, and switches such as `update_alias` and `evaluate`. The service snapshots upload metadata, writes a manifest, and queues work on a background thread.
4. **Embedding** – [backend/batch/embed_job.py](../../backend/batch/embed_job.py) (invoked via the ingest service or CLI) loads manifests, sanitizes text (see [SANITIZATION.md](./SANITIZATION.md)), chunks content, requests embeddings from OCI, and upserts into the target Oracle table.
5. **Alias rotation** – When `update_alias=true`, `ensure_alias()` repoints the alias view (e.g., `MY_DEMO`) to the new table once inserts succeed. Metrics (`files_total`, `chunks_indexed`, `dedupe_skipped`) and evaluation summaries (if `evaluate=true`) are stored alongside the job.

## Manifest Format
- JSON Lines; each line is a document descriptor.
- Required: `path`.
- Optional: `doc_id`, `profile`, `tags` (array), `lang`, `priority`, `metadata`.
- Paths may contain globs; relative paths resolve against the manifest file.

Example:
```json
{"path": "./data/docs/*.pdf", "profile": "legacy_profile", "tags": ["kb"], "lang": "es"}
{"path": "C:/files/runbook.md", "doc_id": "ops_runbook", "priority": 5}
```

## Common Error Codes
| Stage | Status | Detail |
| --- | --- | --- |
| Upload | `400` | Empty body (`"Uploaded file is empty"`). |
| Upload | `413` | Size > `MAX_UPLOAD_MB` (reported in bytes). |
| Upload | `415` | MIME type not present in `ALLOW_MIME`. |
| Upload | `500` | Storage failure (`"Upload failed"`). |
| Job creation | `404` | Missing uploads (`"Upload not found: <id>"`). |
| Job creation | `409` | Active job already references one of the uploads (`ConflictError`). |
| Job creation | `422` | Unknown profile or invalid `upload_ids`. |

> NOTE: The frontend surfaces these statuses verbatim in the Admin ➜ Documents & Embeddings page. Keep the messages concise and actionable.

## Operational Tips
- **Sanitization** – Set `SANITIZE_ENABLED=shadow` in staging to observe counters before enabling hard redaction. Sanitizer output feeds into chunk dedupe; keep placeholder hashes stable.
- **Alias safety** – Use `update_alias=false` when testing new profiles. Inspect job summaries, run golden queries, then re‑run with `update_alias=true` when satisfied.
- **Dual writes** – If `storage.dual_write=true`, ingestion metadata is mirrored to JSON for easier local debugging. Uploads still read from the primary backend defined by `storage.feedback.mode`.
- **Typical validation path** – After a successful job, hit `/chat` with a question that should match the new content and confirm `mode` is `rag` or `hybrid`. If fallback persists, check manifest coverage or sanitization settings.
- **DOCX figures** – Opt-in via `DOCX_EXTRACT_IMAGES=true` to write embedded DOCX images under `<RAG_ASSETS_DIR>/<doc_id>/img_<NNN>.<ext>`. Pair with `DOCX_INLINE_FIGURE_PLACEHOLDERS=true` to see `[FIGURE:<figure_id>]` markers in text chunks and `DOCX_FIGURE_CHUNKS=true` to emit companion `chunk_type=figure` entries that reference the parent chunk and `image_ref`. With all flags off, chunk text matches the previous behaviour.

## DOCX Inline Figures
- **Feature flags**: `DOCX_EXTRACT_IMAGES` writes embedded DOCX images under `RAG_ASSETS_DIR` (default `./data/rag-assets`), `DOCX_INLINE_FIGURE_PLACEHOLDERS` injects `[FIGURE:<figure_id>]` markers at the inline position, and `DOCX_FIGURE_CHUNKS` emits `chunk_type=figure` entries that inherit document/section metadata plus `figure_id`, `image_ref` (relative only), and `parent_chunk_id/parent_chunk_local_index`. `DOCX_IMAGE_DEBUG=1` logs per-image extraction details; loader detects images by scanning `a:blip` embeds and resolves rIds via `word/_rels/document.xml.rels`. Leaving all flags unset keeps legacy text-only behaviour.
- **Filesystem layout**: Images land at `<RAG_ASSETS_DIR>/<doc_id>/img_<NNN>.<ext>` with `figure_id=<doc_id>_img_<NNN>` and `image_ref=<doc_id>/img_<NNN>.<ext>`. `RAG_ASSETS_DIR` is created on demand when writable.
- **Docker/local volumes**: Mount the assets directory when running in containers, e.g. `./data/rag-assets:/app/data/rag-assets`, so figure references stay portable and images persist between runs.
- **Chunking & embeddings**: Placeholders preserve inline order; figure chunks store a deterministic text description only (no binary embeddings) to make the related image retrievable. SOP/procedure chunking repeats the procedure title on split chunks to avoid mixing steps across procedures.
- **Troubleshooting**: Check `DOCX_IMAGES_SUMMARY` (loader) and `DOCX_FIGURE_CHUNKING_SUMMARY` (chunker). If `embed_rids` > 0 but `rels_mapped=0`, relationships parsing failed; if `zip_member_miss > 0`, the relationship target could not be located in the DOCX; if `image_emit_skip_reason=flags_disabled` the inline/figure flags were off; if `images_written` > 0 but `figure_chunks=0`, the chunker is not seeing `block_type=image` blocks.

## DOCX Admin Section Filtering
- **Purpose**: Filter administrative sections (Document Control, Version History, Reviewers, Scope and Purpose, etc.) based on section headings while preserving procedural tables and steps.
- **Behavior**: Filtering is heading-based; only items whose section heading matches admin patterns are excluded. Optional stop patterns disable the initial exclusion window once a procedure section is reached.
- **Pipeline note**: The `structured_docx` profile uses `toc_section_docx_chunker` when enabled; this filtering applies in that path as well.
- **Config** (under `embeddings.profiles.<profile>.chunker`):
```yaml
drop_admin_sections: true
admin_sections:
  enabled: true
  match_mode: heading_regex
  heading_regex:
    - "(?i)^document control$"
    - "(?i)^version history$"
    - "(?i)^reviewers?$"
    - "(?i)^scope and purpose( of the document)?$"
    - "(?i)^approvals?$"
    - "(?i)^distribution( list)?$"
  stop_excluding_after_heading_regex:
    - "(?i)^(procedure|steps to be followed|step\\s*1)\\b"
```

## DOCX Procedure Boundaries
- **Boundary detection**: Procedures open on heading level 1. SOP headings like `SOP4: ...` are treated as procedures and labeled as `Procedure: 4. <title>`.
- **Section chunking**: Within a procedure, the chunker chooses the deepest available heading level (prefer level 3, else level 2) and emits one chunk per heading at that level.
- **Parent context**: Each section chunk begins with:
  - `Procedure: <procedure label>`
  - `Section: <section heading text>`
  - `Path: <procedure> | <parent if any> | <section>`
- **Numeric prefixes**: If a heading text already contains a numeric prefix (e.g., `4.1.2`), it is preserved as-is in the `Section:` line. If no prefix exists, the chunker does not synthesize numbers.
- **TOC hierarchy**: When the DOCX TOC contains nested entries, the chunker can resolve full numeric prefixes for section labels, but headings still drive boundaries.

## CLI Shortcut
```bash
python -m backend.batch.cli embed \
  --manifest backend/ingest/examples/my_pdfs.jsonl \
  --profile legacy_profile \
  --update-alias \
  --evaluate backend/ingest/golden_queries.yaml
```
Domain-targeted runs (one domain per job):
```bash
python -m backend.batch.cli embed --manifest backend/ingest/examples/my_docs.jsonl --profile standard_profile --domain-key TS_SBC --update-alias
python -m backend.batch.cli embed --manifest backend/ingest/examples/my_docs.jsonl --profile standard_profile --domain-key TS_STP --update-alias
```
The CLI shares the same services and config as the API worker, so `.env`, OCI profiles, and Oracle grants must match.
