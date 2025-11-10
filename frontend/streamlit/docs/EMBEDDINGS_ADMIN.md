# Documents & Embeddings (Admin)
Last updated: 2025-11-07

## Purpose
Allow administrators to stage uploads, create ingestion jobs, and monitor the basic status returned by the backend. UI lives in `app/views/admin/embeddings.py` and relies on `app.services.api_client` for HTTP calls.

## Workflow
1. **Select files** – Files enter a client-side queue with statuses (`Queued`, `Uploading`, `Uploaded`, `Failed`). The queue honours `UPLOAD_CONCURRENCY` (default 3).
2. **Upload** – Each file issues `POST /api/v1/uploads` with multipart form data plus optional `source`, `tags`, `lang_hint`. Success responses include `upload_id`, checksum, and MIME metadata.
3. **Create embedding job** – The UI bundles all staged `upload_ids` into `POST /api/v1/ingest/jobs` along with profile, tags, language, priority, `update_alias`, and `evaluate` values pulled from the sidebar controls.
4. **Alias update** – Operators should set `update_alias=true` only after verifying the job is ready to replace the live index. Otherwise, run a dry run first, evaluate results, then re-run with alias updates enabled.

## Headers & Auth
- Every upload/job request adds `Authorization: Bearer <token>` when `AUTH_MODE=db` or `AUTH_ENABLED=true`. If a token is missing the view shows a banner (“Sign in to upload documents”).
- CORS: ensure the backend `server.cors.allow_origins` list contains the Streamlit origin.

## Common Errors
| Status | Surface | UI Message |
| --- | --- | --- |
| 400 | Upload | “Upload failed. Check the file and retry.” (empty or corrupted). |
| 413 | Upload | “File exceeds backend limit (MAX_UPLOAD_MB).” Ask the user to split the document. |
| 415 | Upload | “File type not allowed. Try PDF, DOCX, PPTX, XLSX, TXT, or HTML.” |
| 404 | Job create | “Upload not found. Refresh and upload again.” (staged file expired). |
| 409 | Job create | “An embedding job already references one of these uploads.” Wait for completion or clear the queue. |
| 422 | Job create | “Profile not recognized” or validation errors on `upload_ids`. |

## Metadata Hints
- Allowed formats shown to the user come from `ALLOWED_MIME_HINT`; backend enforcement remains authoritative via `ALLOW_MIME`.
- Upload cards display `upload_id`, `size_bytes`, and `lang_hint`. Use this info when matching backend job payloads.
- CSV export is not part of this view; operators rely on `/api/v1/ingest/jobs/{job_id}` for advanced diagnostics.

## Security Notes
- Never transmit raw filesystem paths; the browser sends file buffers only.
- Ensure HTTPS in production; JWTs are stored as cookies when “Remember me” is available.
- Streamlit caches UI state per session. Use the “Clear uploads” button after a successful job to avoid resubmitting stale `upload_ids`.
