# Backend Changelog

## 2026-01-08
- Files changed: backend/core/services/retrieval_service.py; backend/docs/backend/EMBEDDING_AND_RETRIEVAL.md; backend/docs/CHANGELOG.md.
- What changed: Text-first selection now skips image-like chunks (`chunk_type=figure` or `block_type=image`), excludes them from adaptive-threshold math (p90/t_adapt), and applies cap-per-doc/MMR on text-only candidates when building LLM context, while keeping images in retrieved metadata and honoring `max_chunks`/`max_context_chars`.
- Why: Image/figure chunks could dominate similarity distributions and cap-per-doc/MMR budget, inflating adaptive thresholds and preventing relevant text from being selected.
- Backward compatibility: Retrieval metadata still includes image chunks for UI rendering; text-only docs and existing modes remain unchanged.
- Validation: Not run (not requested).

## 2026-01-06
- Files changed: backend/core/services/retrieval_service.py; backend/tests/retrieval/test_exclude_figure_chunks.py; backend/docs/backend/EMBEDDING_AND_RETRIEVAL.md.
- What changed: Made figure-type chunks selection-aware so retrieval skips them when building LLM context, continues deeper to fill text slots, and logs ranked/excluded/context size diagnostics.
- Why: Figure-only chunks could consume context slots and trigger `min_total_chars_gate` failures after filtering, forcing fallback despite available text.
- Backward compatibility: Figure metadata still returned for UI rendering; text-only flows unchanged.
- Validation: Unit test exercises exclusion while keeping non-figure chunks in context (not executed here).

## 2026-01-06
- Files changed: backend/core/services/retrieval_service.py; backend/config/app.yaml; backend/tests/retrieval/test_exclude_figure_chunks.py; backend/docs/backend/EMBEDDING_AND_RETRIEVAL.md.
- What changed: Added configurable `exclude_chunk_types_from_llm` (defaulting to `["figure"]`) and filtered figure-only chunks out of the LLM prompt while keeping them in retrieval metadata.
- Why: Figure chunks could outrank procedural text, starving the LLM of context and causing unintended `NO_CONTEXT` fallbacks.
- Backward compatibility: Text-only docs unaffected; figure metadata still returned for UI rendering.
- Validation: Unit test covers figure exclusion from context (not executed here).

## 2026-01-06
- Files changed: backend/ingest/loaders/docx_loader.py.
- What changed: Fixed DOCX inline image detection by using namespace-agnostic blip lookup (python-docx xpath namespace bug), ensured image emit attempts include hash/metadata even when writes fail, and incremented write attempts before disk writes.
- Why: Inline images were silently skipped (no placeholders/figure chunks) because blip detection always failed.
- Backward compatibility: DOCX text chunking unchanged; image features still flag-gated.
- Validation: Covered by unit tests (not executed here).

## 2026-01-06
- Files changed: backend/batch/cli.py; backend/ingest/loaders/docx_loader.py; backend/ingest/loaders/chunking/toc_section_docx_chunker.py; backend/tests/ingest/test_toc_section_docx_chunker.py; backend/tests/ingest/test_docx_loader_images.py; backend/docs/backend/INGESTION_AND_MANIFESTS.md; backend/docs/backend/EMBEDDING_AND_RETRIEVAL.md; backend/docs/backend/CONFIG_REFERENCE.md.
- What changed: Auto-loaded `.env` for the batch CLI; hardened DOCX image extraction using relationships parsing plus ordered blip traversal; added figure placeholder/figure-chunk summaries and deterministic figure descriptions; documented DOCX_IMAGE_DEBUG and troubleshooting signals; refreshed tests for extraction order and SOP/figure behaviour.
- Why: Prevent SOP content from mixing across procedures and ensure inline DOCX figures are extracted, written to `RAG_ASSETS_DIR`, and chunked with placeholders/figure chunks.
- Backward compatibility: DOCX figure features remain gated behind env flags; SOP parsing keeps legacy behaviour unless SOP headings are present.
- Validation: Added unit tests (not executed in this environment).

## 2026-01-05
- Files changed: backend/ingest/loaders/docx_loader.py; backend/ingest/loaders/chunking/toc_section_docx_chunker.py; backend/batch/embed_job.py; backend/tests/ingest/test_toc_section_docx_chunker.py; backend/docs/backend/CONFIG_REFERENCE.md; backend/docs/backend/INGESTION_AND_MANIFESTS.md; backend/docs/backend/EMBEDDING_AND_RETRIEVAL.md; backend/docs/backend/SETUP_AND_RUN.md; backend/docs/backend/RUNBOOK.md.
- What changed: Added opt-in DOCX image extraction to `RAG_ASSETS_DIR`, inline figure placeholders, and figure chunks (metadata carries `figure_id/image_ref/parent_chunk_id`); chunker/test updates plus docs for the new flags.
- Why: Enable figure-aware ingestion and embeddings without breaking default DOCX text chunking.
- Backward compatibility: Defaults keep all new flags off (`DOCX_EXTRACT_IMAGES`, `DOCX_INLINE_FIGURE_PLACEHOLDERS`, `DOCX_FIGURE_CHUNKS`), preserving prior behaviour.
- Validation: Added unit coverage for placeholders/figure chunks; pytest not available in the current environment to execute tests.

## 2025-12-30
- Files changed: backend/app/routers/chat.py; backend/core/services/retrieval_service.py; backend/docs/backend/API_REFERENCE.md; backend/docs/backend/BACKEND_OVERVIEW.md; backend/docs/backend/CONFIG_REFERENCE.md; backend/docs/backend/EMBEDDING_AND_RETRIEVAL.md; backend/docs/CHANGELOG.md.
- What changed: Fixed X-RAG-Domain override to use a single retrieval call and honor the effective target view, added retrieval_target diagnostics, and cleaned documentation formatting.
- Why: Phase B regression fix to ensure domain-aware retrieval actually switches views.
- Backward compatibility: No header keeps the default alias behavior unchanged.
- Validation:
```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-RAG-Domain: TS_STP" \
  -d '{"question":"How to Pause Queue Service","user_id":1,"session_id":"abc123"}' | jq .

curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"How to Pause Queue Service","user_id":1,"session_id":"abc123"}' | jq .
```

## 2025-12-30
- Files changed: backend/providers/oci/vectorstore.py; backend/core/services/retrieval_service.py.
- What changed: Added target_view support to OracleVSStore similarity search and ensured retrieval uses the effective target view without duplicate calls.
- Why: Fix runtime TypeError and make domain override functional end-to-end.
- Backward compatibility: Default path unchanged when no X-RAG-Domain header is provided.
- Validation: same commands as above (TS_STP with header vs without).
