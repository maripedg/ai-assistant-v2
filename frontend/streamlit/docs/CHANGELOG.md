# Changelog
Last updated: 2025-11-07

## Unreleased

- Add full documentation set under docs/ and .env.example
- Clarify setup, configuration, services, and state

## 2026-02-05
- Added `--domain-key` embed CLI override that resolves `index_name`/`alias_name` from `embeddings.domains.*` in `backend/config/app.yaml`; defaults remain unchanged when absent.
- Updated backend docs (INGESTION_AND_MANIFESTS, EMBEDDING_AND_RETRIEVAL, CONFIG_REFERENCE) to document domain-targeted embedding runs.
- Change rationale: POC Phase A for multi-domain embedding without multi-target refactor; backward compatible when `--domain-key` is not provided.
- Validation (not run): `python -m backend.batch.cli embed --manifest backend/ingest/examples/my_docs.jsonl --profile standard_profile --domain-key TS_SBC --update-alias`; `python -m backend.batch.cli embed --manifest backend/ingest/examples/my_docs.jsonl --profile standard_profile --domain-key TS_STP --update-alias`.
## 2025-11-07
- Added Admin ➜ Feedback History polish: Q/A column now combines question + answer preview, and a toggle reveals the Raw JSON tab for full payload inspection.
- Hardened auth header injection: every admin call now routes through `app.services.api_client._auth_headers()`, and the UI refuses to run when `AUTH_ENABLED=true` but no JWT is present.
- Normalised thumbs feedback: empty comments stay empty (no auto-fill from the answer), while metadata still carries `question`, `answer_preview`, `mode`, and `message_id`.
