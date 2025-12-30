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
