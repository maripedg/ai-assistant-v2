# Configuration
Last updated: 2025-11-07

`app_config/env.py` loads `.env` from `frontend/streamlit/.env` (dotenv) and exposes `get_config()`. Update the `.env` file and restart Streamlit whenever you change a value—config is cached at startup.

## Core Variables
| Key | Default | Notes |
| --- | --- | --- |
| `BACKEND_API_BASE` | `http://localhost:5000` | Base URL for `/chat` and most `/api/v1/*` calls. |
| `FRONTEND_BASE_URL` | — | Optional override for admin uploads; when set, `app.services.api_client` prefers this value so browser dev proxies can hit different origins. |
| `FRONTEND_PORT` | `8501` | Streamlit server port. |
| `ASSISTANT_TITLE` | `RODOD / DBE Assistant` | Displayed in the sidebar. |
| `LOG_LEVEL` | `INFO` | Passed to `logging`. |
| `REQUEST_TIMEOUT` | `60` | Seconds for backend HTTP calls (uploads, chat, feedback). |

## Auth & Feedback Modes
| Key | Description |
| --- | --- |
| `AUTH_MODE` | `local` (file-based) or `db` (calls `/api/v1/auth/login`). DB mode stores the backend-provided `user.id` (JWT `sub`) in `st.session_state["user_id"]`. |
| `FEEDBACK_MODE` | `local` or `db`. DB mode posts to `/api/v1/feedback/`. |
| `DUAL_WRITE_FEEDBACK` | When true, writes go to both local storage and backend; warnings surface if either side fails. |
| `AUTH_ENABLED` | Toggle for strict header enforcement. When true, the UI refuses to call admin APIs unless a JWT is present. All `/api/v1/*` requests (uploads, jobs, users, feedback) include `Authorization: Bearer ...`. |
| `FEEDBACK_DEFAULT_USER_ID` | Optional fallback user_id when JWT-based login is unavailable (e.g., local auth). Used by `app/views/chat` before sending thumbs feedback. |

Cookie/session settings:
| Key | Description |
| --- | --- |
| `SESSION_SECRET` | Enables “Remember me” cookies. Also used as JWT fallback secret server-side. |
| `SESSION_TTL_MIN` | Cookie TTL in minutes. |
| `SESSION_COOKIE_NAME` | Cookie name (`assistant_session` default). |

## Directories
| Key | Description |
| --- | --- |
| `AUTH_STORAGE_DIR` | Location of `usuarios.json` for local auth. |
| `FEEDBACK_STORAGE_DIR` | Directory containing `fback.json`, `fback_icon.json`, `fback.csv`. |

## Admin Uploads & Jobs
| Key | Description |
| --- | --- |
| `DEFAULT_PROFILE` | Pre-selected embedding profile on the Documents & Embeddings page (must match backend config). |
| `UPLOAD_CONCURRENCY` | Max simultaneous uploads (UI queue). Recommended range: 3–5. |
| `ALLOWED_MIME_HINT` | Optional CSV shown to operators; backend enforcement relies on `ALLOW_MIME`. |
| `AUTH_TOKEN_SCOPE_UPLOAD`, `AUTH_TOKEN_SCOPE_INGEST` | Copy text shown when scope errors occur. Actual enforcement happens at the gateway/backend. |

## Debug Flags
- `DEBUG_CHAT_UI`, `DEBUG_CHAT_UI_STRICT`: Enable verbose logging, raw payload expanders, and telemetry in the chat view.
- `DEBUG_HTTP`, `DEBUG_FEEDBACK_UI`: Additional toggles for HTTP/request tracing and the Feedback History tab (shows `fb_` state keys when set).

## Behaviour Notes
- `app.services.api_client` always calls `_auth_headers()` before hitting the backend. The helper asks `app.services.auth_session` for a stored JWT and injects `Authorization: Bearer ...` when available. Set `AUTH_ENABLED=true` to ensure admin views refuse to run when the header is missing.
- Feedback History takes `fb_*` keys in `st.session_state`; clearing the page resets filters but preserves the `fb_admin_raw` toggle so admins can keep the raw JSON tab open.
- When `AUTH_MODE=db`, logging in via `/api/v1/auth/login` stores the backend `user.id`. Thumbs feedback derives `user_id` from this value; if it cannot be parsed, the payload omits `user_id` and the backend records it as `null`.
