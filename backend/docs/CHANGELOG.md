# Backend Changelog

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
