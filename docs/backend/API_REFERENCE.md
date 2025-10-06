# API Reference

The FastAPI application in [backend/app/main.py](../../backend/app/main.py) exposes two public endpoints via routers located in [backend/app/routers](../../backend/app/routers).

## `GET /healthz`
- **Router**: [backend/app/routers/health.py](../../backend/app/routers/health.py)
- **Purpose**: Surface readiness of embeddings and chat providers.
- **Request**: No body or parameters.
- **Response** (`200 OK`):
  ```json
  {
    "ok": false,
    "services": {
      "embeddings": "down (ValueError)",
      "llm_primary": "up",
      "llm_fallback": "down (Timeout)"
    }
  }
  ```
  `services` entries are built from `backend.app.deps.health_probe()`, which wraps provider configuration validation and lightweight client calls.
- **Failure Modes**: The handler never raises; it downgrades probe errors into human-readable reasons and sets `ok=false`. HTTP status remains 200 even when providers are unavailable.

## `POST /chat`
- **Router**: [backend/app/routers/chat.py](../../backend/app/routers/chat.py)
- **Purpose**: Retrieve relevant vector chunks and synthesize an answer with OCI LLMs.
- **Request Model** ([backend/app/models/chat.py](../../backend/app/models/chat.py)):
  ```json
  {
    "question": "How do I reset my fiber modem?"
  }
  ```
- **Processing Pipeline**:
  1. Ensures singleton providers are initialised through `backend.app.deps`.
  2. Calls `RetrievalService.answer(question)` with vector, primary LLM, fallback LLM, and `settings.app` config.
  3. Sets `X-Answer-Mode` response header to the resolved mode (`rag`, `hybrid`, or `fallback`).
- **Response Model**:
  ```json
  {
    "question": "How do I reset my fiber modem?",
    "answer": "Hold the reset button for 10 seconds...",
    "answer2": null,
    "answer3": null,
    "retrieved_chunks_metadata": [
      {
        "text": "Step 1: Power cycle the modem...",
        "source": "C:/docs/fiber_manual.pdf",
        "doc_id": "fiber_modem_reset",
        "chunk_id": "fiber_modem_reset_chunk_1",
        "raw_score": 0.62,
        "similarity": 0.81
      }
    ],
    "mode": "rag",
    "sources_used": "all",
    "used_chunks": [
      {
        "chunk_id": "fiber_modem_reset_chunk_1",
        "source": "C:/docs/fiber_manual.pdf",
        "score": 0.81,
        "snippet": "Step 1: Power cycle the modem..."
      }
    ],
    "decision_explain": {
      "score_mode": "normalized",
      "distance": "dot_product",
      "max_similarity": 0.81,
      "threshold_low": 0.2,
      "threshold_high": 0.45,
      "top_k": 12,
      "short_query_active": false,
      "mode": "rag",
      "effective_query": "How do I reset my fiber modem?",
      "used_llm": "primary"
    }
  }
  ```
- **Error Handling**: FastAPI validation rejects invalid payloads with `422 Unprocessable Entity`. Internal errors in providers propagate as `500` unless intercepted by custom middleware (none included by default).
- **Notes**:
  - `answer2` and `answer3` are reserved for multi-answer modes but currently remain `null` (see `RetrievalService._build_response`).
  - `retrieved_chunks_metadata` echoes raw scores from Oracle before filtering; `used_chunks` reflects the prompt context actually passed to the LLM after dedupe and size checks.
  - If no chunks meet thresholds or the primary model returns an empty string, the fallback LLM generates the answer and `mode` is forced to `fallback`.
