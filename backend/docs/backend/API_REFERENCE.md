# API Reference (Overview)
Last updated: 2025-12-30

This overview covers the two public, unauthenticated endpoints that every deployment exposes. Detailed request/response contracts for the `/api/v1/*` namespace live under [backend/docs](../../backend/docs/).

## GET /healthz
- **Purpose**: Surface readiness of embeddings and LLM providers.
- **Request**: No body or query params. Set `Accept: application/json`.
- **Response** (`200 OK`):
```json
{
  "ok": true,
  "services": {
    "embeddings": "up",
    "llm_primary": "up",
    "llm_fallback": "up"
  }
}
```
- **Failure modes**: Dependency failures are downgraded into `services.<name> = "down (<reason>)"`. HTTP status remains 200 to keep probes simple.

## POST /chat
- **Purpose**: Ask a question and receive a retrieval-augmented answer.
- **Headers**: `Content-Type: application/json`. Optional `X-RAG-Domain: <domain_key>` routes retrieval to `embeddings.domains.<key>.alias_name` (default is `embeddings.alias.name`). The backend replies with `X-Answer-Mode` (`rag`, `hybrid`, or `fallback`) mirroring `response.mode`.
- **Body schema** ([backend/app/models/chat.py](../../backend/app/models/chat.py)):
```json
{ "question": "How do I reset my fiber modem?" }
```
- **Response** (`200 OK`):
```json
{
  "question": "How do I reset my fiber modem?",
  "answer": "Hold the reset button for 10 seconds.",
  "answer2": null,
  "answer3": null,
  "retrieved_chunks_metadata": [
    {
      "chunk_id": "doc-1#0",
      "source": "fiber_manual.pdf",
      "similarity": 0.81,
      "text": "Step 1: Power cycle..."
    }
  ],
  "used_chunks": [
    {
      "chunk_id": "doc-1#0",
      "source": "fiber_manual.pdf",
      "score": 0.81,
      "snippet": "Step 1: Power cycle..."
    }
  ],
  "mode": "rag",
  "sources_used": "all",
  "decision_explain": {
    "score_mode": "normalized",
    "distance": "dot_product",
    "max_similarity": 0.81,
    "threshold_low": 0.25,
    "threshold_high": 0.55,
    "short_query_active": false,
    "top_k": 12,
    "used_llm": "primary",
    "retrieval_target": "MY_DEMO"
  }
}
```
- **Error handling**: FastAPI rejects invalid payloads with `422 Unprocessable Entity`. Unknown `X-RAG-Domain` values return `400 Bad Request`. Upstream errors (Oracle, OCI) bubble up as `500` unless caught by an API gateway. The UI should treat empty `answer` + `mode=fallback` as a graceful degradation.

## Auth Notes
- `/chat` and `/healthz` remain open for simplicity. All `/api/v1/*` endpoints expect `Authorization: Bearer <JWT>` when `AUTH_ENABLED=true` (see [backend/docs/API_AUTH.md](../../backend/docs/API_AUTH.md)).
- JWT claims include `sub` (user id), `email`, `role`, and `status`. Frontends derive `user_id` for feedback submissions from this `sub`.

## Example Usage
```bash
curl -s http://localhost:8000/healthz | jq .

curl -s -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"List hybrid decision gates."}' | jq .
```

For upload/ingest, user, auth, and feedback APIs see [backend/docs/API_REFERENCE.md](../../backend/docs/API_REFERENCE.md) plus the generated HTTP/Postman assets under `backend/docs/http` and `backend/docs/postman`.
