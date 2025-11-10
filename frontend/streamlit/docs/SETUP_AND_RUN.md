# Setup & Run
Last updated: 2025-11-07

## Prerequisites
- Python 3.10+ (matching `requirements.txt`).
- A running backend (FastAPI) accessible via `BACKEND_API_BASE`.
- `.env` populated with at least `BACKEND_API_BASE`, `FRONTEND_PORT`, `AUTH_MODE`, `FEEDBACK_MODE`, and `SESSION_SECRET` (if cookies are desired).

## Install & Launch
```bash
cd frontend/streamlit
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/main.py --server.port ${FRONTEND_PORT:-8501}
```

PowerShell:
```powershell
cd frontend/streamlit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app/main.py --server.port $env:FRONTEND_PORT
```

## Environment File
```bash
cp .env.example .env
```
Fill in:
- `BACKEND_API_BASE` / `FRONTEND_BASE_URL`
- `AUTH_MODE`, `FEEDBACK_MODE`, `DUAL_WRITE_FEEDBACK`
- `DEFAULT_PROFILE`, `UPLOAD_CONCURRENCY`
- `AUTH_ENABLED` (set to `true` in environments where the backend requires bearer tokens)
- Optional `FEEDBACK_DEFAULT_USER_ID` for local auth scenarios

Restart Streamlit after edits; `get_config()` caches values per run.

## Quick Validation
1. Load `http://localhost:8501/`.
2. Log in as a user defined in either local storage or the backend.
3. Send a chat question; open browser dev tools to confirm the response carries `X-Answer-Mode`.
4. Visit **Feedback (Admin)** (if `role=admin`), toggle filters, and open the “Raw JSON” tab to verify all metadata fields (question, answer_preview, mode, client, ui_version) are present.
5. Visit **Documents & Embeddings (Admin)**, upload a TXT file, and confirm `upload_id` surfaces in the UI.

## Useful Commands
```bash
# Format/lint/tests
black .
flake8
pytest -q
```

## Troubleshooting
- **Backend unreachable**: Confirm `BACKEND_API_BASE` matches the FastAPI port. The Status tab calls `/healthz`; use it to verify connectivity.
- **401/403 on admin calls**: Ensure you logged in via the backend (`AUTH_MODE=db`) and that `auth_session` stored a JWT. When `AUTH_ENABLED=true`, the UI refuses to call `/api/v1/*` without `Authorization`.
- **Uploads blocked by CORS**: Make sure the backend CORS list includes the Streamlit origin. If running via tunnels (ngrok), add that URL to `allow_origins`.
