# Ingestion & Chunking Diagnostic
Date: 2025-12-30

## Entry Points and Call Graph
- **CLI / API worker**: `backend/batch/embed_job.py` (invoked via `backend/batch/cli.py` and `backend/app/services/ingest.py` jobs).
- **Loader**: `backend.ingest.router.route_and_load` → `backend.ingest.normalizer.normalize_metadata` feeds normalized items into `embed_job.embed_manifest`.
- **Chunker selection**: `embed_job.embed_manifest` reads `embeddings.profiles.<profile>.chunker` → chooses `chunker_type` (`structured_pdf`, `structured_docx`, `tokens`, or `char`).
  - Structured branches: `chunk_structured_pdf_items` / `chunk_structured_docx_items` (or `chunk_docx_toc_sections` when `USE_TOC_SECTION_DOCX_CHUNKER=1`).
  - Fixed branches: `chunk_text_by_tokens` or `chunk_text`.
- **Embedding**: `embed_job._build_strategy` duplicates a minimal char-based chunker for legacy fallbacks (no token path).
- **Upsert**: `OracleVSUpserter` writes chunks; not part of chunking duplication.

Simplified flow:
`embed_job.embed_manifest` → normalize → choose chunker_type → structured chunkers (docx/pdf) OR fixed chunkers (char/tokens) → `vector_buffer` → embeddings → upsert.

## Duplicate / Legacy Module Table
| Module path | What it does | Usage evidence | Non-usage evidence | Recommended action |
| --- | --- | --- | --- | --- |
| `backend/ingest/chunking/*` (char/token/structured_*.py + `__init__.py`) | Thin shims re-exporting loader chunkers. | Imported by `backend/batch/embed_job.py`, `backend/batch/cli.py`, tests under `backend/tests/ingest/*`, examples. | No unique code; all functions come from loader modules. | Keep short-term; plan to deprecate once imports switch to `backend.ingest.loaders.chunking.*`. |
| `backend/ingest/loaders/chunking/*` | Actual implementations (char, token, structured docx/pdf, TOC utils). | Imported indirectly through shims; direct tests in `backend/tests/ingest/test_structured_*`; examples in `backend/ingest/loaders/chunking/examples`. | Not directly imported by embed job (hidden behind shims). | Keep as canonical implementations. |
| `backend/core/embeddings/embedding_strategy.py::_build_strategy` | Defines a fallback strategy that chunks via inline char splitter (no tokens/structured). | Called from `embed_job._build_strategy` when custom strategies are present; mirrors char chunking logic. | Does not call token/structured chunkers; duplicates a subset of `chunk_text`. | Document as legacy fallback; consider merging with `chunk_text` helpers to avoid drift. |
| `backend/ingest/chunking/structured_docx_chunker` vs `backend/ingest/chunking/toc_section_docx_chunker` | Both chunk DOCX; TOC version gated by `USE_TOC_SECTION_DOCX_CHUNKER`. | Env-flag branch in `embed_job.embed_manifest`; examples under `backend/ingest/examples/run_toc_section_docx_chunker.py`. | TOC chunker disabled by default; only used when env var set. | Keep both; mark TOC as optional/experimental. |

## Notes on Documentation
- `backend/docs/backend/INGESTION_AND_MANIFESTS.md` still contains encoding artifacts and references the pipeline generically; chunker paths are correct but could point to `backend/ingest/chunking/*` shims rather than loader modules. No blocking changes made here.
- `backend/docs/backend/EMBEDDING_AND_RETRIEVAL.md` describes chunkers at a high level; does not cite duplicate paths.

## Instrumentation
- Added optional env flag `CHUNKING_DIAGNOSTIC=1` in `backend/batch/embed_job.py` to log the selected `chunker_type`, `effective_max`, `profile`, and whether `USE_TOC_SECTION_DOCX_CHUNKER` is active. Disabled by default; no behavior change.

## Usage Map Script
- `scripts/ingestion/find_chunking_usage.py` walks the repo, parses imports via `ast`, and writes `docs/engineering/ingestion_chunking_usage_map.json`. Candidates include both shim (`backend.ingest.chunking.*`) and implementation (`backend.ingest.loaders.chunking.*`) modules.
- Run: `python scripts/ingestion/find_chunking_usage.py`

## Active vs Duplicate Summary
- **Canonical path today**: `backend.ingest.chunking.*` (shims) consumed by embed job/CLI/tests → implementation lives in `backend.ingest.loaders.chunking.*`.
- **Duplicates**: Shim layer duplicates exports of loader implementations; `_build_strategy` duplicates a simple char chunker separate from `chunk_text`.
- **Cleanup plan** (non-breaking, future): Update imports to `backend.ingest.loaders.chunking.*`, add deprecation warnings to shims, and consolidate char-splitting logic in `_build_strategy` to reuse `chunk_text`.
