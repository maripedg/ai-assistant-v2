# Services

Overview

- The services/ layer encapsulates backend HTTP calls, auth cookie management, and local storage for users/feedback.

Modules

- api_client.py
  - health_check() -> (ok: bool, payload: dict)
  - chat(question: str) -> normalized dict with answer, answer2, retrieved_chunks_metadata, mode, used_chunks, decision_explain, raw
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

Quick Links

- Index: ./INDEX.md
- Architecture: ./ARCHITECTURE.md

