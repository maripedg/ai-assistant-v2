**Documents & Embeddings (Admin)**

Purpose

- Enable administrators to upload documents (one file per request) and trigger a single embedding job that references every collected `upload_id`.

User Flow

1. Select files. Each file appears in a staging list with status: Queued, Uploading, Uploaded, or Failed.
2. Click **Upload** to start per-file multipart/form-data requests against `POST /api/v1/uploads`.
3. Click **Create Embedding Job** once the desired files are uploaded. The client posts all `upload_ids` to `POST /api/v1/ingest/jobs`.
4. Show a confirmation toast with the resulting `job_id` and a **Go to Assistant** link. No progress tracking lives on this page.

Permissions & Security

- UI gate keeps the view limited to admin role. Non-admin users see the access-restricted banner (see TESTING).
- Backend enforces scopes/roles for `/api/v1/uploads` and `/api/v1/ingest/jobs`. Forward JWT headers when `AUTH_ENABLED` is true and scope env vars are configured (`AUTH_TOKEN_SCOPE_UPLOAD`, `AUTH_TOKEN_SCOPE_INGEST`).
- Ensure backend CORS settings allow the Streamlit origin.
- Never accept raw filesystem paths; only file buffers are uploaded.

Upload & Job Calls

- Uploads: `POST /api/v1/uploads` (multipart/form-data, single file per request with optional `source`, `tags`, `lang_hint`). Backend reference: `../../backend/docs/API_REFERENCE.md`.
- Jobs: `POST /api/v1/ingest/jobs` (JSON payload containing `upload_ids`, `profile`, `tags`, `lang_hint`, `priority`, `update_alias`, `evaluate`). The UI defaults to `DEFAULT_PROFILE`; confirm the value matches backend config.

Error Handling

- 415 Unsupported Media Type -> "File type not allowed. Allowed: PDF, DOCX, PPTX, XLSX, TXT, HTML."
- 413 Payload Too Large -> "File exceeds backend limit (see MAX_UPLOAD_MB)."
- 422 Unknown Profile -> "Profile not recognized. Update DEFAULT_PROFILE or backend config."
- 404 Upload Not Found -> "Upload expired or missing. Refresh the list and retry."
- 409 Conflict -> "An embedding job already references one of these uploads. Wait or clear the queue."
- Any other failure renders "Upload failed" or "Job creation failed" with log guidance; backend remains source of truth.

File Types & Limits

- Allowed MIME types follow backend defaults: `application/pdf`, Office Open XML (docx, pptx, xlsx), `text/plain`, `text/html`. Use `ALLOWED_MIME_HINT` in the UI when present.
- Size limit derives from backend (`MAX_UPLOAD_MB`). Frontend displays warnings; enforcement happens server-side.

Concurrency Guidance

- Recommended `UPLOAD_CONCURRENCY` range is 3 to 5. Additional files wait in the client queue until a slot frees up.
- Concurrency is configurable through `.env`; disable the upload button when the queue is full.

Security Notes

- Send JWT or session headers on every admin call.
- Prefer HTTPS for production deployments and confirm backend CORS rules.

Links & References

- Backend API reference: `../../backend/docs/API_REFERENCE.md` (Documents & Embeddings section).
- Backend error catalog: `../../backend/docs/API_ERRORS.md`.
- Configuration variables: `./CONFIGURATION.md`.
