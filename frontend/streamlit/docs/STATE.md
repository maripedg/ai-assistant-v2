# State
Last updated: 2025-11-07

Streamlit stores UI state in `st.session_state`. We centralise defaults in two places:
- `state/session.py` – global keys (auth flags, chat history, upload trackers).
- `app/state/*.py` – feature-specific namespaces (currently `feedback_filters`).

## Global Keys (`state/session.py`)
| Key | Description |
| --- | --- |
| `is_authenticated` / `authenticated` | Boolean flags for login state (new + legacy). |
| `username`, `auth_user`, `role` | Current principal metadata. |
| `chat_history` | List of chat turns rendered in `app/views/chat`. |
| `feedback_mode` | Map of message index ➜ `{icon, needs_reset}` for thumbs UI. |
| `health_status` | Cached `/healthz` payload for the Status tab. |
| `config_cache` | Result of `get_config()` to avoid repeated reads. |
| `last_feedback_ok` | Toggles the toast shown after general feedback submission. |
| `profile`, `tags`, `lang_hint`, `update_alias`, `evaluate`, `upload_concurrency`, `files`, `last_job_id`, `job_snapshot` | Documents & Embeddings admin state. |
| `_chat_css_injected` | Guards CSS injection so we only insert styles once per session. |

## Feedback History Keys
The admin Feedback view (see `app/views/admin/feedback.py`) uses namespaced keys to avoid collisions:
- `fb_date_from`, `fb_date_to` – ISO dates bound to Streamlit date pickers.
- `fb_rating` – `"all" | "like" | "dislike"`.
- `fb_mode` – `"all" | "rag" | "hybrid" | "fallback" | "n/a"`.
- `fb_user_filter` – Substring filter for `user_id` or `session_id`.
- `fb_search` – Text search over question + comment.
- `fb_page`, `fb_page_size` – Remote pagination hints while the view still applies client-side filters.
- `fb_admin_raw` – Toggles the “Raw JSON” tab in `app/components/feedback_table.py`.
- `fb_table` – Selection key passed into the table component so multiple users can inspect different rows without colliding.

Helpers in `app/state/feedback_filters.py` manage an alternative namespace (`feedback_filters_*`) for future refactors; both sets of keys follow the same defaults.

## Patterns
- Always call `init_session()` before reading/writing new keys; it seeds defaults from `state/session.DEFAULT_KEYS`.
- Prefix feature-specific keys (e.g., `fb_`, `upload_`) to avoid collisions when Streamlit reruns scripts.
- When storing complex objects (lists/dicts) mutate copies or reassign the key to trigger Streamlit reruns.
