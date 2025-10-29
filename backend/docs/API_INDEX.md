# Backend API - Index

- See `API_REFERENCE.md` for endpoint details and examples.
- Client assets: Postman collection (`postman/Backend_API.postman_collection.json`) and VS Code REST file (`http/Backend_API.http`).

Contents

- API Reference: API_REFERENCE.md
- Auth Config: API_AUTH.md
- Runtime Config: API_CONFIG.md
- Errors: API_ERRORS.md
- Client assets:
  - Postman: postman/Backend_API.postman_collection.json
  - VS Code REST: http/Backend_API.http

Services Covered

- Health: GET `/healthz`
- Chat: POST `/chat`
- Auth: `/api/v1/auth` (login, refresh)
- Users: `/api/v1/users` (CRUD + password)
- Feedback: `/api/v1/feedback` (create/list/get)
- Documents & Embeddings: `/api/v1/uploads`, `/api/v1/ingest/jobs`
- SharePoint Sync: `/api/v1/sharepoint/*`
- Debug (dev only): `/api/_debug/db`
