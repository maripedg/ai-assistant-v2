# Backend API Reference
Last updated: 2025-11-07

Unless stated otherwise, all endpoints return/accept JSON and live under the same FastAPI process defined in [backend/app/main.py](../app/main.py). When `AUTH_ENABLED=true` (typical for DB-auth deployments), every `/api/v1/*` request must supply `Authorization: Bearer <JWT>`. Health and chat remain public but can sit behind a gateway if needed.

Pagination parameters follow `limit` (default 20, max 100) and `offset` (default 0). Error payloads follow FastAPI conventions—see [API_ERRORS.md](./API_ERRORS.md) for codes and examples.

## Health
### GET `/healthz`
- **Request**: no body.
- **Response**:
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
- **Notes**: Dependency failures downgrade to `services.<name> = "down (<reason>)"` while HTTP status stays `200`.

## Chat
### POST `/chat`
- **Headers**: `Content-Type: application/json`. Response includes `X-Answer-Mode` mirroring the body’s `mode`.
- **Body**:
```json
{ "question": "List hybrid decision gates." }
```
- **Response**:
```json
{
  "question": "List hybrid decision gates.",
  "answer": "Hybrid answers require similarity >= 0.25...",
  "answer2": null,
  "answer3": null,
  "retrieved_chunks_metadata": [
    {
      "chunk_id": "doc-42#3",
      "source": "retrieval_playbook.md",
      "similarity": 0.74,
      "text": "The rag/hybrid gate requires..."
    }
  ],
  "used_chunks": [
    {
      "chunk_id": "doc-42#3",
      "source": "retrieval_playbook.md",
      "score": 0.74,
      "snippet": "The rag/hybrid gate requires..."
    }
  ],
  "mode": "rag",
  "sources_used": "all",
  "decision_explain": {
    "score_mode": "normalized",
    "distance": "dot_product",
    "max_similarity": 0.74,
    "threshold_low": 0.25,
    "threshold_high": 0.55,
    "short_query_active": false,
    "top_k": 12,
    "used_llm": "primary"
  }
}
```
- **Notes**: `mode` may be `rag`, `hybrid`, or `fallback`. Fallback responses still include diagnostics so the UI can explain why sources are hidden. Usage logging (when enabled) records the same decision in `CHAT_INTERACTIONS.RESP_MODE`.

## Auth
### POST `/api/v1/auth/login`
- **Headers**: `Content-Type: application/json`.
- **Body**:
```json
{ "email": "admin@example.com", "password": "secret" }
```
- **Response**:
```json
{
  "token": "eyJhbGciOi...",
  "user": {
    "id": 7,
    "email": "admin@example.com",
    "role": "admin",
    "status": "active"
  }
}
```
- **Errors**: `401 {"detail":"unauthorized"}` (bad creds), `403 {"detail":"forbidden"}` (user suspended/deleted).

### POST `/api/v1/auth/refresh`
- **Headers**: `Authorization: Bearer <token>`.
- **Response**: same shape as login with a refreshed token.
- **Errors**: `401 {"detail":"missing_token"|"invalid_token"}`, `403 {"detail":"forbidden"}`, `404 {"detail":"user_not_found"}`.

JWT claims include `sub` (user id), `email`, `role`, and `status`. Frontends store `sub` in session state so feedback submissions carry the correct `user_id`.

## Users
Base path: `/api/v1/users`.

| Method & Path | Notes |
| --- | --- |
| `POST /` | Create a user. Body requires `email`; optional `name`, `role`, `password`, `status`. Emails must be unique. Returns `UserOut`. |
| `GET /` | Query params: `email`, `status`, `limit`, `offset`. Returns list of `UserOut`. |
| `GET /{user_id}` | Fetch a user or `404 {"detail":"user_not_found"}`. |
| `PATCH /{user_id}` | Partial update (role, name, status). |
| `DELETE /{user_id}` | Soft delete by default (`hard=false`). Returns `{"ok": true}` or `404`. |
| `POST /{user_id}/password` | Change password. Available only when `auth.mode=local`; otherwise returns `400 {"detail":"local_auth_disabled"}`. |

`UserOut` fields: `id`, `email`, `name`, `role`, `status`, `created_at`, `updated_at`. Password hashes are never returned.

## Feedback
Base path: `/api/v1/feedback`. Comments are sanitized using [backend/common/sanitizer.py](../common/sanitizer.py); expect placeholders (`[EMAIL]`, `[PHONE]`) when `SANITIZE_ENABLED=on`.

### POST `/`
```json
{
  "user_id": 7,
  "session_id": "20d9c5e9",
  "rating": 1,
  "category": "dislike",
  "comment": "Answer referenced an outdated KB.",
  "metadata": {
    "question": "When did we rotate the alias?",
    "answer_preview": "The alias rotated on...",
    "mode": "hybrid",
    "message_id": "msg-123",
    "client": "streamlit",
    "ui_version": "chat-v2"
  }
}
```
Response shape (`FeedbackOut`):
```json
{
  "id": 42,
  "user_id": 7,
  "session_id": "20d9c5e9",
  "rating": 1,
  "category": "dislike",
  "comment": "Answer referenced an outdated KB.",
  "metadata_json": "{\"question\":\"...\",\"mode\":\"hybrid\", ...}",
  "created_at": "2025-11-07T14:33:18Z"
}
```

### GET `/`
Query params: `user_id`, `category`, `date_from`, `date_to`, `limit`, `offset`. Returns a list of `FeedbackOut`. The frontend derives question/answer previews from `metadata_json`.

### GET `/{feedback_id}`
Returns a single `FeedbackOut` or `404 {"detail":"feedback_not_found"}`.

## Documents & Embeddings

### POST `/api/v1/uploads`
- **Headers**: `Content-Type: multipart/form-data`.
- **Fields**:
  - `file` (required) – the document to stage.
  - `source`, `tags`, `lang_hint` (optional; tags accept CSV or JSON list).
- **Response**:
```json
{
  "upload_id": "upl-4f0d",
  "filename": "kb.pdf",
  "size_bytes": 133742,
  "content_type": "application/pdf",
  "source": "manual-upload",
  "tags": ["product:demo"],
  "lang_hint": "es",
  "storage_path": "stage/kb.pdf",
  "checksum_sha256": "6f6c..."
}
```
- **Errors**: `400` (empty upload), `413` (size > `MAX_UPLOAD_MB`), `415` (disallowed MIME), `500` (storage failure).

### GET `/api/v1/uploads/{upload_id}`
Returns the staged metadata or `404 {"detail":"Upload not found"}`.

### POST `/api/v1/ingest/jobs`
- **Body**:
```json
{
  "upload_ids": ["upl-4f0d", "upl-1ab2"],
  "profile": "legacy_profile",
  "tags": ["product:demo"],
  "lang_hint": "auto",
  "priority": 10,
  "update_alias": true,
  "evaluate": false
}
```
- **Response** (`202 Accepted`):
```json
{
  "job_id": "emb-20251107-5e3b",
  "status": "queued",
  "profile": "legacy_profile",
  "created_at": "2025-11-07T14:35:02Z",
  "inputs": {
    "uploads_count": 2,
    "tags": ["product:demo"],
    "lang_hint": "auto",
    "priority": 10,
    "update_alias": true,
    "evaluate": false
  }
}
```
- **Errors**: `404` (missing upload), `409` (conflicting job), `422` (unknown profile or blank upload_ids), `500` (unexpected failure).

### GET `/api/v1/ingest/jobs/{job_id}`
Returns the job status with optional `progress`, `summary`, `metrics`, and `logs_tail`. `404 {"detail":"Job not found"}` if unknown.

## SharePoint Sync (optional)
- **POST `/api/v1/sharepoint/sync/run`**: Trigger a sync. Body accepts `{ "mode": "update", "folder_name": "rolling" }`. Returns `SyncRunResponse` with `sync_id`, `uploads_registered`, `job_id`, timestamps, and errors (if any). Failures bubble as `502 {"detail":"SharePoint sync failed"}`.
- **GET `/api/v1/sharepoint/history`**: Proxy the SharePoint service’s history summary. Errors propagate as `502`.

## Debug (dev only)
### GET `/api/_debug/db`
Enabled when `settings.app.debug=true` or `ENV` is `dev|development|local`. Returns:
```json
{
  "effective_url": "oracle+cx_oracle://user:***@host/service",
  "source": "env",
  "service_name": "FREEPDB1",
  "current_schema": "APP_USER"
}
```
Never expose this route in production.

## Assets
- REST Client file: [http/Backend_API.http](./http/Backend_API.http).
- Postman collection: [postman/Backend_API.postman_collection.json](./postman/Backend_API.postman_collection.json).

Use these assets for manual testing after updating env variables (`@base_url`, `@auth_token`, `@upload_id`, `@job_id`).
