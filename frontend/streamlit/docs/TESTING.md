# Testing
Last updated: 2025-11-07

## Automated Tests
- Runner: `pytest -q` from `frontend/streamlit`.
- Focus: logic in `app/services/*`, `app/state/*`, and lightweight view helpers.
- Mock backend calls with `responses` or `monkeypatch` to avoid real HTTP requests.

## Manual Checklist

### Chat & Feedback
1. Log in (DB mode) and send a question. Confirm the UI renders mode chips and the browser shows `X-Answer-Mode` in network headers.
2. Leave thumbs feedback **with** a comment, then another **without** a comment. Verify:
   - Payload includes `user_id` (inspect backend logs or use a proxy).
   - Comments remain blank when not provided—no auto-fill from the answer text.
3. Set `DEBUG_CHAT_UI=true` to ensure the raw payload expander and console logs still work.

### Feedback History View
1. Open **Feedback (Admin)** with an admin user.
2. Apply filters: date range, `rating=like`, `mode=hybrid`, text search. Ensure KPIs and counts update accordingly.
3. Hover the Q/A column to view the combined tooltip and confirm truncated text respects the style guide.
4. Toggle “Admin raw JSON view” (`fb_admin_raw`). The third tab (“Raw JSON”) should appear within the row expander, showing the backend payload verbatim.
5. Page through results (Prev/Next) and verify `fb_page` updates while filters persist.

### Documents & Embeddings
1. Upload representative files (PDF, TXT, DOCX). Each should reach “Uploaded” and expose `upload_id`.
2. Attempt unsupported MIME (`.exe`) to confirm the 415 toast. Repeat with an oversized file to trigger the 413 message.
3. Create a dry-run job (no alias update). Capture `job_id` and confirm the toast copy.

### Users (Admin)
1. Create a new user and edit role/status.
2. Attempt to re-create the same email to confirm 409 handling.

### State Isolation
1. Open multiple tabs (Chat + Feedback History). Ensure filters set in Feedback History do not alter chat state and vice versa. Inspect `st.session_state` for only `fb_*` mutations.

## Troubleshooting Tips
- Use `pytest -k <pattern>` to focus on service modules when iterating quickly.
- Set `DEBUG_FEEDBACK_UI=1` to print tracked `fb_*` keys if filters behave unexpectedly.
- When verifying JWT propagation, run Streamlit with `streamlit run app/main.py --server.headless true` and inspect logs emitted by `app.services.api_client`.
