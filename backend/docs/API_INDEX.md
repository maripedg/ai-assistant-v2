# Backend API Index
Last updated: 2025-11-07

This index links the detailed references plus client assets under `backend/docs/http` and `backend/docs/postman`.

## Documents
- [API_REFERENCE.md](./API_REFERENCE.md) – Full request/response docs for every endpoint.
- [API_AUTH.md](./API_AUTH.md) – JWT format, login/refresh flows, frontend header requirements.
- [API_CONFIG.md](./API_CONFIG.md) – Runtime flags (`features.*`, `storage.*`, `USAGE_LOG_ENABLED`, upload limits).
- [API_ERRORS.md](./API_ERRORS.md) – Canonical status codes and message examples.
- HTTP examples – [`http/Backend_API.http`](./http/Backend_API.http).
- Postman collection – [`postman/Backend_API.postman_collection.json`](./postman/Backend_API.postman_collection.json).

## Endpoint Catalog
| Path | Method(s) | Summary |
| --- | --- | --- |
| `/healthz` | GET | Readiness of embeddings + primary/fallback LLMs. |
| `/chat` | POST | Question ➜ rag/hybrid/fallback answer with `X-Answer-Mode`. |
| `/api/v1/auth/login` | POST | Email/password login; returns JWT + user profile. |
| `/api/v1/auth/refresh` | POST | Validate existing bearer token and issue a fresh JWT. |
| `/api/v1/users/` | GET/POST | List/create users (email unique, status gating). |
| `/api/v1/users/{id}` | GET/PATCH/DELETE | Profile operations plus soft/hard delete. |
| `/api/v1/users/{id}/password` | POST | Change password (local mode only). |
| `/api/v1/feedback/` | GET/POST | Create/list feedback with sanitized comments and metadata JSON. |
| `/api/v1/feedback/{id}` | GET | Fetch single feedback row. |
| `/api/v1/uploads` | POST | Stage a file for ingestion (size/MIME enforced). |
| `/api/v1/uploads/{upload_id}` | GET | Inspect staged upload metadata. |
| `/api/v1/ingest/jobs` | POST/GET | Create a job from staged uploads; poll job status. |
| `/api/v1/sharepoint/sync/run` | POST | Trigger a SharePoint/manual sync run. |
| `/api/v1/sharepoint/history` | GET | Proxy SharePoint sync history. |
| `/api/_debug/db` | GET | (Dev only) Inspect effective Oracle connection info. |

> Reminder: All `/api/v1/*` routes expect `Authorization: Bearer <token>` when `AUTH_ENABLED=true` or when frontends operate in DB auth mode.
