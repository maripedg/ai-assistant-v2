# Services
Last updated: 2025-11-07

The canonical modules live under `app/services/`. The legacy `frontend/streamlit/services/__init__.py` simply re-exports these modules for backwards compatibility—new imports should always use `from app.services import ...`.

## `app.services.api_client`
- Central HTTP helper that wraps `requests`. Resolves the base URL from `BACKEND_API_BASE` (or `FRONTEND_BASE_URL` for admin uploads) and injects `_auth_headers()` on every request.
- `_auth_headers()` calls `app.services.auth_session.get_auth_headers()`. When the user logged in via `/api/v1/auth/login`, a JWT is stored in session/cookies and every `/api/v1/*` request includes `Authorization: Bearer ...`. This is critical when `AUTH_ENABLED=true`.
- Key helpers: `health_check()`, `chat()`, `users_*()`, `upload_file()`, `create_ingest_job()`, `feedback_create()`/`feedback_list()`, and `send_feedback()` (used by the chat thumbs).
- `chat()` normalises responses into `{answer, answer2, answer3, retrieved_chunks_metadata, mode, used_chunks, decision_explain}` so the UI always has consistent structures, regardless of backend shape.

## `app.services.auth_session`
- Handles local login, JWT-based login, cookie issuance (via `extra_streamlit_components`), and session restoration.
- Stores `st.session_state["user_id"]` when the backend login response contains `user.id`. Feedback payloads read this value; if it’s missing they fall back to `FEEDBACK_DEFAULT_USER_ID`.
- Exposes `get_auth_headers()` which returns `{ "Authorization": "Bearer <token>" }` or `{}`.

## `app.services.storage`
- Local persistence for users and feedback when running in `AUTH_MODE=local` / `FEEDBACK_MODE=local`.
- Provides dual-write helpers so you can mirror to both local JSON and backend APIs when `DUAL_WRITE_FEEDBACK=true`.

## `app.services.feedback_api`
- Wraps `api_client.feedback_list()` to coerce totals and rating filters.
- `build_feedback_payload()` constructs the backend payload with `question`, `answer_preview`, `mode`, `message_id`, and optional `note` fields inside `metadata`. Chat thumbs reuse this helper before calling `api_client.send_feedback()`.

## Deprecated Shim
`frontend/streamlit/services/__init__.py` emits a warning and re-exports the new modules. Update imports gradually to avoid the shim entirely.

## Tips
- Keep future services under `app/services/*.py` to stay consistent.
- When debugging HTTP failures, temporarily set `DEBUG_HTTP=1` (logged inside `api_client`).
- Don’t bypass `_auth_headers()`; manual `requests` calls in views risk missing the Authorization header and will fail once `AUTH_ENABLED` is true.
