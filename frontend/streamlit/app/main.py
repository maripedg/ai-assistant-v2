from datetime import datetime
from pathlib import Path
import sys

PARENT = Path(__file__).resolve().parents[1]  # .../frontend/streamlit
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

import streamlit as st
from app_config.env import get_config
from app.services.api_client import APIClient
from app.services import storage
from app.services import auth_session
from state.session import init_session, get_bool, get_str
from app.views import chat as view_chat
from app.views import status as view_status
from app.views import users as view_users
from app.views.admin import feedback as view_admin_feedback
from app.views.admin import embeddings as view_admin_embeddings

cfg = get_config()
st.set_page_config(page_title=cfg["ASSISTANT_TITLE"], layout="wide")

init_session()
st.session_state.config_cache = cfg

# Initialize cookie manager and attempt session restore
auth_session.get_cookie_manager()
auth_session.try_restore_session_from_token()

# Deferred feedback toast
if st.session_state.get("last_feedback_ok"):
    st.toast("Thanks for your feedback!", icon="✅")
    st.session_state["last_feedback_ok"] = False

# Ensure local admin user exists when using local auth
storage.ensure_admin(cfg["AUTH_STORAGE_DIR"])

# Sidebar: login / account
with st.sidebar:
    st.markdown(f"## {cfg['ASSISTANT_TITLE']}")

    if not (get_bool("is_authenticated") or get_bool("authenticated")):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        remember_supported = bool(cfg.get("SESSION_SECRET")) and auth_session.cookies_available()
        remember_help = None if remember_supported else "Set SESSION_SECRET to enable remember me."
        remember_me = st.checkbox(
            "Remember me",
            value=False,
            disabled=not remember_supported,
            help=remember_help,
        )

        st.caption("Use your email and password.")

        if st.button("Log In"):
            ok, reason = auth_session.login(email, password, remember_me)
            if ok:
                st.toast(f"Welcome, {email}")
                st.rerun()
            else:
                st.error(reason or "Login failed")
    else:
        display_user = get_str("username") or get_str("auth_user")
        role = get_str("role", "user")
        st.success(f"Logged in as **{display_user}** ({role})")

        with st.expander("Account Settings", expanded=False):
            with st.form("change_password_form", clear_on_submit=True):
                current_pw = st.text_input("Current password", type="password")
                new_pw = st.text_input("New password", type="password")
                confirm_pw = st.text_input("Confirm password", type="password")
                change_pw = st.form_submit_button("Update Password")

            if change_pw:
                if new_pw != confirm_pw:
                    st.error("New passwords do not match.")
                elif not new_pw:
                    st.error("New password cannot be empty.")
                else:
                    # Local change is handled by previous UI in legacy mode; here we inform the user.
                    st.info("Password change is managed by the backend in DB mode.")

        st.divider()
        st.markdown("### Feedback")
        with st.form("general_feedback_form", clear_on_submit=True):
            feedback_text = st.text_area("Share your feedback")
            submit_feedback = st.form_submit_button("Submit Feedback")

        if submit_feedback:
            feedback_text_clean = (feedback_text or "").strip()
            if not feedback_text_clean:
                st.warning("Please enter feedback before submitting.")
            else:
                record = {
                    "username": display_user,
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
            auth_session.logout()
            # Clear transient state
            st.session_state.history = []
            st.session_state.metadata = []
            st.session_state.feedback_mode = {}
            st.session_state.health_status = None
            st.session_state.config_cache = {}
            st.session_state.last_feedback_ok = False
            st.rerun()

# Auth guard
if not (get_bool("is_authenticated") or get_bool("authenticated")):
    st.stop()

# Navigation
tabs = {
    "Assistant": "assistant",
    "Status": "status",
}
if get_str("role", "user") == "admin":
    tabs["Documents & Embeddings (Admin)"] = "admin_embeddings"
    tabs["Users (Admin)"] = "users_admin"
    tabs["Feedback (Admin)"] = "admin_feedback"

tab_labels = list(tabs.keys())
default_label = st.session_state.pop("nav_target", None)
default_index = tab_labels.index(default_label) if default_label in tab_labels else 0
tab_label = st.sidebar.radio("Navigation", tab_labels, index=default_index, key="nav_selection")
tab = tabs[tab_label]

# Backend client
# Admin uploads use app.services.api_client helpers that prefer FRONTEND_BASE_URL over BACKEND_API_BASE.
client = APIClient(cfg["BACKEND_API_BASE"], timeout=cfg["REQUEST_TIMEOUT"])

if tab == "assistant":
    view_chat.render(client, cfg["ASSISTANT_TITLE"], cfg["FEEDBACK_STORAGE_DIR"])
elif tab == "status":
    view_status.render(client)
elif tab == "users_admin":
    view_users.render(client)
elif tab == "admin_embeddings":
    view_admin_embeddings.render(client)
elif tab == "admin_feedback":
    view_admin_feedback.render(client)
