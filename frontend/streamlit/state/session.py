import streamlit as st

DEFAULT_KEYS = {
    "authenticated": False,
    "auth_user": None,
    "history": [],
    "metadata": [],
    "feedback_mode": {},
    "health_status": None,
    "config_cache": {},
    "last_feedback_ok": False,
}


def init_session():
    for k, v in DEFAULT_KEYS.items():
        if k not in st.session_state:
            st.session_state[k] = v


def add_history(role: str, content: str):
    st.session_state.history.append((role, content))

