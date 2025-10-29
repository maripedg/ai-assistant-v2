# API Reference

This reference documents the active backend endpoints and expected payloads. Unless noted otherwise, endpoints are unauthenticated by default. Pagination uses `limit` (default 20, max 100) and `offset` (default 0). Sanitizer placeholders such as `[EMAIL]` or `[PHONE]` may appear in persisted comments when sanitization runs in `shadow` or `on` modes. Feature flags (`features.users_api`, `features.feedback_api`) must stay enabled (default) for those routers to load.

## Health

- Summary: Liveness probe plus dependency snapshots.
- Method & Path: GET `/healthz`
- Headers: `Accept: application/json`
- Query Params: none
- Request Body: none
- Success 200 Example:
```json
{
  "ok": true,
  "services": {
    "embeddings": "up",
    "llm_primary": "up",
    "llm_fallback": "up"
  }
}
```
- Errors: 500 on unexpected failures (per dependency probe).

## Chat

- Summary: Question answering with retrieval-augmented generation.
- Method & Path: POST `/chat`
- Headers:
  - `Content-Type: application/json`
  - Response header: `X-Answer-Mode: extractive|rag|hybrid|fallback`
- Request Body schema:
```json
{
  "question": "string"
}
```
- Example Request:
```json
{
  "question": "How do I configure Oracle SBC TLS?"
}
```
- Success 200 Response (representative):
```json
{
  "question": "How do I configure Oracle SBC TLS?",
  "answer": "Update the SBC TLS profile and upload the certificate via the security menu...",
  "answer2": null,
  "answer3": null,
  "retrieved_chunks_metadata": [
    {"chunk_id": "doc-1#0", "source": "sbc_guide.pdf", "similarity": 0.72, "text": "..."}
  ],
  "mode": "rag",
  "sources_used": "all",
  "used_chunks": [
    {"chunk_id": "doc-1#0", "source": "sbc_guide.pdf", "score": 0.72, "snippet": "TLS certificates are managed from..."}
  ],
  "decision_explain": {
    "score_mode": "normalized",
    "distance": "dot_product",
    "max_similarity": 0.72,
    "threshold_low": 0.25,
    "threshold_high": 0.55,
    "top_k": 12,
    "effective_query": "configure sbc tls",
    "short_query_active": false,
    "used_llm": "primary",
    "mode": "rag"
  }
}
```
- Common Errors:
  - 422 validation error (missing `question`).
  - 500 internal error (retrieval or LLM failure).

## Auth

Base path: `/api/v1/auth`. Tokens are JWTs signed with server-side secret; refresh reuses existing claims.

### Login
- Method & Path: POST `/api/v1/auth/login`
- Headers: `Content-Type: application/json`
- Request Body schema:
```json
{
  "email": "user@example.com",
  "password": "string"
}
```
- Success 200 Example:
```json
{
  "token": "eyJhbGciOi...",
  "user": {
    "id": 7,
    "email": "user@example.com",
    "role": "user",
    "status": "active"
  }
}
```
- Errors:
  - 401 `{"detail":"unauthorized"}` (email not found or password mismatch).
  - 403 `{"detail":"forbidden"}` (status `suspended` or `deleted`).

### Refresh Token
- Method & Path: POST `/api/v1/auth/refresh`
- Headers:
  - `Authorization: Bearer <token>`
  - `Accept: application/json`
- Request Body: none
- Success 200 Example:
```json
{
  "token": "eyJhbGciOi...new...",
  "user": {
    "id": 7,
    "email": "user@example.com",
    "role": "user",
    "status": "active"
  }
}
```
- Errors:
  - 401 `{"detail":"missing_token"}` (header missing/malformed).
  - 401 `{"detail":"invalid_token"}` (decode failure/expired).
  - 403 `{"detail":"forbidden"}` (user revoked).
  - 404 `{"detail":"user_not_found"}`.

## Users

Base path: `/api/v1/users`. Status values: `invited`, `active`, `suspended`, `deleted`. Email is unique. Local auth mode allows password capture on create/change; SSO mode ignores password fields.

### Create User
- Method & Path: POST `/api/v1/users/`
- Headers: `Content-Type: application/json`
- Body schema:
```json
{
  "email": "user@example.com",
  "name": "User Name",
  "role": "user",
  "password": "optional",
  "status": "optional"
}
```
- Success 200 Example:
```json
{
  "id": 1,
  "email": "user@example.com",
  "name": "User Name",
  "role": "user",
  "status": "active",
  "created_at": "2025-10-21T15:34:00Z",
  "updated_at": "2025-10-21T15:34:00Z"
}
```
- Errors:
  - 409 `{"detail":"email_already_exists"}`
  - 422 FastAPI validation array (invalid email, missing fields)

### List Users
- Method & Path: GET `/api/v1/users/`
- Query params:
  - `email` (optional substring filter)
  - `status` (optional exact status)
  - `limit` (int, default 20, max 100)
  - `offset` (int, default 0)
- Success 200 Example:
```json
[
  {
    "id": 1,
    "email": "user@example.com",
    "name": "User Name",
    "role": "user",
    "status": "active",
    "created_at": "2025-10-21T15:34:00Z",
    "updated_at": "2025-10-21T15:34:00Z"
  }
]
```

### Get User by ID
- Method & Path: GET `/api/v1/users/{user_id}`
- Path params: `user_id` (int, required)
- Success 200: `UserOut` JSON as above.
- Errors: 404 `{"detail":"user_not_found"}`

### Update User (partial)
- Method & Path: PATCH `/api/v1/users/{user_id}`
- Headers: `Content-Type: application/json`
- Body schema (any subset of updatable fields):
```json
{
  "name": "New Name",
  "role": "admin",
  "status": "suspended"
}
```
- Success 200: updated `UserOut`
- Errors: 404 `{"detail":"user_not_found"}`

### Delete or Suspend User
- Method & Path: DELETE `/api/v1/users/{user_id}`
- Query params: `hard` (bool, default `false`). `false` => suspend; `true` => hard delete when storage allows.
- Success 200 Example:
```json
{ "ok": true }
```
- Errors: 404 `{"detail":"user_not_found"}`

### Change Password
- Method & Path: POST `/api/v1/users/{user_id}/password`
- Headers: `Content-Type: application/json`
- Body schema:
```json
{
  "current_password": "optional-when-admin",
  "new_password": "Strong#2025"
}
```
- Success 200:
```json
{ "ok": true }
```
- Errors:
  - 400 `{"detail":"local_auth_disabled"}`
  - 401 `{"detail":"invalid_current_password"}` (when enforced)
  - 404 `{"detail":"user_not_found"}`

## Feedback

Base path: `/api/v1/feedback`. Comments are sanitized on write using configured placeholders (`[EMAIL]`, `[PHONE]`, `[CARD]`, ...). `category` is free-form (examples: `bug`, `idea`, `like`). `metadata` is optional JSON stored as-is.

### Create Feedback
- Method & Path: POST `/api/v1/feedback/`
- Headers: `Content-Type: application/json`
- Body schema:
```json
{
  "user_id": 1,
  "session_id": "abc123",
  "rating": 5,
  "category": "like",
  "comment": "Found card 4111 1111 1111 1111",
  "metadata": {"app_version": "1.2.3"}
}
```
- Success 200 Example:
```json
{
  "id": 42,
  "user_id": 1,
  "session_id": "abc123",
  "rating": 5,
  "category": "like",
  "comment": "Found [CARD]",
  "metadata": {"app_version": "1.2.3"},
  "created_at": "2025-10-21T15:35:00Z"
}
```
- Errors: standard FastAPI validation (422) for bad payloads.

### List Feedback
- Method & Path: GET `/api/v1/feedback/`
- Query params:
  - `user_id` (int, optional)
  - `category` (string, optional)
  - `date_from` (ISO8601 string, optional)
  - `date_to` (ISO8601 string, optional)
  - `limit` (int, default 20, max 100)
  - `offset` (int, default 0)
- Success 200 Example:
```json
[
  {
    "id": 42,
    "user_id": 1,
    "session_id": "abc123",
    "rating": 5,
    "category": "like",
    "comment": "Found [CARD]",
    "metadata": {"app_version": "1.2.3"},
    "created_at": "2025-10-21T15:35:00Z"
  }
]
```

### Get Feedback by ID
- Method & Path: GET `/api/v1/feedback/{fb_id}`
- Path params: `fb_id` (int, required)
- Success 200: `FeedbackOut` JSON (as above)
- Errors: 404 `{"detail":"feedback_not_found"}`

## Documents & Embeddings

- Summary: Manage document ingestion pipeline (manual uploads, embedding jobs, SharePoint sync).
- Flow:
  1. Upload files to staging via `POST /api/v1/uploads`.
  2. Create an embedding job with `POST /api/v1/ingest/jobs` (accepts staged upload IDs).
  3. Poll job status with `GET /api/v1/ingest/jobs/{job_id}` until `status` is `succeeded` or `failed`.
  4. (Optional) Trigger SharePoint sync to register external files, which internally calls the same ingestion pipeline.

### Upload Document
- Method & Path: POST `/api/v1/uploads`
- Content type: `multipart/form-data`
- Form fields:
  - `file` (required file part)
  - `source` (optional string, default `manual-upload`)
  - `tags` (optional CSV or JSON list string)
  - `lang_hint` (`auto|es|en|pt`, default `auto`)
- Example (multipart):
```
POST /api/v1/uploads HTTP/1.1
Content-Type: multipart/form-data; boundary=---011000010111000001101001

-----011000010111000001101001
Content-Disposition: form-data; name="file"; filename="guide.pdf"
Content-Type: application/pdf

<binary pdf bytes>
-----011000010111000001101001
Content-Disposition: form-data; name="source"

manual-upload
-----011000010111000001101001
Content-Disposition: form-data; name="tags"

product,public
-----011000010111000001101001--
```
- Success 201 Example:
```json
{
  "upload_id": "upl-01habc2def3",
  "filename": "guide.pdf",
  "size_bytes": 24576,
  "content_type": "application/pdf",
  "source": "manual-upload",
  "tags": ["product", "public"],
  "lang_hint": "auto",
  "storage_path": "staging/2025/10/29/upl-01habc2def3/guide.pdf",
  "checksum_sha256": "f84e1c7c3bb2e...",
  "created_at": "2025-10-29T14:22:16Z"
}
```
- Errors:
  - 400 `{"detail":"No file provided"}` or `{"detail":"Uploaded file is empty"}`
  - 413 `{"detail":"Upload exceeds maximum size of <bytes> bytes"}`
  - 415 `{"detail":"Unsupported MIME type: <type>"}`
  - 500 `{"detail":"Upload failed"}`

### Get Upload Metadata
- Method & Path: GET `/api/v1/uploads/{upload_id}`
- Path params: `upload_id` (string)
- Success 200: same `UploadMeta` schema as upload response.
- Errors: 404 `{"detail":"Upload not found"}`

### Create Ingestion Job
- Method & Path: POST `/api/v1/ingest/jobs`
- Headers: `Content-Type: application/json`
- Request body schema:
```json
{
  "upload_ids": ["upl-01habc2def3", "upl-01hxyz98765"],
  "profile": "legacy_profile",
  "tags": ["product:demo"],
  "lang_hint": "auto",
  "priority": 10,
  "update_alias": false,
  "evaluate": false
}
```
- Note: `profile` must match a configured profile (`app.ingest_profiles` or `embeddings.profiles`) or one of the built-ins such as `legacy_profile`; otherwise the endpoint returns 422 `{"detail":"Unknown profile: <name>"}`.
- Success 202 Example:
```json
{
  "job_id": "emb-20251029-40af2c",
  "status": "queued",
  "profile": "legacy_profile",
  "created_at": "2025-10-29T14:25:00Z",
  "started_at": null,
  "finished_at": null,
  "current_phase": null,
  "inputs": {
    "uploads_count": 2,
    "tags": ["product:demo"],
    "lang_hint": "auto",
    "priority": 10,
    "update_alias": false,
    "evaluate": false
  },
  "progress": {
    "files_total": 2,
    "files_processed": 0,
    "chunks_total": 0,
    "chunks_indexed": 0,
    "dedupe_skipped": 0
  },
  "summary": null,
  "metrics": null,
  "logs_tail": [],
  "error": null
}
```
- Errors:
  - 400 `{"detail":"upload_ids must be unique"}` or `{"detail":"upload_ids must not be empty"}`
  - 404 `{"detail":"Upload not found: <id list>"}`
  - 422 `{"detail":"Unknown profile: <name>"}`
  - 409 `{"detail":"Conflicting job active job already references one of the uploads"}`
  - 500 `{"detail":"Unable to create job"}`

### Ingestion Job Status
- Method & Path: GET `/api/v1/ingest/jobs/{job_id}`
- Path params: `job_id` (string)
- Success 200 Example:
```json
{
  "job_id": "emb-20251029-40af2c",
  "status": "running",
  "profile": "legacy_profile",
  "created_at": "2025-10-29T14:25:00Z",
  "started_at": "2025-10-29T14:25:05Z",
  "finished_at": null,
  "current_phase": "embedding",
  "inputs": {
    "uploads_count": 2,
    "tags": ["product:demo"],
    "lang_hint": "auto",
    "priority": 10,
    "update_alias": false,
    "evaluate": false
  },
  "progress": {
    "files_total": 2,
    "files_processed": 1,
    "chunks_total": 180,
    "chunks_indexed": 120,
    "dedupe_skipped": 3
  },
  "summary": null,
  "metrics": null,
  "logs_tail": [
    "[INFO] files=1/2 chunks=120 inserted=120 skipped=0"
  ],
  "error": null
}
```
- Errors: 404 `{"detail":"Job not found"}`

### SharePoint Sync (optional)
- Method & Path: POST `/api/v1/sharepoint/sync/run`
- Headers: `Content-Type: application/json`
- Request body schema:
```json
{
  "mode": "update",
  "folder_name": "rolling"
}
```
- Success 202 Example:
```json
{
  "sync_id": "sp-sync-20251029-01",
  "mode": "update",
  "site_key": "default-site",
  "target_directory": "rolling",
  "uploads_registered": 5,
  "job_id": "emb-20251029-652ab4",
  "status": "succeeded",
  "started_at": "2025-10-29T14:40:00Z",
  "finished_at": "2025-10-29T14:42:10Z",
  "errors": null
}
```
- Errors:
  - 502 `{"detail":"SharePoint sync failed"}`
  - 502 `{"detail":"SharePoint sync service error 500"}`

### SharePoint History (optional)
- Method & Path: GET `/api/v1/sharepoint/history`
- Success 200 Example:
```json
{
  "data": {
    "recent_runs": [
      {"sync_id": "sp-sync-20251028-ff12ab", "status": "succeeded", "uploads_registered": 3}
    ]
  }
}
```
- Errors: 502 `{"detail":"Invalid response from SharePoint sync service"}`

## Debug (dev only)

- Summary: Database connection inspector, enabled only when `settings.app.debug` or `ENV=dev|development|local`.
- Method & Path: GET `/api/_debug/db`
- Success 200 Example:
```json
{
  "effective_url": "oracle+cx_ora://user:***@host/service",
  "source": "env",
  "service_name": "XE",
  "current_schema": "APP_USER"
}
```
- Notes: Do not expose in production; router mounts conditionally.
