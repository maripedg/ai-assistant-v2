# Setup & Run

## Prerequisites
- Python 3.11+ with build tooling to compile `oracledb`.
- Oracle Database 23ai (or compatible) reachable from the machine running the API.
- OCI account with access to Generative AI embeddings and chat models and a configured profile in `oci/config`.
- Optional but recommended: virtual environment manager (`venv`, `conda`, or `uv`) for dependency isolation.

## Environment Configuration
1. Copy `backend/.env.example` to `backend/.env` and fill in connection details (no secrets should be committed).
2. Ensure the OCI CLI profile referenced by `OCI_CONFIG_PROFILE` exists in `oci/config`. `backend/app/deps.py` will force `OCI_CONFIG_FILE` to `${repo}/oci/config` on import.
3. If sanitization is required, set `SANITIZE_ENABLED=on|shadow` and related knobs documented in [SANITIZATION.md](./SANITIZATION.md).

## Install Dependencies
```bash
python -m venv .venv
. .venv/Scripts/Activate  # PowerShell: .\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r backend/requirements.txt
```
The requirements file includes optional ingestion extras (`PyPDF2`, `tqdm`). Skip them only if you do not plan to run embed jobs.

## Launch the API Server
```bash
uvicorn backend.app.main:app --reload --port 8080
```
`backend/app/main.py` runs `validate_startup(True)` on import, so the terminal will display status probes for embeddings and both chat models along with retrieval configuration summaries. Address any reported failures before serving traffic.

## Running Tests
```bash
pytest backend/tests -q
```
Tests rely on fakes for most services but may skip OCI-specific adapters unless `OCI_TESTS_DISABLED` is cleared.

## Common Issues
- **`ModuleNotFoundError: oracledb`**: Install Oracle Instant Client prerequisites and rerun `pip install oracledb`. Windows users need the appropriate Instant Client ZIP on `PATH`.
- **`Alias view 'MY_DEMO' not found`**: Either run an embed job with `--update-alias` or manually create the view using [backend/providers/oracle_vs/index_admin.py](../../backend/providers/oracle_vs/index_admin.py).
- **`OCI configuration mismatch` warnings**: `backend/app/deps._warn_if_region_mismatch()` compares endpoint and config file regions; update either the endpoint URL or the profile region.
- **Empty responses**: When the primary chat model returns no text, the service falls back automatically. Check OCI quotas and model availability if fallback usage spikes.

## Next Steps
- Populate the vector index using the ingestion workflow in [INGESTION_AND_MANIFESTS.md](./INGESTION_AND_MANIFESTS.md).
- Validate retrieval quality with golden queries before exposing the assistant to users.
# Setup and Run

## Purpose
Guide to run the backend locally and in Docker, with smoke tests and troubleshooting.

## Components / Architecture
- FastAPI app at `backend/app/main.py`
- Dependencies and clients wired in `backend/app/deps.py`
- Requirements in `backend/requirements.txt`

## Parameters & Env
- Copy `backend/.env.example` to `backend/.env` and fill values.
- See [Config](./CONFIG_REFERENCE.md) for variable meanings.

## Local Setup

```bash
# 1) Create and activate venv (Python 3.11+ recommended)
python -m venv backend/.venv
.\backend\.venv\Scripts\activate  # PowerShell on Windows

# 2) Install dependencies
pip install -r backend/requirements.txt

# 3) Set env (optional if backend/.env exists)
set -a; source backend/.env 2>/dev/null || true; set +a

# 4) Run the API
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Smoke test:

```bash
curl -s http://localhost:8000/healthz | jq .
curl -s -X POST http://localhost:8000/chat -H 'Content-Type: application/json' -d '{"question":"hello"}' | jq .
```

## Docker (if applicable)
The repository includes `backend/Dockerfile` (TODO: current file is empty; add build steps if using Docker).

Example run:

```bash
docker build -t ai-assistant-backend -f backend/Dockerfile .
docker run --rm -p 8000:8000 --env-file backend/.env ai-assistant-backend
```

## Troubleshooting
- Health shows embeddings down: verify OCI config file/profile and IAM permissions. Ensure `OCI_CONFIG_PATH` points to a file and key file exists.
- Oracle vector errors: confirm alias view exists and `oraclevs` DSN/user/password are correct.
- 400 from embed service: remove empty/whitespace texts before embedding.

## See also
- [Config](./CONFIG_REFERENCE.md)
- [API Reference](./API_REFERENCE.md)
- [Runbook](./RUNBOOK.md)
