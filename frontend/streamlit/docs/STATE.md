# State

Overview

- Session state lives in state/session.py and governs authentication flags, chat history, metadata, and transient UI flags.

Session Keys (state/session.py)

- authenticated: bool, login status
- auth_user: str|None, username
- history: list[(role, content)] for chat
- metadata: list, retrieved chunks metadata
- feedback_mode: dict[int -> {icon, needs_reset?}] per answer index
- health_status: health payload from backend status view
- config_cache: snapshot of get_config() for quick access
- last_feedback_ok: flag to show toast after feedback submit

Lifecycle

- init_session() ensures default keys at startup.
- add_history(role, content) appends chat rows (used by views/chat).

Patterns for New Keys

- Initialize defaults in DEFAULT_KEYS to avoid KeyError.
- Use st.session_state.setdefault(key, default) when read lazily.
- Avoid storing large objects or binary data; keep fast-to-serialize content.

Quick Links

- Index: ./INDEX.md
- Architecture: ./ARCHITECTURE.md

