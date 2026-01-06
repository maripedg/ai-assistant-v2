# Embedding & Retrieval
Last updated: 2025-12-30

This document explains how documents are embedded, how the runtime chooses between rag/hybrid/fallback, and how the `X-Answer-Mode` header/`decision_explain` payload are produced.

## Embedding Lifecycle
1. **Manifest expansion** — JSONL manifests (see [Ingestion & Manifests](./INGESTION_AND_MANIFESTS.md)) list file paths, optional tags, language hints, and target profiles.
2. **Cleaning & sanitization** — Loaders normalise text (strip invisible chars, harmonise line endings). `backend.common.sanitizer.sanitize_if_enabled()` then redacts or audits PII according to `SANITIZE_*` flags before chunking.
3. **Chunking** — Profile-driven chunkers (char/tokens) apply `size` + `overlap`, attach metadata such as `source`, `doc_id`, `chunk_id`, `tags`, `lang`, and optional dedupe hashes.
   - When DOCX image extraction is enabled, DOCX chunking injects `[FIGURE:<figure_id>]` markers and emits additional `chunk_type=figure` entries with `figure_id/image_ref/parent_chunk_id`; figure chunks embed their text description only (no binaries).
4. **Embeddings** — `make_embeddings()` (OCI adapter) batches requests using `embeddings.batching.{batch_size,rate_limit_per_min}`. Loader hints (`input_types.documents/queries`) ensure Oracle Vector Search uses compatible distance metrics.
5. **Upsert & alias** — Chunks land in the physical table named by `embeddings.profiles.<profile>.index_name` (or, when `--domain-key` is provided, `embeddings.domains.<key>.index_name`). If `update_alias=true`, `backend/providers/oracle_vs/index_admin.py` recreates the alias view (default `embeddings.alias.name`, or `embeddings.domains.<key>.alias_name` when overridden) pointing to the new table. Evaluation runs (optional) exercise golden queries before alias rotation.

## DOCX Inline Figures
- **What gets embedded**: Only text; figure chunks contain a deterministic description (no binaries). Inline text chunks may include `[FIGURE:<figure_id>]` placeholders to preserve position when `DOCX_INLINE_FIGURE_PLACEHOLDERS=1`.
- **Metadata**: Figure chunks set `chunk_type=figure`, `figure_id`, `image_ref` (relative, e.g., `<doc_id>/img_003.png`), and `parent_chunk_id/parent_chunk_local_index` so retrieval can join the text chunk that referenced the image.
- **Storage**: Images are written locally under `RAG_ASSETS_DIR/<doc_id>/img_<NNN>.<ext>` when `DOCX_EXTRACT_IMAGES=1`; `RAG_ASSETS_DIR` defaults to `./data/rag-assets` relative to the repo. Enable `DOCX_IMAGE_DEBUG=1` for per-image extraction logs.
- **Troubleshooting**: Loader emits `DOCX_IMAGES_SUMMARY` (counts of blips/relationships/writes); chunker emits `DOCX_FIGURE_CHUNKING_SUMMARY` (placeholders/figure chunks/parent links). If figures exist but `rels_mapped=0`, relationships parsing is broken; if `zip_member_miss > 0`, the relationship targets are not found in the DOCX; if `image_emit_skip_reason=flags_disabled` the figure/placeholder flags were off; if images write but figure chunks are zero, the chunker did not receive `block_type=image`.
- **Prompt context**: Figure chunks are kept in retrieval metadata for the UI, but are excluded from the LLM prompt by default (`retrieval.hybrid.exclude_chunk_types_from_llm: ["figure"]`). Retrieval skips excluded chunk types when forming the LLM context and keeps scanning ranked candidates so non-figure text still satisfies chunk/byte gates. Inline placeholders in text chunks remain the bridge between answers and rendered images.
- **Backwards compatibility**: With all DOCX flags off (`DOCX_EXTRACT_IMAGES`, `DOCX_INLINE_FIGURE_PLACEHOLDERS`, `DOCX_FIGURE_CHUNKS`), chunk text and embeddings match the legacy text-only pipeline.

## Retrieval Modes
Implementation: [backend/core/services/retrieval_service.py](../../backend/core/services/retrieval_service.py).

| Mode | Trigger | Header / Payload |
| --- | --- | --- |
| `rag` | `max_similarity >= threshold_high`. | `X-Answer-Mode: rag`; `decision_explain.mode = "rag"`. |
| `hybrid` | `threshold_low <= max_similarity < threshold_high` and hybrid gates satisfied (min chunks/context). | `X-Answer-Mode: hybrid`; `sources_used` may be `partial` when not every retrieved chunk survives gating. |
| `fallback` | No chunks cleared thresholds, hybrid gates failed, or primary LLM returned blank. | `X-Answer-Mode: fallback`; `used_llm` flips to `"fallback"`. |

Short questions (token count ≤ `retrieval.short_query.max_tokens`) temporarily tighten thresholds to avoid hallucinated rag decisions. The `decision_explain.short_query_active` flag captures this and mirrors in telemetry (`CHAT_INTERACTIONS.RESP_MODE`).

## Thresholds & Distances
- **Normalized**: default mode where similarity lives in `[0,1]` regardless of Oracle distance. Dot-product values are mapped via `(raw + 1) / 2`.
- **Raw**: when `score_mode=raw`, provide `raw_dot_low/high` or `raw_cosine_low/high`. Unsupported distances raise on startup.
- **Hybrid gates** (`retrieval.hybrid.*`): enforce `min_similarity_for_hybrid`, `min_chunks_for_hybrid`, and `min_total_context_chars`. Failing any gate downgrades the answer to fallback even if similarity cleared `threshold_low`.

## Diagnostics
- `retrieved_chunks_metadata`: raw oracle rows sorted by similarity with `text` previews.
- `used_chunks`: subset that made it into the prompt.
- `sources_used`: `all`, `partial`, or `none` (signals if the UI should downplay citations).
- `decision_explain`: contains thresholds, `effective_query`, `short_query_active`, `used_llm`, `mode`, `score_mode/distance`, and `retrieval_target` (view queried). This payload is mirrored into usage logging for later analytics.
- Override: `X-RAG-Domain: <domain_key>` routes retrieval to `embeddings.domains.<key>.alias_name`; omitting it keeps using `embeddings.alias.name`. Decision thresholds and gates are unchanged by the override.

## Tips
- Keep profile distance metrics in sync with `retrieval.distance`.
- When enabling raw mode, update both normalized and raw thresholds so short-query overrides remain meaningful.
- Remember to bump `embeddings.alias.active_index` (or run with `update_alias=true`) whenever promoting a new table; `/chat` uses the alias view, so mismatches show up as empty answers/fallback spikes.
