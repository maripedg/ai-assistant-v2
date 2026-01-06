# Setup & Run
Last updated: 2025-11-07

## Prerequisites
- Python 3.11+ with build tools for `oracledb`.
- Oracle Database 23ai reachable from the host (vector-enabled schema, write access to `AUTH_LOGINS`, `CHAT_SESSIONS`, `CHAT_INTERACTIONS` if `USAGE_LOG_ENABLED` will be turned on).
- OCI account with access to Generative AI embeddings/chat models plus a configured CLI profile in `oci/config`.
- Optional: `uv`/`venv` for virtual environments.

## Environment
1. Copy `.env` template:
   ```bash
   cp backend/.env.example backend/.env
   ```
2. Fill in `DB_*`, `OCI_*`, `JWT_SECRET`, `SESSION_SECRET`, `MAX_UPLOAD_MB`, and any sanitization or usage logging flags (`USAGE_LOG_ENABLED`).
3. For DOCX image extraction, ensure `RAG_ASSETS_DIR` (default `./data/rag-assets`) is writable and set `DOCX_EXTRACT_IMAGES`, `DOCX_INLINE_FIGURE_PLACEHOLDERS`, and `DOCX_FIGURE_CHUNKS` as needed.
4. Verify `oci/config` (or custom `OCI_CONFIG_PATH`) contains the profile referenced by `OCI_CONFIG_PROFILE`.

## Install Dependencies
```bash
python -m venv backend/.venv
source backend/.venv/bin/activate  # Windows: backend\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r backend/requirements.txt
```

## Run the API
```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```
On startup the process prints OCI/Oracle probe results (`validate_startup(True)`). Address failures (credentials, network, alias missing) before continuing.

## Smoke Test
```bash
curl -s http://localhost:8000/healthz | jq .
curl -s -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"List hybrid gates."}' | jq .
```
If `features.users_api`/`feedback_api` are enabled, test `/api/v1/auth/login` and `/api/v1/feedback/` to confirm JWTs and sanitization work end-to-end.

## Database Prep
- Create the alias view referenced by `embeddings.alias.name` or run an embed job with `--update-alias` to generate it.
- Grant the service account permission to create tables (embedding) and insert into usage logging tables if enabled.

## Usage Logging Toggle
When `USAGE_LOG_ENABLED=true`, ensure the tables `AUTH_LOGINS`, `CHAT_SESSIONS`, `CHAT_INTERACTIONS` exist. The service inserts `RESP_MODE` and similarity metrics for each `/chat` call, so capacity planning should include these writes.

## Troubleshooting
- **`ModuleNotFoundError: oracledb`** – Install Oracle Instant Client libraries and re-run `pip install oracledb`.
- **`Alias view ... not found`** – Ensure at least one embed job created `<alias>_vN` and run `ensure_alias()` to point the alias to that table.
- **`Upload exceeds maximum size ...`** – Raise `MAX_UPLOAD_MB` or split documents; the frontend mirrors this limit.
- **`SharePoint sync failed` (502)** – Check the SharePoint microservice logs and the `SP_*` env values in `app.yaml`.

## Next Steps
- Populate the index: follow [Ingestion & Manifests](./INGESTION_AND_MANIFESTS.md).
- Review `backend/docs` for endpoint-specific payloads before integrating additional clients.
- Wire monitors: expose `/healthz` to your platform probes and alert on fallback spikes using the `RESP_MODE` column in `CHAT_INTERACTIONS`.
