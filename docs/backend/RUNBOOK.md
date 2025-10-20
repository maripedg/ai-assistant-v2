# Runbook

Operational guide for running and supporting the AI Assistant backend.

## Daily Checks
- Call `GET /healthz` (see [API_REFERENCE.md](./API_REFERENCE.md)) and confirm `ok=true`. Investigate any `down (...)` reasons reported.
- Inspect `sanitizer.log` if sanitization is enabled; unexpected spikes in redactions may indicate noisy inputs.
- Review OCI and Oracle service dashboards for quota usage or maintenance windows.

## Embedding Job Promotion
1. **Pre-flight**:
   - Validate `.env` credentials for Oracle and OCI.
   - Ensure target profile exists in `config/app.yaml` and that the alias name points to the current production table.
2. **Execute job**:
   ```bash
   python -m backend.batch.cli embed \
     --manifest <path/to/manifest.jsonl> \
     --profile <profile_name> \
     --update-alias \
     --evaluate backend/ingest/golden_queries.yaml
   ```
   Monitor stdout for sanitizer counters, batch progress, and evaluation metrics.
3. **Post-check**:
   - Re-run `/healthz`; expect all providers to be `up`.
   - Fire representative `/chat` queries and confirm answers reference the new content.
   - Archive the job summary (stdout) alongside the manifest used for auditing.

## Rolling Back an Index
- Identify the previous physical table name (e.g., `MY_DEMO_v1`).
- Connect with `oracledb` or use the embed job helpers:
  ```python
  from backend.providers.oracle_vs.index_admin import ensure_alias
  conn = ...  # create via oracledb.connect
  ensure_alias(conn, 'MY_DEMO', 'MY_DEMO_v1')
  ```
- Re-run `/healthz` to confirm connectivity; the API automatically reads through the alias.

## Handling Sanitization Alerts
- Locate offending documents via the `doc_id` key in `sanitizer.log`.
- Adjust pattern packs (`config/sanitize/*.patterns.json`) or update allowlists to balance recall vs. false positives.
- Redeploy pattern changes by restarting ingestion runs (the module caches configs per profile but reloads on new processes).

## OCI & Oracle Troubleshooting
- **Region mismatch warnings**: Update OCI profile (`oci/config`) or `providers.yaml` endpoint to align. Warnings are emitted once per combination by `backend.app.deps`.
- **Authentication failures**: `validate_startup` prints the reason (`config error`, HTTP status). Check file permissions on the OCI config or expired API keys.
- **LLM generation parameters**: On startup, `validate_startup` prints effective generation params per LLM (primary/fallback). Use this to confirm `max_tokens`, `temperature`, `top_p`, etc. are applied as configured.
- **Vector table schema drift**: `ensure_index_table` raises when dimensions differ. Drop and recreate the physical table or rebuild embeddings with the expected vector dimension.

## Monitoring Ideas
- Capture `decision_explain` from `/chat` responses to observe score distributions and fallback rates.
- Track embed job metrics (`docs`, `chunks`, `inserted`, `skipped`, `errors`, `hit_rate`, `mrr`) per run.
- Alert when `sanitizer.log` records unexpected label types indicating new PII forms.

## TODO
- Automate alias rotation with change-management scripts to avoid manual SQL during rollbacks.
- Define explicit SLOs (e.g., max fallback rate, minimum hit rate) and wire probes into a monitoring system.
# Runbook

## Purpose
Operational guidance for day‑2 activities: health checks, reindexing, key rotation, and troubleshooting.

## Components / Architecture
- FastAPI service at `backend/app/main.py`
- Probes via `backend/app/deps.py`
- Ingestion job at `backend/batch/embed_job.py`

## Parameters & Env
- See [Config](./CONFIG_REFERENCE.md) for env variables.
- Ensure `OCI_CONFIG_*` and DB credentials are current after rotations.

## Examples

Health check:
```bash
curl -s http://localhost:8000/healthz | jq .
```

Reindex (re‑embed documents):
```bash
python -m backend.batch.cli embed \
  --manifest backend/ingest/examples/my_pdfs.jsonl \
  --profile legacy_profile \
  --update-alias
```

Day‑2 checks (ingestion quality):
- Counts by content_type and chunk totals:
  ```bash
  python -m backend.batch.cli embed --manifest backend/ingest/examples/my_pdfs.jsonl --profile legacy_profile --dry-run
  # Inspect TYPE/ITEMS/CHUNKS summary
  ```
- Watch for high percentage of empty items (should be near 0% except scanned PDFs).
- For PDFs with no text, plan OCR as a separate step.

Rotate keys (OCI API key):
1. Update `~/.oci/config` key_file and fingerprint.
2. Update `backend/.env` `OCI_CONFIG_PATH`/`OCI_CONFIG_PROFILE` if needed.
3. Restart service.

Reload config:
```bash
# Restart app (dev)
pkill -f uvicorn || true
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Retry failed jobs:
- If using queues (TODO if queue is enabled): re‑enqueue or re‑run CLI with the same manifest.

## Ops Notes
- OracleVS alias must exist and project METADATA as CLOB: recreated via `--update-alias`.
- Keep provider `distance` consistent with retrieval settings to avoid score drift.
- Logs: Uvicorn access suppressed; backend service logs decisions and probes.

## See also
- [Embedding & Retrieval](./EMBEDDING_AND_RETRIEVAL.md)
- [Ingestion & Manifests](./INGESTION_AND_MANIFESTS.md)
