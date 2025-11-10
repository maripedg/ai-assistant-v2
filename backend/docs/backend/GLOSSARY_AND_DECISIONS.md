# Glossary & Decisions
Last updated: 2025-11-07

## Glossary
- **Alias view** – Oracle view named by `embeddings.alias.name` (e.g., `MY_DEMO`) exposing `(ID, TEXT, METADATA, EMBEDDING)` regardless of the active physical table (`MY_DEMO_v1`, `MY_DEMO_v2`, …).
- **Embedding profile** – Bundle in `config/app.yaml` that defines chunker settings, index/table names, distance metrics, metadata keep lists, and batching hints.
- **Golden queries** – Regression set stored in [backend/ingest/golden_queries.yaml](../../backend/ingest/golden_queries.yaml) used during ingestion evaluation.
- **Short query** – Question whose alphanumeric token count is ≤ `retrieval.short_query.max_tokens`; triggers tighter thresholds before rag/hybrid decisions.
- **Hybrid gate** – Guardrail under `retrieval.hybrid.*` that enforces minimum similarity, chunk count, or context length before allowing hybrid responses.
- **Usage logging tables** – Oracle tables `AUTH_LOGINS`, `CHAT_SESSIONS`, `CHAT_INTERACTIONS` (the latter includes `RESP_MODE`) populated when `USAGE_LOG_ENABLED=true`.
- **RESP_MODE** – Column on `CHAT_INTERACTIONS` mirroring the `mode`/`X-Answer-Mode` decision (`rag`, `hybrid`, `fallback`). Used for reporting fallback rates.
- **Sanitization** – Regex/substitution pipeline defined in [SANITIZATION.md](./SANITIZATION.md); used by ingestion and feedback comments to scrub PII.

## Key Decisions
1. **Singleton providers**: Routers instantiate embeddings/vector/LLM clients once at import time to reduce per-request overhead. Config changes require a process restart.
2. **Oracle alias indirection**: Retrieval always queries the alias view so embed jobs can build new tables and swap aliases without downtime.
3. **Hybrid-first RAG**: Mode selection favours rag but promotes hybrid if similarity is marginal yet gates are satisfied, otherwise fallback is used to avoid hallucinations.
4. **Telemetry hooks**: Usage logging captures `RESP_MODE`, similarity, and auth metadata in Oracle for compliance and BI. Controlled via `USAGE_LOG_ENABLED`.
5. **Sanitization opt-in**: Deployment toggles (`SANITIZE_ENABLED=off|shadow|on`) allow staging environments to audit detections before enforcing redaction.
6. **Dual-write migration**: `storage.dual_write=true` mirrors writes between DB and JSON stores to ease migrations; reads always follow the `mode`.

## Open Questions
- Should provider singletons be replaced with request-scoped dependencies for multi-tenant deployments?
- Do we keep the placeholder `answer2` / `answer3` fields or repurpose them once multi-answer LLM prompts mature?
- Should usage logging tables live in a dedicated schema instead of the application schema, and should we expose health checks for logging?
