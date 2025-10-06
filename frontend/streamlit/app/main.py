from datetime import datetime
from pathlib import Path
import sys

PARENT = Path(__file__).resolve().parents[1]  # .../frontend/streamlit
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

import streamlit as st
from app_config.env import get_config
from services.api_client import APIClient
from services import storage
from services import auth_session
from state.session import init_session
from app.views import chat as view_chat
from app.views import status as view_status

cfg = get_config()
st.set_page_config(page_title=cfg["ASSISTANT_TITLE"], layout="wide", page_icon="🤖")

init_session()
st.session_state.config_cache = cfg

# Inicializa gestor de cookies (si está disponible)
auth_session.get_cookie_manager()

# Notificación diferida de feedback
if st.session_state.get("last_feedback_ok"):
    st.toast("Thanks for your feedback!", icon="✅")
    st.session_state["last_feedback_ok"] = False

# Asegura admin y usuarios en storage
storage.ensure_admin(cfg["AUTH_STORAGE_DIR"])

# Auto-login por cookie si aplica
if not st.session_state.authenticated and cfg.get("SESSION_SECRET"):
    token = auth_session.get_cookie(cfg["SESSION_COOKIE_NAME"])
    username = auth_session.verify_token(token, cfg["SESSION_SECRET"])
    if username:
        st.session_state.authenticated = True
        st.session_state.auth_user = username
    elif token:
        auth_session.delete_cookie(cfg["SESSION_COOKIE_NAME"])

# Sidebar: login y utilidades
with st.sidebar:
    st.markdown(f"## {cfg['ASSISTANT_TITLE']}")

    if not st.session_state.authenticated:
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

        remember_supported = bool(cfg.get("SESSION_SECRET")) and auth_session.cookies_available()
        remember_help = None
        if not remember_supported:
            remember_help = "Set SESSION_SECRET and install extra-streamlit-components to enable remember me."
        remember_me = st.checkbox(
            "Remember me",
            value=False,
            disabled=not remember_supported,
            help=remember_help,
        )

        if st.button("Log In"):
            users = storage.load_users(cfg["AUTH_STORAGE_DIR"])
            hashed = storage.hash_password(password)
            if username in users and users[username] == hashed:
                st.session_state.authenticated = True
                st.session_state.auth_user = username

                if remember_me and remember_supported:
                    token = auth_session.issue_token(
                        username,
                        cfg["SESSION_TTL_MIN"],
                        cfg["SESSION_SECRET"],
                    )
                    auth_session.set_cookie(
                        cfg["SESSION_COOKIE_NAME"],
                        token,
                        max_age=max(1, cfg["SESSION_TTL_MIN"]) * 60,
                    )
                else:
                    auth_session.delete_cookie(cfg["SESSION_COOKIE_NAME"])

                st.rerun()
            else:
                auth_session.delete_cookie(cfg["SESSION_COOKIE_NAME"])
                st.error("Invalid username or password.")
    else:
        st.success(f"Logged in as **{st.session_state.auth_user}**")

        with st.expander("Account Settings", expanded=False):
            with st.form("change_password_form", clear_on_submit=True):
                current_pw = st.text_input("Current password", type="password")
                new_pw = st.text_input("New password", type="password")
                confirm_pw = st.text_input("Confirm password", type="password")
                change_pw = st.form_submit_button("Update Password")

            if change_pw:
                users = storage.load_users(cfg["AUTH_STORAGE_DIR"])
                stored_hash = users.get(st.session_state.auth_user)
                if stored_hash != storage.hash_password(current_pw):
                    st.error("Current password is incorrect.")
                elif new_pw != confirm_pw:
                    st.error("New passwords do not match.")
                elif not new_pw:
                    st.error("New password cannot be empty.")
                else:
                    users[st.session_state.auth_user] = storage.hash_password(new_pw)
                    storage.save_users(cfg["AUTH_STORAGE_DIR"], users)
                    st.success("Password updated successfully.")

        st.divider()
        st.markdown("### Feedback")
        with st.form("general_feedback_form", clear_on_submit=True):
            feedback_text = st.text_area("Share your feedback")
            submit_feedback = st.form_submit_button("Submit Feedback")

        if submit_feedback:
            feedback_text_clean = feedback_text.strip()
            if not feedback_text_clean:
                st.warning("Please enter feedback before submitting.")
            else:
                record = {
                    "username": st.session_state.auth_user,
                    "feedback": feedback_text_clean,
                    "ts": datetime.utcnow().isoformat(timespec="seconds"),
                }
                try:
                    storage.append_feedback(cfg["FEEDBACK_STORAGE_DIR"], record)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Failed to save feedback: {exc}")
                else:
                    st.session_state["last_feedback_ok"] = True
                    st.rerun()

        if st.button("Log Out"):
            auth_session.delete_cookie(cfg["SESSION_COOKIE_NAME"])
            st.session_state.authenticated = False
            st.session_state.auth_user = None
            st.session_state.history = []
            st.session_state.metadata = []
            st.session_state.feedback_mode = {}
            st.session_state.health_status = None
            st.session_state.config_cache = {}
            st.session_state.last_feedback_ok = False
            st.rerun()

# Bloquea app si no está autenticado
if not st.session_state.authenticated:
    st.stop()

# Navegación principal
tab = st.sidebar.radio("Navigation", ["Assistant", "Status"])

# Cliente del backend
client = APIClient(cfg["BACKEND_API_BASE"], timeout=cfg["REQUEST_TIMEOUT"])

if tab == "Assistant":
    view_chat.render(client, cfg["ASSISTANT_TITLE"], cfg["FEEDBACK_STORAGE_DIR"])
elif tab == "Status":
    view_status.render(client)
