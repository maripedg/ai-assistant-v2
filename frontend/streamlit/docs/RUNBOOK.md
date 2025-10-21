# Runbook

Overview

- Operational guide for starting, stopping, and troubleshooting the Streamlit frontend locally.

Start/Stop

```bash
cd frontend/streamlit
streamlit run app/main.py --server.port $FRONTEND_PORT
```

```powershell
cd frontend/streamlit
streamlit run app/main.py --server.port $env:FRONTEND_PORT
```

- Stop with Ctrl+C in the terminal.

Common Issues

- Port conflicts: set FRONTEND_PORT in .env to a free port.
- Missing env vars: copy .env.example to .env and set BACKEND_API_BASE.
- Cookies not persisting: install extra-streamlit-components and set SESSION_SECRET.
- Cached state: use the “Rerun” button or restart the server; optionally clear .streamlit cache folder if present.

Logs & Artifacts

- Artifacts can be written to artifacts-frontend/.
- Feedback and users live under data/; remove files to reset local state.

Quick Links

- Index: ./INDEX.md
- Configuration: ./CONFIGURATION.md

## Smoke Tests (Users & Feedback)

### Local auth
- Set `AUTH_MODE=local`.
- Login with a known local user (password required). Expect success and role to appear in header.

### DB auth (temporary)
- Set `AUTH_MODE=db`.
- Login with an email that exists in backend (`/api/v1/users/`). Expect info banner “DB auth without password verification (temporary)”.

### Admin Users page
- With role `admin`, open Users (Admin).
- Create a user, edit name/role/status, suspend and (optionally) hard‑delete.
- In local mode, change password; in db mode, expect provider message.

### Feedback thumbs
- In Chat, click 👍 and 👎 on an answer.
- `FEEDBACK_MODE=local`: verify local JSON/CSV updated under `FEEDBACK_STORAGE_DIR`.
- `FEEDBACK_MODE=db`: verify entry in `/api/v1/feedback/`.
- With `DUAL_WRITE_FEEDBACK=true`, verify both; if one fails, UI shows a warning.

## Troubleshooting

- 404 not_found (Users/Feedback): Check ID/email filters; refresh list; ensure resource exists.
- 409 conflict / already exists (Create user): Email must be unique. Adjust and retry.
- 422 validation_error: Inspect response details; fix invalid fields (role/status/email format).
- Auth (DB mode) accepts wrong password: Expected in temporary DB mode (email‑only). See `AUTH_MODE=db` note in Configuration.
- Dual‑write warnings: When `DUAL_WRITE_FEEDBACK=true`, one backend can fail independently. Check network/API logs; local write should still persist.
- Remember me not working: Ensure `SESSION_SECRET` and `SESSION_TTL_MIN` set. Clear cookies and retry.
