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
- Cached state: use the Rerun button or restart the server; optionally clear the .streamlit cache folder if present.

Logs & Artifacts

- Artifacts can be written to artifacts-frontend/.
- Feedback and users live under data/; remove files to reset local state.

Quick Links

- Index: ./INDEX.md
- Configuration: ./CONFIGURATION.md

## Admin Documents View

- 415 unsupported media type: confirm backend ALLOW_MIME or override, update ALLOWED_MIME_HINT copy, and retry with a supported format.
- 413 payload too large: check backend MAX_UPLOAD_MB; remind user of size limit and split the document if needed.
- 422 unknown profile: ensure DEFAULT_PROFILE matches backend configuration (app.ingest_profiles or embeddings profiles).
- 404 upload not found: the upload cache expired; ask user to re-upload before creating a job.
- 409 conflict on job create: another job references one of the uploads; wait for completion or clear the local list.
- After a successful job creation, prompt the operator to clear the staged list to avoid resubmitting the same upload_ids.

## Troubleshooting Chat UI

1. Set `DEBUG_CHAT_UI=true` and `DEBUG_CHAT_UI_STRICT=true` in `.env`.
2. Run `streamlit run app/main.py` (ensure the backend is reachable).
3. Watch the terminal for structured logs such as `API:chat_response`, `answer_section:enter`, `render_primary_answer:markdown_rendered`, and `answer_section:exit`.
4. In the chat view, expand **Debug: Raw payload** to inspect the backend response and confirm which answer fields were populated.
5. Confirm the visible **DEBUG — Answer Box** appears above the rendered Markdown when strict mode is enabled.

## Feedback Troubleshooting

- If you encounter `name 'datetime' is not defined`, ensure the patched frontend with the UTC helper import is deployed.
- If the backend rejects `created_at`, the client now retries without it. Verify server timestamps or allow the backend to set the creation time.

## Smoke Tests (Chat Answer Panel)

- Mock or force responses for rag, hybrid, and fallback modes via the status view or local fixtures.
- rag: expect the mode chip with the book icon, Sources list visible, evidence count > 0, and confidence High or Medium.
- hybrid: expect compass icon, Sources list only when sim_max >= threshold_low, and the info note when sources_used == partial.
- fallback: expect life ring icon, no Sources section, and the Why panel explaining the fallback decision.
- Verify the summary line format "Mode: {mode}. Evidence: {n}. Confidence: {bucket}." for each mode.
- Confirm the confidence bar and tooltip text match STYLEGUIDE guidance.

## Smoke Tests (Users & Feedback)

### Local auth
- Set AUTH_MODE=local.
- Login with a known local user (password required). Expect success and role to appear in header.

### DB auth (temporary)
- Set AUTH_MODE=db.
- Login with an email that exists in backend (/api/v1/users/). Expect info banner "DB auth without password verification (temporary)".

### Admin Users page
- With role admin, open Users (Admin).
- Create a user, edit name/role/status, suspend, and optionally hard-delete.
- In local mode, change password; in db mode, expect provider message.

### Feedback thumbs
- In Chat, click thumbs up and thumbs down on an answer.
- FEEDBACK_MODE=local: verify local JSON/CSV updated under FEEDBACK_STORAGE_DIR.
- FEEDBACK_MODE=db: verify entry in /api/v1/feedback/.
- With DUAL_WRITE_FEEDBACK=true, verify both; if one fails, UI shows a warning.

## Troubleshooting

- 404 not_found (Users/Feedback): Check ID/email filters; refresh list; ensure resource exists.
- 409 conflict / already exists (Create user): Email must be unique. Adjust and retry.
- 422 validation_error: Inspect response details; fix invalid fields (role/status/email format).
- Auth (DB mode) accepts wrong password: Expected in temporary DB mode (email only). See AUTH_MODE=db note in Configuration.
- Dual-write warnings: When DUAL_WRITE_FEEDBACK=true, one backend can fail independently. Check network/API logs; local write should still persist.
- Remember me not working: Ensure SESSION_SECRET and SESSION_TTL_MIN set. Clear cookies and retry.
