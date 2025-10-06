# Glossary & Decisions

## Glossary
- **Alias view**: Oracle view named by `embeddings.alias.name` (e.g., `MY_DEMO`) that exposes `(ID, TEXT, METADATA, EMBEDDING)` regardless of which versioned table currently holds data. Managed by [backend/providers/oracle_vs/index_admin.py](../../backend/providers/oracle_vs/index_admin.py).
- **Embedding profile**: Configuration bundle in `config/app.yaml` describing chunking, distance metric, and storage targets for a corpus (e.g., `legacy_profile`, `standard_profile`).
- **Golden queries**: Curated questions defined in [backend/ingest/golden_queries.yaml](../../backend/ingest/golden_queries.yaml) with expected doc IDs/phrases, used to regression-test retrieval quality.
- **Short query**: Question with ≤ `retrieval.short_query.max_tokens` alphabetic tokens; triggers tighter thresholds to reduce false positives.
- **Fallback mode**: Response path when similarity scores are below thresholds or the primary LLM returns no output; uses the fallback prompt/model configured in `config/app.yaml` and `providers.yaml`.
- **Shadow sanitization**: Mode where PII is detected and logged but original text is preserved (`SANITIZE_ENABLED=shadow`). Useful during tuning to gauge pattern coverage without altering embeddings.
- **OracleVS upserter**: Helper in `backend/batch/embed_job.py` that performs insert-or-skip operations into Oracle tables, optionally deduplicating by hash.
- **Decision explain**: Diagnostic payload attached to `/chat` responses summarising scoring inputs (`score_mode`, thresholds, selected mode, LLM used).

## Key Decisions
1. **Direct singleton providers**: `backend/app/routers/chat.py` instantiates embeddings, vector store, and LLMs at import time for simplicity. This minimises per-request overhead but couples process lifetime to provider availability; restarts are required after config changes.
2. **Oracle alias indirection**: Retrieval always targets a stable view so embed jobs can load new tables and swap aliases without API downtime.
3. **OCI-native chat fallback**: Primary LLM may be a public alias (`cohere.command-english-v3.0`), but fallback requires an OCID and uses the OCI Generative AI Chat API (`OciChatModelChat`) for reliability.
4. **Score normalisation**: Dot-product scores are normalised by default to abstract away vector store distance semantics. Raw score mode remains configurable for teams wanting full control.
5. **Sanitization opt-in**: Sanitization is optional by default (`off`) to simplify local development; production environments are expected to enable `on` or `shadow` according to compliance needs.
6. **Evaluation baked into ingestion**: Golden query evaluation runs immediately after embedding to provide a promotion gate before alias updates occur.

## TODO / Open Questions
- Should provider singletons be replaced with dependency-injected factories to support multi-tenant or per-request configuration?
- How should extractive answers (`answer2`, `answer3`) be populated, or should these fields be removed from the API schema?
- The `standard_profile` declares `chunker.type: tokens`, but no tokenizer-backed implementation exists yet; confirm strategy expectations or adjust config.
- Define a process for distributing tenant-specific sanitization packs beyond the default profile.
