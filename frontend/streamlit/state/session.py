import streamlit as st

AUTH_KEYS = {
    "is_authenticated": False,
    "username": "",
    "role": "user",
    # Back-compat keys used elsewhere
    "authenticated": False,
    "auth_user": None,
}

DEFAULT_KEYS = {
    **AUTH_KEYS,
    "history": [],
    "metadata": [],
    "feedback_mode": {},
    "health_status": None,
    "config_cache": {},
    "last_feedback_ok": False,
    "profile": "legacy_profile",
    "tags": "",
    "lang_hint": "auto",
    "update_alias": False,
    "evaluate": False,
    "upload_concurrency": 3,
    "files": [],
    "last_job_id": "",
    "job_snapshot": {},
    "assistant_meta": [],
}


def init_session():
    for k, v in DEFAULT_KEYS.items():
        if k not in st.session_state:
            st.session_state[k] = v


def add_history(role: str, content: str):
    st.session_state.history.append((role, content))


def get_bool(key: str, default: bool = False) -> bool:
    return bool(st.session_state.get(key, default))


def get_str(key: str, default: str = "") -> str:
    v = st.session_state.get(key)
    return v if isinstance(v, str) else default


def set_kv(key: str, value):
    st.session_state[key] = value


def clear_auth_state():
    for k in ("is_authenticated", "username", "role", "authenticated", "auth_user"):
        if k in st.session_state:
            st.session_state[k] = DEFAULT_KEYS[k]
