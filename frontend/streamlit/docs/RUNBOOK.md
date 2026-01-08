# Runbook
Last updated: 2025-11-07

## Start / Stop
```bash
cd frontend/streamlit
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app/main.py --server.port $FRONTEND_PORT
```

Windows PowerShell:
```powershell
cd frontend/streamlit
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app/main.py --server.port $env:FRONTEND_PORT
```

Stop with `Ctrl+C`. Clear Streamlit’s cache (`.streamlit/`) if layout glitches persist.

## Common Issues
| Symptom | Action |
| --- | --- |
| Port in use | Set `FRONTEND_PORT` to an open port before starting Streamlit. |
| Missing env values | Copy `.env.example` to `.env` and populate `BACKEND_API_BASE`, `AUTH_MODE`, etc. |
| Cookies not persisting | Install `extra-streamlit-components` and set `SESSION_SECRET`. Without it, “Remember me” falls back to in-memory storage. |
| CORS errors on uploads | Ensure backend `server.cors.allow_origins` includes the Streamlit origin. Verify `FRONTEND_BASE_URL`/`BACKEND_API_BASE` align. |
| `Authorization header missing` banners | Login again to refresh the JWT. When `AUTH_ENABLED=true`, admin calls are blocked until `auth_session.get_auth_headers()` returns a token. |
| Feedback History shows stale rows | Use the “Reset filters” button, confirm `fb_admin_raw` toggle state, and refresh. Debug state (set `DEBUG_FEEDBACK_UI=1`) to inspect `fb_*` keys. |
| Figures not rendering inline | Verify `[FIGURE:<id>]` appears in the answer and `retrieved_chunks_metadata` has matching `figure_id`/`image_ref`. Ensure `RAG_ASSETS_DIR` points to the images. Set `CHAT_FIGURES_DEBUG=1` to view resolved paths and existence in the UI. |

## Smoke Tests
1. **Login** – Authenticate as admin. Expect sidebar to show role and the “Documents & Embeddings” / “Feedback (Admin)” tabs.
2. **Chat** – Send a test question. Confirm that the answer renders along with the mode chip, `X-Answer-Mode` header is visible in browser dev tools, and thumbs feedback works with and without comments.
3. **Feedback History** – Apply filters (`rating=like`, `mode=hybrid`, search text). Ensure Q/A column truncates properly, KPIs update, and toggling “Raw JSON” adds the third tab.
4. **Documents & Embeddings** – Upload a small TXT file, confirm status transitions to Uploaded, and create a dry-run job (`update_alias=false`). Expect toast with `job_id`.
5. **Users (Admin)** – List users, update a role, and attempt to create a duplicate email (should produce 409 in the UI).

## Logs & Artifacts
- Local feedback and credentials live under `data/`; delete the files to reset local state when running in `local` modes.
- Optional automation scripts belong under `scripts/` (see [SCRIPTS.md](./SCRIPTS.md)).

## Troubleshooting Chat UI
1. Set `DEBUG_CHAT_UI=true` (and `DEBUG_CHAT_UI_STRICT=true` for extra panels).
2. Inspect terminal output for `API:chat_response` summaries.
3. Use the “Debug: Raw payload” expander inside chat history to verify `mode`, `decision_explain`, and chunk counts.

## Troubleshooting Admin Calls
- 4xx errors are surfaced inline. Use the toast copy plus backend logs to identify whether it was a 415/413/422/409/404.
- For CORS or TLS errors, re-run the requests via `curl` using the same base URL to isolate whether it’s a browser-only issue.
- Refresh JWTs with the **Refresh** button in sidebar if the backend rotates secrets; stale tokens lead to 401s until a new login occurs.
