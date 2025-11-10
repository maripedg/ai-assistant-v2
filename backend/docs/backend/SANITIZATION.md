# Sanitization
Last updated: 2025-11-07

Sanitization protects PII/secret material before it reaches Oracle Vector Search or long-term storage. The same helper is used during ingestion and when operators create feedback entries via `/api/v1/feedback/`.

## Engine
- Module: [backend/common/sanitizer.py](../../backend/common/sanitizer.py).
- Entry point: `sanitize_if_enabled(text: str, doc_id: str) -> (processed_text, counters)`.
- Modes (`SANITIZE_ENABLED`): `off` (no-op), `shadow` (detect + audit, original text returned), `on` (redact or pseudonymize).
- Audit: when matches occur and `SANITIZE_AUDIT_ENABLED=true`, the module appends JSON lines to `sanitizer.log` including `doc_id`, profile, mode, and redaction counts.

## Configuration
| Variable | Default | Notes |
| --- | --- | --- |
| `SANITIZE_ENABLED` | `off` | `off`, `shadow`, or `on`. |
| `SANITIZE_PROFILE` | `default` | Chooses `<profile>.patterns.json` from `SANITIZE_CONFIG_PATH`. |
| `SANITIZE_CONFIG_PATH` | `./config/sanitize` | Directory containing pattern packs. |
| `SANITIZE_PLACEHOLDER_MODE` | `redact` | Switch to `pseudonym` to append short hashes per match. |
| `SANITIZE_HASH_SALT` | `changeme` | Mixes into pseudonym hashing. |
| `SANITIZE_AUDIT_ENABLED` | `true` | Enables `sanitizer.log`. |

Patterns support single `pattern` or `patterns[]` entries with optional `group_value` replacements and `validator` hooks (currently `luhn`). Allow-listed tokens bypass replacements.

## Usage Sites
- **Ingestion** – Every document path passes through the sanitizer after normalization and before chunking. Redaction counters are logged to stdout and stored in `sanitizer.log`.
- **Feedback comments** – `/api/v1/feedback/` sanitizes `payload.comment` (see [backend/app/routers/feedback.py](../../backend/app/routers/feedback.py)). This prevents credentials from leaking into metadata. Counters are ignored for UX but can be inspected via `sanitizer.log`.

## Tips
- Use `shadow` mode in staging to tune patterns without affecting embeddings.
- Keep placeholder formats short; large tokens impact chunk sizes and similarity.
- Update allowlists whenever legitimate values (e.g., official phone numbers) should survive redaction.
- Remember to restart the backend or CLI workers after editing pattern files—config is cached per process.

See [Ingestion & Manifests](./INGESTION_AND_MANIFESTS.md) for job flow and [Config Reference](./CONFIG_REFERENCE.md) for the broader env matrix.
