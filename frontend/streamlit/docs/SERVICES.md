# Services

Overview

- The services/ layer encapsulates backend HTTP calls, auth cookie management, and local storage for users/feedback.

Modules

- api_client.py
  - health_check() -> (ok: bool, payload: dict)
  - chat(question: str) -> normalized dict with fields answer, retrieved_chunks_metadata, mode, used_chunks, decision_explain, sources_used, and raw.
    - The chat view shows a mode chip (icon + tooltip) derived from the mode field.
    - A decision summary line reports the mode, evidence count (len(used_chunks) or decision_explain.kept_n), and confidence computed from decision_explain.sim_max against supplied thresholds.
    - Evidence cards are populated from used_chunks when the backend thresholds allow. Fallback mode, failed gates, or low similarity hide sources by design.
    - A Why this mode? panel renders the reasoning from decision_explain, including thresholds and gate state, with an optional confidence bar.
  - Uses requests with REQUEST_TIMEOUT from config.

- auth_session.py
  - issue_token(username, ttl_min, secret) -> signed token
  - verify_token(token, secret) -> username|None
  - Cookie helpers set_cookie/get_cookie/delete_cookie using extra-streamlit-components when available, else session fallback.

- storage.py
  - Users: hash_password(), load_users(), save_users(), ensure_admin()
  - Feedback: append_feedback(), append_icon_feedback(); files under FEEDBACK_STORAGE_DIR

Request/Response Shapes

- POST /chat expects {question: str}
- Response is normalized by api_client.chat() and rendered directly by views/chat.

Answer Rendering & Debug

- The chat view displays the first non-empty answer value (answer -> answer2 -> answer3) as Markdown above the decision/evidence panel.
- When all answer fields are empty, the UI shows the placeholder text "No answer content returned."
- If the normalized payload includes `question`, the frontend shows it as a right-aligned user bubble above the answer—no additional fields are required from the backend.
- Set `DEBUG_CHAT_UI=true` in `.env` to emit verbose console logs (`API:chat_response`, `UI:render_start`, etc.) and show debug expanders with the raw payload inside the chat view.
- Disable the flag for normal operation to keep the console quiet and hide the debug UI.

Quick Links

- Index: ./INDEX.md
- Architecture: ./ARCHITECTURE.md
