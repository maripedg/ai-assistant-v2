# API Auth
Last updated: 2025-11-07

## Overview
- Authentication lives under `/api/v1/auth/*`.
- Tokens are JWTs signed with `HS256` using `JWT_SECRET` (falls back to `SESSION_SECRET` if unset).
- By default the API does **not** enforce auth; deployments that set `AUTH_ENABLED=true` or run the Streamlit app in DB mode must forward `Authorization: Bearer <token>` to every `/api/v1/*` route.
- Roles are advisory (`user`, `admin`); route protection currently happens at the UI/gateway layer.

## Login
`POST /api/v1/auth/login`
- Body: `{ "email": "user@example.com", "password": "secret" }`
- Response:
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
  - `401 {"detail":"unauthorized"}` – email not found or password mismatch.
  - `403 {"detail":"forbidden"}` – user status `suspended` or `deleted`.

## Refresh
`POST /api/v1/auth/refresh`
- Headers: `Authorization: Bearer <token>`
- Response: Same shape as login with a fresh token/expiry.
- Errors:
  - `401 {"detail":"missing_token"|"invalid_token"}` – absent/malformed/expired token.
  - `403 {"detail":"forbidden"}` – status changed to suspended/deleted.
  - `404 {"detail":"user_not_found"}` – user deleted between login and refresh.

## JWT Payload
```json
{
  "sub": "7",
  "email": "user@example.com",
  "role": "admin",
  "status": "active",
  "iat": 1730965200,
  "exp": 1731051600
}
```
The Streamlit frontend stores `sub` in `st.session_state["user_id"]` so feedback submissions always include a server-trusted `user_id`. If `user_id` is missing (e.g., local auth), the UI can fall back to `FEEDBACK_DEFAULT_USER_ID`.

## Deployment Notes
- **AUTH_MODE (`config/app.yaml`)**:
  - `local` – passwords are hashed with `auth.password_algo` (default `bcrypt`) when creating users. `/api/v1/auth/login` validates against DB hashes. `/api/v1/users/{id}/password` works only in this mode.
  - `sso` / `hybrid` – reserved for future integrations.
- **AUTH_ENABLED (frontend env)** – When the frontend sets this flag, it always forwards `Authorization: Bearer ...` on admin uploads, ingest jobs, users, and feedback APIs. Gateways should reject requests missing the header to keep the behaviour consistent.
- **Usage logging** – If `USAGE_LOG_ENABLED=true`, successful logins insert rows into `AUTH_LOGINS`. Use this to audit auth activity alongside `CHAT_SESSIONS` and `CHAT_INTERACTIONS`.

## Testing
```bash
export BASE=http://localhost:8000
curl -s -X POST $BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"secret"}' | jq .

curl -s -X POST $BASE/api/v1/auth/refresh \
  -H "Authorization: Bearer $TOKEN" | jq .
```
Replace `$TOKEN` with the value returned from login.
