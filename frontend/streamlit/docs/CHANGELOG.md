# Changelog
Last updated: 2025-11-07

## Unreleased

- Add full documentation set under docs/ and .env.example
- Clarify setup, configuration, services, and state

## 2025-11-07
- Added Admin ➜ Feedback History polish: Q/A column now combines question + answer preview, and a toggle reveals the Raw JSON tab for full payload inspection.
- Hardened auth header injection: every admin call now routes through `app.services.api_client._auth_headers()`, and the UI refuses to run when `AUTH_ENABLED=true` but no JWT is present.
- Normalised thumbs feedback: empty comments stay empty (no auto-fill from the answer), while metadata still carries `question`, `answer_preview`, `mode`, and `message_id`.
