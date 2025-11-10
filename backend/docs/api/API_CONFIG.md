# API Runtime Config
Last updated: 2025-11-07

This page summarises the flags that influence API behaviour, especially those touched by recent features (usage logging, upload limits, auth mode toggles).

## Feature Flags
| Key | Description |
| --- | --- |
| `features.users_api` | Enable `/api/v1/users/*`. Disable if users are managed elsewhere. |
| `features.feedback_api` | Enable `/api/v1/feedback/*`. |
| `AUTH_ENABLED` (frontend env) | When true, clients must forward `Authorization: Bearer ...` to every `/api/v1/*` route. Pair with gateway enforcement. |

## Storage & Uploads
| Key | Description |
| --- | --- |
| `storage.users.mode` / `storage.feedback.mode` | `db` or `json` backends. |
| `storage.dual_write` | Mirror writes to both backends (reads stay on `mode`). Useful for migrations. |
| `STAGING_DIR` | Upload staging path (default `/data/staging`). |
| `MAX_UPLOAD_MB` | Size ceiling enforced by `/api/v1/uploads` (`413` when exceeded). |
| `ALLOW_MIME` | CSV or JSON list of MIME types. `/api/v1/uploads` rejects others with `415`. |

## Auth
| Key | Description |
| --- | --- |
| `auth.mode` | `local`, `sso`, or `hybrid`. Local mode hashes passwords and allows `/users/{id}/password`. |
| `auth.password_algo` | Hashing function for new passwords (`bcrypt` default). |
| `auth.require_signup_approval` / `AUTH_REQUIRE_SIGNUP_APPROVAL` env | If truthy, new `/users` default to status `invited` until approved. |
| `JWT_SECRET`, `SESSION_SECRET`, `JWT_TTL_MIN` | Signing material and TTL for issued tokens. |

## Usage Logging (Oracle)
| Key | Description |
| --- | --- |
| `USAGE_LOG_ENABLED` | Enables inserts into `AUTH_LOGINS`, `CHAT_SESSIONS`, `CHAT_INTERACTIONS`. |
| `USAGE_LOG_SCHEMA` (optional) | Override schema for logging tables if different from `DB_USER`. |

> NOTE: Logging tables must exist with the expected columns (`RESP_MODE` on `CHAT_INTERACTIONS`). Creation/grants happen outside the repo.

## Sanitization
`SANITIZE_ENABLED`, `SANITIZE_PROFILE`, `SANITIZE_CONFIG_PATH`, `SANITIZE_PLACEHOLDER_MODE`, `SANITIZE_HASH_SALT`, `SANITIZE_AUDIT_ENABLED`. See [docs/backend/SANITIZATION.md](../../docs/backend/SANITIZATION.md).

## Misc
| Key | Description |
| --- | --- |
| `retrieval.*` | Thresholds, hybrid gates, and distances controlling rag/hybrid/fallback. |
| `embeddings.*` | Active profile, alias name, dedupe, batching. |
| `SP_*` | SharePoint sync base URL, cron, timezone. |

## Guidance
- After editing YAML/env files, restart the FastAPI process; `backend/app/deps.py` caches settings.
- Keep the Streamlit frontend’s `.env` in sync with backend changes (`AUTH_MODE`, `AUTH_ENABLED`, `BACKEND_API_BASE`, `FEEDBACK_DEFAULT_USER_ID`).
- When toggling `USAGE_LOG_ENABLED`, smoke test both `/api/v1/auth/login` and `/chat` to confirm the Oracle tables accept inserts.
