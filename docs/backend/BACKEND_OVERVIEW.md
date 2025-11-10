# Backend Overview
Last updated: 2025-11-07

## Purpose
Summarise how the FastAPI backend ingests documents, answers questions with retrieval‑augmented generation (RAG), and records operational telemetry. Use this page as the jump‑off point to [Setup & Run](./SETUP_AND_RUN.md), [API docs](../backend/API_REFERENCE.md), and the [Runbook](./RUNBOOK.md).

## Architecture Summary
- **FastAPI application** – [backend/app/main.py](../../backend/app/main.py) wires routers (`/chat`, `/api/v1/*`, `/healthz`) plus optional debug endpoints. Startup runs `validate_startup()` to probe OCI and Oracle dependencies.
- **Dependency factories** – [backend/app/deps.py](../../backend/app/deps.py) loads `.env`, `config/app.yaml`, and `config/providers.yaml`, instantiates embeddings/LLM/vector clients, and exposes settings to routers and services.
- **Retrieval core** – [backend/core/services/retrieval_service.py](../../backend/core/services/retrieval_service.py) handles similarity search, dedupe, rag/hybrid/fallback decisioning, and sets the `X-Answer-Mode` header alongside a structured `decision_explain`.
- **Persistence & repos** – `backend/core/repos/*` provide DB/JSON adapters for users and feedback. The repo factory honours `storage.dual_write` to mirror between adapters when needed.
- **Ingestion toolchain** – [backend/app/services/ingest.py](../../backend/app/services/ingest.py) plus [backend/batch/embed_job.py](../../backend/batch/embed_job.py) manage uploads, manifests, embed jobs, and alias rotation backed by Oracle Vector Search.
- **Cross-cutting utilities** – Sanitization lives in [backend/common/sanitizer.py](../../backend/common/sanitizer.py); SharePoint sync helpers, schedulers, and OCI adapters live under `backend/app/services/` and `backend/providers/`.

## Request Lifecycle (Chat)
1. **Intake** – `POST /chat` receives `{"question": "<user text>"}`.
2. **Vector search** – `RetrievalService` queries the alias view named by `embeddings.alias.name` for the top‑`k` chunks defined by the active profile.
3. **Decisioning** – Similarity thresholds (with short-question overrides) pick `rag`, `hybrid`, or `fallback`. The decision is echoed in `response.mode` and the `X-Answer-Mode` header.
4. **Context assembly** – Deduped chunks obey `hybrid.max_context_chars`, `max_chunks`, and gating rules before prompting the LLM.
5. **Generation** – The primary OCI chat model renders the answer; blank/low-confidence responses trigger the fallback model and prompt.
6. **Response** – Clients receive the question echo, up to three answer fields, `retrieved_chunks_metadata`, `used_chunks`, and a structured `decision_explain`.

## Usage Logging (Oracle)
When `USAGE_LOG_ENABLED=true`, the service records auth/session activity inside Oracle tables owned by the application schema:

| Table | Purpose | Key Columns |
| --- | --- | --- |
| `AUTH_LOGINS` | Each successful `/api/v1/auth/login`. | `LOGIN_ID`, `USER_ID`, `EMAIL`, `IP_ADDRESS`, `CREATED_AT` |
| `CHAT_SESSIONS` | Logical chat sessions keyed by JWT subject and UI session. | `SESSION_ID`, `USER_ID`, `AUTH_LOGIN_ID`, `CLIENT`, `CREATED_AT` |
| `CHAT_INTERACTIONS` | One row per `/chat` answer; captures the final decision. | `INTERACTION_ID`, `SESSION_ID`, `QUESTION`, `RESP_MODE`, `SIM_MAX`, `CREATED_AT` |

> NOTE: Table DDL/grants are managed outside this repo. Ensure they exist before enabling logging.

## Component Map
- `backend/app/routers/` – HTTP surface (auth, users, feedback, ingest, sharepoint, health, chat, debug).
- `backend/core/services/` – Retrieval, ingest orchestration, and supporting services.
- `backend/app/services/` – Upload staging, embed runner integration, SharePoint orchestration, scheduled jobs.
- `backend/providers/` – OCI embeddings/chat wrappers, OracleVS admin helpers, and vector store adapters.
- `backend/batch/` – CLI entrypoints for embedding jobs and golden-query evaluation.
- `backend/common/` – Sanitizer and other shared helpers.
- `backend/ingest/` – Manifest specs, golden query fixtures, and sample manifests.

## External Dependencies
- **OCI Generative AI** for embeddings/chat (auth via `oci` SDK, config via `providers.yaml` or env overrides).
- **Oracle Database 23ai** for vector storage, alias views, and usage logging tables (`AUTH_LOGINS`, `CHAT_SESSIONS`, `CHAT_INTERACTIONS`).
- **FastAPI stack** (`fastapi`, `uvicorn`, `pydantic`) and supporting libs (`python-dotenv`, `PyYAML`, `langchain-community`).

## Related Reading
- [Config Reference](./CONFIG_REFERENCE.md) – env keys (`DB_*`, `OCI_*`, `AUTH_*`, `USAGE_LOG_ENABLED`, upload limits).
- [Setup & Run](./SETUP_AND_RUN.md) – local bootstrap, required `.env`, Oracle prep.
- [Embedding & Retrieval](./EMBEDDING_AND_RETRIEVAL.md) – detailed mode thresholds and hybrid rules.
- [Ingestion & Manifests](./INGESTION_AND_MANIFESTS.md) – upload ➜ job ➜ alias promotion flow plus error catalogue.
- [Runbook](./RUNBOOK.md) – daily checks, smoke tests (healthz/login/chat/feedback), and rollback steps.
