# API Reference

This reference documents the active backend endpoints and expected payloads. Defaults assume no auth enforcement. Pagination uses `limit` (default 20, max 100) and `offset` (default 0).

## Health

- Summary: Liveness and provider status.
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
- Errors: 500 on internal errors.

## Chat

- Summary: Ask a question; backend selects retrieval vs LLM modes.
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
- Success 200 Response (schema):
```json
{
  "question": "...",
  "answer": "...",
  "answer2": null,
  "answer3": null,
  "retrieved_chunks_metadata": [ {"source": "file.pdf", "page": 1} ],
  "mode": "rag",
  "sources_used": "file.pdf",
  "used_chunks": [
    {"chunk_id": "doc-1#0", "source": "file.pdf", "score": 0.78, "snippet": "..."}
  ],
  "decision_explain": {
    "max_similarity": 0.78,
    "threshold_low": 0.1,
    "threshold_high": 0.3,
    "top_k": 12,
    "effective_query": "...",
    "short_query_active": false,
    "used_llm": "primary",
    "mode": "rag",
    "score_mode": "normalized",
    "distance": "dot_product"
  }
}
```
- Common Errors:
  - 422 validation error (missing `question`).
  - 500 internal error.

## Users

Base path: `/api/v1/users`

Status values: `invited` | `active` | `suspended`. `email` must be unique. In local auth mode, `password` is accepted on create and is never returned.

### Create User
- Method & Path: POST `/api/v1/users/`
- Headers: `Content-Type: application/json`
- Body schema:
```json
{
  "email": "user@example.com",
  "name": "User Name",
  "role": "user",
  "password": "secret-optional"
}
```
- Success 200 Example:
```json
{
  "id": 1,
  "email": "user@example.com",
  "name": "User Name",
  "role": "user",
  "status": "invited",
  "created_at": "2025-10-21T15:34:00+00:00",
  "updated_at": "2025-10-21T15:34:00+00:00"
}
```
- Errors:
  - 409 `{"
detail":"email_already_exists"}`
  - 422 validation error

### List Users
- Method & Path: GET `/api/v1/users/`
- Query params:
  - `email` (optional, substring match)
  - `status` (optional)
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
    "created_at": "2025-10-21T15:34:00+00:00",
    "updated_at": "2025-10-21T15:34:00+00:00"
  }
]
```

### Get User by ID
- Method & Path: GET `/api/v1/users/{id}`
- Path params: `id` (int, required)
- Success 200: `UserOut` JSON (as above)
- Errors: 404 `{"
detail":"user_not_found"}`

### Update User (partial)
- Method & Path: PATCH `/api/v1/users/{id}`
- Body schema (any subset):
```json
{
  "name": "New Name",
  "role": "admin",
  "status": "suspended"
}
```
- Success 200: updated `UserOut`
- Errors: 404 `{"
detail":"user_not_found"}`

### Delete or Suspend User
- Method & Path: DELETE `/api/v1/users/{id}`
- Query params: `hard` (bool, default false). If `false`, performs soft delete by setting `status="suspended"`.
- Success 200 Example:
```json
{ "ok": true }
```
- Errors: 404 `{"
detail":"user_not_found"}`

### Change Password
- Method & Path: POST `/api/v1/users/{id}/password`
- Headers: `Content-Type: application/json`
- Body schema:
```json
{
  "current_password": "optional-when-admin",
  "new_password": "Strong#2025"
}
```
- Behavior:
  - Only in `auth.mode=local`. Otherwise 400 `local_auth_disabled`.
  - In this build, treated as admin (no auth). Structure allows enforcing current_password later.
  - Server hashes the new password and updates `password_updated_at`.
- Success 200:
```json
{ "ok": true }
```
- Errors:
  - 400 `{ "detail": "local_auth_disabled" }`
  - 401 `{ "detail": "invalid_current_password" }`
  - 404 `{ "detail": "user_not_found" }`

## Feedback

Base path: `/api/v1/feedback`

Comments are sanitized on write; placeholders like `[CARD]` may replace detected PII depending on sanitizer config. `category` is free-form (examples: `bug|idea|like`). `metadata` is an optional JSON object.

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
  "created_at": "2025-10-21T15:35:00+00:00"
}
```
- Notes: In DB, metadata is stored as JSON; in JSON mode, stored inline.

### List Feedback
- Method & Path: GET `/api/v1/feedback/`
- Query params:
  - `user_id` (int, optional)
  - `category` (string, optional)
  - `date_from` (ISO8601, optional)
  - `date_to` (ISO8601, optional)
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
    "created_at": "2025-10-21T15:35:00+00:00"
  }
]
```

### Get Feedback by ID
- Method & Path: GET `/api/v1/feedback/{id}`
- Path params: `id` (int, required)
- Success 200: `FeedbackOut` JSON (as above)
- Errors: 404 `{"
detail":"feedback_not_found"}`

### Delete Feedback (not implemented)
- Method & Path: DELETE `/api/v1/feedback/{id}`
- Note: Not available in current build.

Behavior Notes

- Storage backend behavior is controlled by `storage.*` flags; reads follow the configured mode. If `dual_write=true`, writes go to both DB and JSON.
- Feedback comments are sanitized using the configured sanitizer mode (shadow/on).
- Pagination defaults: `limit=20`, `offset=0` with `limit<=100`.
