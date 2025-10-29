# Configuration

Overview

- Configuration is sourced from frontend/streamlit/.env via app_config/env.py (dotenv). The loader exposes get_config(), used by app/main.py.

Sources

- .env (dotenv) at frontend/streamlit/.env
- Code defaults in app_config/env.py
- Data/asset directories under frontend/streamlit/

Environment Variables

- BACKEND_API_BASE: Base URL of backend API (default http://localhost:5000)
- FRONTEND_BASE_URL: Override for backend base URL when different from BACKEND_API_BASE (used by Admin uploads)
- FRONTEND_PORT: Port for Streamlit server (default 8501)
- AUTH_STORAGE_DIR: Directory for users (default ./data/credenciales)
- FEEDBACK_STORAGE_DIR: Directory for feedback (default ./data/feedback)
- ASSISTANT_TITLE: App title (default from env.py)
- SESSION_TTL_MIN: Cookie token TTL in minutes (default 120)
- SESSION_COOKIE_NAME: Cookie name (default assistant_session)
- SESSION_SECRET: HMAC secret for cookies (required to enable remember me)
- REQUEST_TIMEOUT: HTTP timeout for backend calls in seconds (default 60)
- LOG_LEVEL: Log level (default INFO)
- DEFAULT_PROFILE: Embedding profile id used in Admin view (e.g., legacy_profile)
- UPLOAD_CONCURRENCY: Max simultaneous uploads in Admin view (recommend 3-5)
- ALLOWED_MIME_HINT: Optional CSV of MIME types to display in Admin copy (enforcement remains backend)
- AUTH_ENABLED: Toggle auth headers/JWT handling for admin calls (true|false)
- AUTH_TOKEN_SCOPE_UPLOAD: Scope/claim required for `POST /api/v1/uploads`
- AUTH_TOKEN_SCOPE_INGEST: Scope/claim required for `POST /api/v1/ingest/jobs`

Paths & Expectations

- Users file lives at {AUTH_STORAGE_DIR}/usuarios.json
- Feedback JSON/CSV at {FEEDBACK_STORAGE_DIR}/fback.json, fback_icon.json, fback.csv
- Artifacts can be written under artifacts-frontend/

Override Strategy

- All settings are read once at startup; update .env and rerun the app.
- MIME type and size checks are ultimately enforced by the backend; the frontend only surfaces hints/warnings based on ALLOWED_MIME_HINT and MAX_UPLOAD_MB.

Quick Links

- Index: ./INDEX.md
- Setup: ./SETUP_AND_RUN.md

## Auth/Feedback Modes

Environment variables

```
AUTH_MODE=local        # local | db (default: local)
FEEDBACK_MODE=local    # local | db (default: local)
DUAL_WRITE_FEEDBACK=false
```

Behavior

- AUTH_MODE=local: login validates hashed password against local users JSON. Password changes are handled locally.
- AUTH_MODE=db: login uses `/api/v1/auth/login` (email + password required). Password changes are delegated to backend/provider (if endpoint exists).
- FEEDBACK_MODE=local: thumbs feedback is appended to local JSON/CSV using the icon shape (like|dislike).
- FEEDBACK_MODE=db: thumbs feedback is posted to `/api/v1/feedback/` using the homologated payload (category, rating, comment, metadata).
- DUAL_WRITE_FEEDBACK=true: best‑effort dual‑write (local + db). If one leg fails, the UI shows a non‑blocking warning.

Payloads

- Local:
  ```json
  {
    "username": "user@example.com",
    "question": "…",
    "answer": "…",
    "icon": "like | dislike",
    "feedback": "",
    "ts": "2025-10-06T22:35:24Z"
  }
  ```
- DB:
  ```json
  {
    "category": "like | dislike",
    "rating": 5 | 1,
    "comment": "",
    "metadata": {
      "username": "user@example.com",
      "question": "…",
      "answer": "…",
      "ts": "2025-10-06T22:35:24Z"
    }
  }
  ```

Examples

Local‑only:
```
AUTH_MODE=local
FEEDBACK_MODE=local
DUAL_WRITE_FEEDBACK=false
```

DB‑only:
```
AUTH_MODE=db
FEEDBACK_MODE=db
DUAL_WRITE_FEEDBACK=false
```

Transition (dual‑write):
```
AUTH_MODE=db
FEEDBACK_MODE=db
DUAL_WRITE_FEEDBACK=true
```

### DB auth

When `AUTH_MODE=db`, the frontend requires valid credentials via the backend endpoint `/api/v1/auth/login`. The backend responds with a token and user info (email, role, status). Suspended users are rejected. The previous email‑only development mode is no longer used.
