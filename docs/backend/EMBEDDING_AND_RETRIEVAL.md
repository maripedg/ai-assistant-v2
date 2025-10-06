# Embedding & Retrieval

This document covers how embeddings are generated and how the runtime retrieval pipeline consumes them.

## Embedding Lifecycle
- **Profiles**: Defined in [backend/config/app.yaml](../../backend/config/app.yaml) under `embeddings.profiles`. Each profile supplies a physical `index_name`, chunker settings, distance metric, and preferred metadata keys.
- **Alias indirection**: `embeddings.alias.name` (e.g., `MY_DEMO`) exposes a stable Oracle view that the API queries. Individual jobs write to versioned tables (e.g., `MY_DEMO_v1`) and optionally repoint the alias.
- **Batching**: `embeddings.batching` specifies `batch_size`, `workers` (currently informational), and `rate_limit_per_min`; CLI flags override these values per run.
- **Dedupe**: When `embeddings.dedupe.by_hash=true`, `_hash_normalize()` lowercases and trims chunk text before hashing with SHA-256. Upserts skip rows whose `HASH_NORM` already exists.
- **Document metadata**: `_ensure_chunk_metadata()` persists `source`, `doc_id`, `chunk_id`, `lang`, `tags`, and `priority`. Profiles may request extra metadata in future via the strategy interface.
- **Schema management**: `ensure_index_table()` creates or validates the target table with `VECTOR(dim)` columns, ensuring the embedding dimension matches the OCI model output. Alias creation uses `CREATE OR REPLACE VIEW <alias> AS SELECT ... FROM <index>`.

## RetrievalService Overview
Implementation: [backend/core/services/retrieval_service.py](../../backend/core/services/retrieval_service.py).

1. **Similarity search** – `vector_store.similarity_search_with_score(question, k)` queries Oracle via [backend/providers/oci/vectorstore.py](../../backend/providers/oci/vectorstore.py). Results include raw Oracle scores and metadata enriched with `raw_score`, `similarity`, and `text_preview`.
2. **Normalization** – `_normalize()` transforms raw scores into `[0,1]` based on the configured `distance` (`dot_product`, `cosine`, or fallback). For raw scoring, thresholds use the metric-specific ranges.
3. **Threshold selection** – `_pick_thresholds()` chooses `(low, high)` from `retrieval.thresholds` or `retrieval.short_query` when `_is_short_query()` finds ≤ `max_tokens` alphabetic tokens in the question.
4. **Mode decision** – `_decide_mode()` returns:
   - `rag` when score ≥ high;
   - `hybrid` when low ≤ score < high;
   - `fallback` otherwise.
   The same thresholds are recapped in `decision_explain`.
5. **Context assembly** – `_select_context()` sorts by `similarity`, removes duplicates using `dedupe_by` (default `doc_id`), filters out tiny chunks (less than `hybrid.min_tokens_per_chunk` chars), and concatenates text up to `hybrid.max_context_chars` or `hybrid.max_chunks`.
6. **Prompt selection** – Uses the `rag` system prompt by default and falls back to `hybrid` prompt when the mode is `hybrid`. Prompts come from `prompts.*.system` in `app.yaml`.
7. **LLM interaction** – Calls `primary_llm.generate()` with `[Context]` and `[Question]` sections. If the result is empty, `_build_response()` reinvokes the fallback LLM with the fallback prompt.
8. **Response payload** – Returns question echo, `answer`, placeholders for `answer2`/`answer3`, raw metadata list, the actual chunks used in the prompt, `sources_used` hint (`all`, `partial`, `none`), and `decision_explain`.

### Score Modes & Distances
- `score_mode=normalized` (default) expects scores in `[0,1]`. Dot-product raw scores are transformed via `(raw + 1)/2`.
- `score_mode=raw` instructs the service to work directly with Oracle values. Separate thresholds exist for dot-product (`raw_dot_low`, `raw_dot_high`) and cosine distances (`raw_cosine_low`, `raw_cosine_high`). Unsupported metrics fall back to normalized heuristics.
- Short-query thresholds override both modes when triggered.

### Short Query Handling
`_is_short_query()` removes punctuation, lowercases, and counts alphabetic tokens. When `len(tokens) <= short_max_tokens`, the service applies tighter thresholds (`short_low`, `short_high`) and reports `short_query_active=true`.

### Fallback Behavior
- If no results are returned, if context assembly yields zero chunks, or if the primary LLM returns blank output, `_build_response()` forces `mode="fallback"` and uses the fallback prompt/model.
- The fallback LLM defaults to the same primary model when `make_chat_model_fallback()` is not configured, but the shipped configs expect a dedicated OCI OCID.

## Evaluation Loop
`_evaluate_golden_queries()` in the embed job executes retrieval end-to-end using the same alias the API will query. Integrate this into ingestion pipelines to catch regressions before promoting a new index table.

## TODO
- Implement extractive and multi-answer paths (`answer2`, `answer3`) or prune them from the schema to avoid confusion.
- Consider exposing retrieval diagnostics (e.g., raw ranks, trace IDs) via an authenticated endpoint for deeper observability.
