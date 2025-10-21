# API Config

Feature Flags

- `features.users_api`: enable/disable Users router.
- `features.feedback_api`: enable/disable Feedback router.

Storage

- `storage.users.mode`: `db | json` primary backend for Users.
- `storage.feedback.mode`: `db | json` primary backend for Feedback.
- `storage.dual_write`: if `true`, write to both DB and JSON; reads follow `mode`.
- JSON paths: `storage.users.json_path`, `storage.feedback.json_path`.

Sanitization

- Env-driven (backend/common/sanitizer.py):
  - `SANITIZE_ENABLED`: `off | shadow | on`
  - `SANITIZE_PROFILE`: rule set name (e.g., `default`)
  - `SANITIZE_CONFIG_PATH`: directory for `*.patterns.json`
  - `SANITIZE_PLACEHOLDER_MODE`: `redact | pseudonym`
  - `SANITIZE_HASH_SALT`: salt for pseudonym hashing
  - `SANITIZE_AUDIT_ENABLED`: `true|false`

Database

- App config: `database.sqlalchemy_url` (preferred when set)
- If empty, URL is built from env:
  - `DB_USER`, `DB_PASSWORD`
  - Either `DB_DSN` as `host:port/SERVICE`, or `DB_HOST`, `DB_PORT`, `DB_SERVICE`
- Effective URL format:
  - `oracle+oracledb://USER:PASSWORD@HOST:PORT/?service_name=SERVICE`
- Pooling:
  - `database.pool_min`, `database.pool_max`, `database.pool_timeout_seconds`

Server/CORS

- `server.cors.allow_*` arrays in `backend/config/app.yaml`.
