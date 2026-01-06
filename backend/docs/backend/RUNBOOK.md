# Runbook
Last updated: 2025-11-07

Operational guidance for keeping the backend healthy and for validating new builds before handing off to the Streamlit frontend.

## Daily Checks
1. `GET /healthz` should return `{"ok": true}` with all providers `up`.
2. Review application logs for sanitizer counters or ingestion errors.
3. If `USAGE_LOG_ENABLED=true`, spot‑check the Oracle tables (`AUTH_LOGINS`, `CHAT_SESSIONS`, `CHAT_INTERACTIONS`) to ensure rows continue to stream in (especially `RESP_MODE` transitions).

## Smoke Tests
| Step | Command | Expectation |
| --- | --- | --- |
| Health | `curl -s http://localhost:8000/healthz | jq .` | `ok=true`; investigate any `down (...)` entries. |
| Auth | `curl -s -X POST http://localhost:8000/api/v1/auth/login -d '{"email":...}'` | `200` with `{token,user{...}}`. Status `suspended/deleted` should return `403`. |
| Chat | `curl -s -X POST http://localhost:8000/chat -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{"question":"<known answer>"}' -i` | `200` with `X-Answer-Mode` header plus `mode` in the body (`rag/hybrid/fallback`). |
| Feedback create | `curl -s -X POST http://localhost:8000/api/v1/feedback/ ...` (include `metadata.question`/`answer_preview`) | `200` and sanitized `comment` when `SANITIZE_ENABLED=on`. |
| Feedback list | `curl -s http://localhost:8000/api/v1/feedback/?limit=5 | jq .` | Items ordered by `created_at desc`, `metadata_json` includes `mode`, `question`, `answer_preview`. |

## Embedding Job Promotion
1. **Pre-flight** – Confirm `.env` has valid Oracle + OCI credentials, the alias view points to the current production table, and sanitizer settings match expectations.
2. **Run job** – Trigger via CLI or `POST /api/v1/ingest/jobs` (with `update_alias=false` for dry runs). Watch logs for upload errors (`415`, `413`, `422`, `409`, `404`).
3. **Evaluate** – Enable `evaluate=true` when possible and review hit rate/MRR output. Inspect `logs_tail` for chunk counts and dedupe stats.
4. **Promote** – Re-run with `update_alias=true`, confirm `/chat` answers cite the new content, and record job metrics.

## Rollback
1. Identify the previous physical table (`<alias>_vN`) from change logs.
2. Run `ensure_alias(<alias>, <table>)` via `backend/providers/oracle_vs/index_admin.py`.
3. Re-run the smoke tests. Monitor fallback rate in `/chat` responses (and `CHAT_INTERACTIONS.RESP_MODE`) to ensure it returns to baseline.

## Troubleshooting
- **Embeddings down in `/healthz`** – Check OCI credentials and network access; `backend/app/deps.py` prints detailed probe info at startup.
- **Oracle authentication failures** – Ensure `DB_*` env values match the database service. The service logs the effective DSN and whoami output on boot.
- **Sanitization anomalies** – Inspect `sanitizer.log` for unexpected labels; adjust pattern packs or allowlists before re‑running ingestion/feedback writes.
- **Usage logging gaps** – When enabled, missing records usually indicate grants or synonyms broke. Query `ALL_TABLES`/`ALL_OBJECTS` for `AUTH_LOGINS` et al. and verify insert privileges.
- **DOCX figure assets missing** – Confirm `DOCX_EXTRACT_IMAGES` was enabled for the run, `RAG_ASSETS_DIR` is writable/mounted, and figure placeholders/chunks are turned on (`DOCX_INLINE_FIGURE_PLACEHOLDERS`, `DOCX_FIGURE_CHUNKS`) when you expect `[FIGURE:<id>]` markers or `chunk_type=figure` rows.

## References
- [Setup & Run](./SETUP_AND_RUN.md) – local bootstrap instructions.
- [API Reference](./API_REFERENCE.md) – endpoint payloads.
- [Ingestion & Manifests](./INGESTION_AND_MANIFESTS.md) – manifest schema and error catalogue.
- [Sanitization](./SANITIZATION.md) – pattern packs and env toggles.
