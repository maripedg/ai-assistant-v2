# Backend Overview

## Global Table of Contents
- [Backend Overview](./BACKEND_OVERVIEW.md)
- [Setup & Run](./SETUP_AND_RUN.md)
- [API Reference](./API_REFERENCE.md)
- [Ingestion & Manifests](./INGESTION_AND_MANIFESTS.md)
- [Sanitization](./SANITIZATION.md)
- [Embedding & Retrieval](./EMBEDDING_AND_RETRIEVAL.md)
- [Runbook](./RUNBOOK.md)
- [Config Reference](./CONFIG_REFERENCE.md)
- [Glossary & Decisions](./GLOSSARY_AND_DECISIONS.md)

## Purpose
The backend delivers retrieval-augmented generation (RAG) services for the AI Assistant. It exposes a FastAPI application, ingestion utilities that populate Oracle Vector Store tables, and sanitization tooling that protects sensitive data before indexing or exposure.

## Architecture Summary
- FastAPI app in [backend/app/main.py](../../backend/app/main.py) wires up CORS, health checks, and the `/chat` entrypoint.
- Dependency factories in [backend/app/deps.py](../../backend/app/deps.py) load configuration, build OCI GenAI clients, and verify Oracle views.
- Retrieval orchestration lives in [backend/core/services/retrieval_service.py](../../backend/core/services/retrieval_service.py), combining vector similarity results with primary and fallback LLMs.
- Vector IO depends on Oracle via [backend/providers/oci/vectorstore.py](../../backend/providers/oci/vectorstore.py) and alias management helpers in [backend/providers/oracle_vs/index_admin.py](../../backend/providers/oracle_vs/index_admin.py).
- Batch ingestion is coordinated by [backend/batch/embed_job.py](../../backend/batch/embed_job.py) and its CLI wrapper [backend/batch/cli.py](../../backend/batch/cli.py).
- Sanitization operates through [backend/common/sanitizer.py](../../backend/common/sanitizer.py) and pattern packs in [backend/config/sanitize/default.patterns.json](../../backend/config/sanitize/default.patterns.json).

## Component Map
- `backend/app/`: FastAPI application, routing, request/response models, startup probes.
- `backend/core/`: Domain services, ports, and (placeholder) embedding strategies.
- `backend/providers/`: Infrastructure adapters for OCI GenAI and Oracle Vector Store.
- `backend/batch/`: Command-line entrypoints and long-running embed jobs.
- `backend/common/`: Cross-cutting helpers such as sanitization.
- `backend/config/`: Default YAML/JSON configuration shipped with the project.
- `backend/ingest/`: Manifest specification, golden queries, and samples used by embed jobs.
- `backend/tests/`: Pytest suites covering retrieval decisioning, adapters, and API contracts.

## Data Flow Overview
1. **Question intake** – `/chat` receives a `ChatRequest`, instantiates singleton providers via `backend.app.deps`, and calls `RetrievalService.answer()`.
2. **Similarity search** – `RetrievalService` queries the Oracle vector view (alias) for the top `k` chunks and normalizes scores based on the configured metric.
3. **Thresholding & mode selection** – Score thresholds pick one of `rag`, `hybrid`, or `fallback`, with short queries tightening bounds.
4. **Context assembly** – Deduplicated chunks are stitched into a prompt respecting `max_context_chars` and chunk limits.
5. **LLM generation** – The primary OCI GenAI chat model renders the answer. If empty or below thresholds, the fallback model produces a generic response.
6. **Response** – The API returns answer text, mode metadata, chunk diagnostics, and an `X-Answer-Mode` header.
7. **Ingestion** – Separately, embed jobs load manifests, sanitize documents, chunk content, fetch embeddings from OCI, upsert vectors into Oracle, and optionally update alias views.

## External Dependencies
- OCI GenAI SDKs (`oci`, `langchain-community`) for embeddings and chat completions.
- Oracle DB client (`oracledb`) and the LangChain Oracle Vector Store integration.
- FastAPI stack (`fastapi`, `uvicorn`) and configuration helpers (`python-dotenv`, `PyYAML`).
- Optional libraries for ingestion: `PyPDF2` for PDFs, `tqdm` for progress reporting (if enabled).

## TODO
- `backend/app/routers/ingest.py`, `jobs.py`, and several provider stubs remain empty; document or remove when implementations land.
- `backend/core/embeddings/embedding_strategy.py` only defines interfaces; link the embed job once concrete strategies exist.
- Character encoding comments in `config/app.yaml` appear corrupted (e.g., "MantAcn"); clarify intent with configuration owners.
# Backend Overview

## Purpose
High‑level view of the AI Assistant backend and how requests flow through the system. This service exposes a minimal API for health and chat, performs retrieval over Oracle Vector Search, and composes responses using configured LLMs.

## Components / Architecture
Directory layout in `backend/`:

- `app/` – FastAPI app, routers, dependency factories (`deps.py`), startup validation, models.
- `core/` – Core services and ports. Notably `core/services/retrieval_service.py` with ranking/thresholding logic.
- `common/` – Shared utilities (e.g., `common/sanitizer.py`).
- `config/` – App and provider config YAMLs (`app.yaml`, `providers.yaml`).
- `ingest/` and `ingestion/` – Ingestion pipeline code and examples.
- `providers/` – Integrations: OCI embeddings, OracleVS vector store, model clients.
- `queue/`, `repos/`, `worker/` – Extension points for async jobs, persistence adapters, and background workers (if enabled).
- `tests/` – Unit tests.

Typical request path (chat):

1. Client calls `POST /chat` with `question`.
2. Router builds singletons via `app/deps.py`: embeddings adapter, OracleVS store, LLMs.
3. `RetrievalService.answer()` executes similarity search, normalizes scores, applies thresholds and gates, and selects context.
4. Primary/fallback LLM generates the answer; response includes decision explanation and used chunks.

## Parameters & Env
- App config: `backend/config/app.yaml` (retrieval, embeddings profiles, prompts).
- Provider config: `backend/config/providers.yaml` (OCI endpoints, auth mode, DB DSN/user/password, vector distance).
- Environment variables: see [Config](./CONFIG_REFERENCE.md).

## Examples
Minimal request to the running backend:

```bash
curl -s -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is in the demo index?"}' | jq .
```

Health probe:

```bash
curl -s http://localhost:8000/healthz | jq .
```

## Ops Notes
- Startup validates embeddings/LLMs via `validate_startup()` and prints probe results.
- Retrieval ranking and normalization are controlled by `retrieval.distance`, `score_mode`, `score_kind`, `docs_normalized` and thresholds in `app.yaml`.
- OracleVS uses an alias view; ensure it exists and projects `(ID, TEXT, METADATA, EMBEDDING)`.

## See also
- [API Reference](./API_REFERENCE.md)
- [Config](./CONFIG_REFERENCE.md)
- [Embedding & Retrieval](./EMBEDDING_AND_RETRIEVAL.md)
- [Runbook](./RUNBOOK.md)
