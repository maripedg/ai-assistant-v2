# Sanitization

Sanitization is implemented in [backend/common/sanitizer.py](../../backend/common/sanitizer.py) and configured through JSON pattern packs such as [backend/config/sanitize/default.patterns.json](../../backend/config/sanitize/default.patterns.json). The embed job imports `sanitize_if_enabled()` to scrub document text before chunking.

## Runtime Modes
Set via `SANITIZE_ENABLED` (default `off`):
- `off`: passthrough; counters are empty.
- `shadow`: patterns are detected and logged, but the original text is returned.
- `on`: placeholders replace the detected spans; counters and audit logs are emitted.

`sanitize_if_enabled(text, doc_id)` returns `(processed_text, counters)` where `counters` tallies matches per label.

## Environment Variables
| Variable | Description | Default |
| --- | --- | --- |
| `SANITIZE_ENABLED` | `off`, `shadow`, or `on`. | `off` |
| `SANITIZE_PROFILE` | Pattern pack name; resolves `${SANITIZE_CONFIG_PATH}/${profile}.patterns.json`. | `default` |
| `SANITIZE_CONFIG_PATH` | Directory containing pattern JSON. | `./config/sanitize` |
| `SANITIZE_PLACEHOLDER_MODE` | `redact` (constant placeholder) or `pseudonym` (hash prefix). | `redact` |
| `SANITIZE_HASH_SALT` | Salt for pseudonym hashes. | `changeme` |
| `SANITIZE_AUDIT_ENABLED` | When `true`, append JSON lines to `sanitizer.log`. | `true` |

## Pattern Structure
Each `pii` entry defines a label with `pattern` or `patterns` (regex), optional `group_value` selectors, and optional `validator` hooks. Default labels include:
- `email`: Email addresses.
- `msisdn`, `phone`: Phone numbers and MSISDN literals (case-insensitive).
- `imsi`, `imei`, `iccid`: SIM identifiers, with Luhn validation for IMEI.
- `api_key`, `password`: API credentials and XML/JSON passwords.

Allowlisted tokens under `allowlist.tokens` bypass replacement when matched exactly. Placeholder formats are read from `placeholder.format` (`[{TYPE}]`) and `placeholder.format_pseudonym` (`[{TYPE}:{HASH}]`).

## Audit Logging
When counters are non-zero and `SANITIZE_AUDIT_ENABLED=true`, the module appends a JSON blob per document to `sanitizer.log`:
```json
{"doc_id": "fiber_manual.pdf", "profile": "default", "mode": "on", "redactions": {"phone": 2}}
```
The embed job also prints `[sanitizer:<doc>] {"phone": 2}` for quick visibility.

## Override Profiles
Tenants can point `SANITIZE_CONFIG_PATH` to a different directory or set `SANITIZE_PROFILE=<tenant>` to load `<tenant>.patterns.json`. Patterns are cached per `(path, profile)` pair to avoid repeated disk reads.

## TODO
- Document how runtime services (not just ingestion) should call the sanitizer if conversational inputs need scrubbing. Currently only the ingest path integrates it.
- Expand validator hooks beyond `luhn` if new PII types require checksum or structural checks.
