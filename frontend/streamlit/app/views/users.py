import streamlit as st
from typing import Any, Dict, List, Optional

from app_config.env import get_config
from app.services import storage
from app.services.api_client import ApiError  # for error mapping
from app.services import api_client


def _auth_mode() -> str:
    return str(get_config().get("AUTH_MODE", "local")).lower()


def _is_admin() -> bool:
    return bool(st.session_state.get("is_authenticated") or st.session_state.get("authenticated")) and (
        (st.session_state.get("role") or "user") == "admin"
    )


def _handle_error(exc: Exception) -> None:
    if isinstance(exc, ApiError):
        if exc.status == 404:
            st.error("User not found")
        elif exc.status == 409:
            st.warning("Email already exists")
        elif exc.status == 422:
            st.error("Validation error")
        else:
            st.error(f"API error ({exc.status})")
    else:
        st.error(str(exc))


def _list_users(email: Optional[str], status: Optional[str], limit: int, offset: int) -> List[Dict[str, Any]]:
    mode = _auth_mode()
    if mode == "db":
        try:
            return api_client.users_list(email=email, status=status, limit=limit, offset=offset) or []
        except Exception as exc:  # noqa: BLE001
            _handle_error(exc)
            return []
    # local: synthesize from JSON creds
    users_map = storage.load_users(get_config()["AUTH_STORAGE_DIR"]) or {}
    items: List[Dict[str, Any]] = []
    for idx, mail in enumerate(sorted(users_map.keys())):
        if email and email.lower() not in mail.lower():
            continue
        items.append({
            "id": idx + 1,
            "email": mail,
            "name": None,
            "role": "user",
            "status": "active",
            "created_at": None,
        })
    return items[offset: offset + limit]


def render(client) -> None:  # client reserved for future use
    if not _is_admin():
        st.error("Admin only")
        return

    # Flash message renderer
    def _render_flash_users() -> None:
        flash = st.session_state.get("flash_users")
        if not flash:
            return
        kind = (flash.get("type") or "success").lower()
        msg = flash.get("msg") or ""
        if kind == "error":
            st.error(msg)
        elif kind == "warning":
            st.warning(msg)
        else:
            st.success(msg)
        if st.button("Dismiss", key="flash_users_dismiss"):
            st.session_state.pop("flash_users", None)
            st.rerun()

    _render_flash_users()

    tabs = st.tabs(["Create", "Users list"])

    # --- Create tab ---
    with tabs[0]:
        st.subheader("Create user")
        with st.form("create_user_form", clear_on_submit=True):
            email = st.text_input("Email", key="create_email")
            name = st.text_input("Name", key="create_name")
            role = st.selectbox("Role", options=["user", "admin"], index=0, key="create_role")
            pw = st.text_input("Password", type="password", key="create_pw")
            submit = st.form_submit_button("Create")

        if submit:
            if not email:
                st.warning("Email is required")
            else:
                payload: Dict[str, Any] = {"email": email, "name": name or "", "role": role}
                if pw:
                    payload["password"] = pw
                try:
                    # In DB mode, ensure password is passed through; storage passes arbitrary fields in DB mode
                    storage.auth_create_user(payload)
                except Exception as exc:  # noqa: BLE001
                    _handle_error(exc)
                else:
                    st.toast("User created")
                    # Set flash and switch to list view with filter applied
                    st.session_state["flash_users"] = {"type": "success", "msg": f"User {email} created"}
                    st.session_state["users_active_tab"] = "list"
                    st.session_state["users_email_filter"] = email
                    st.session_state["users_list_highlight"] = email
                    st.session_state["users_refresh"] = st.session_state.get("users_refresh", 0) + 1
                    st.rerun()

    # --- List tab ---
    with tabs[1]:
        st.subheader("Users")
        colf1, colf2, colf3 = st.columns([2, 1, 1])
        with colf1:
            f_email_default = st.session_state.get("users_email_filter", "")
            f_email = st.text_input("Filter by email", value=f_email_default)
        with colf2:
            f_status = st.selectbox("Status", options=["any", "active", "suspended"], index=0)
            status_val = None if f_status == "any" else f_status
        with colf3:
            limit = st.selectbox("Page size", options=[10, 20, 50], index=1)

        offset_key = "users_offset"
        offset = st.session_state.get(offset_key, 0)
        items = _list_users(f_email or None, status_val, int(limit), int(offset))

        highlight = st.session_state.get("users_list_highlight")
        for u in items:
            cols = st.columns([0.5, 3, 2, 1, 1, 1])
            if highlight and u.get("email") == highlight:
                cols[0].markdown("✅")
            else:
                cols[0].markdown("")
            cols[1].write(u.get("email"))
            cols[2].write(u.get("name") or "-")
            cols[3].write(u.get("role") or "user")
            cols[4].write(u.get("status") or "-")
            with cols[5]:
                with st.expander("Actions"):
                    nid = u.get("id")
                    new_name = st.text_input("Name", value=u.get("name") or "", key=f"nm_{nid}")
                    new_role = st.selectbox("Role", options=["user", "admin"], index=0 if (u.get("role") or "user") == "user" else 1, key=f"rl_{nid}")
                    new_status = st.selectbox("Status", options=["active", "suspended"], index=0 if (u.get("status") or "active") == "active" else 1, key=f"st_{nid}")
                    if st.button("Save", key=f"sv_{nid}"):
                        try:
                            storage.auth_patch_user(int(nid), {"name": new_name or None, "role": new_role, "status": new_status})
                        except Exception as exc:  # noqa: BLE001
                            _handle_error(exc)
                        else:
                            st.success("User updated")
                            st.session_state["users_refresh"] = st.session_state.get("users_refresh", 0) + 1
                            st.rerun()

                    # Suspend/Activate
                    if (u.get("status") or "active") == "active":
                        if st.button("Suspend", key=f"sp_{nid}"):
                            try:
                                storage.auth_patch_user(int(nid), {"status": "suspended"})
                            except Exception as exc:  # noqa: BLE001
                                _handle_error(exc)
                            else:
                                st.success("User suspended")
                                st.rerun()
                    else:
                        if st.button("Activate", key=f"ac_{nid}"):
                            try:
                                storage.auth_patch_user(int(nid), {"status": "active"})
                            except Exception as exc:  # noqa: BLE001
                                _handle_error(exc)
                            else:
                                st.success("User activated")
                                st.rerun()

                    # Delete
                    hard = st.checkbox("Hard delete", key=f"hd_{nid}")
                    if st.button("Delete", key=f"dl_{nid}"):
                        try:
                            storage.auth_delete_user(int(nid), hard=bool(hard))
                        except Exception as exc:  # noqa: BLE001
                            _handle_error(exc)
                        else:
                            st.success("User deleted")
                            st.rerun()

                    # Change password
                    if _auth_mode() == "local":
                        newpw = st.text_input("New password", type="password", key=f"np_{nid}")
                        if st.button("Update Password", key=f"up_{nid}"):
                            try:
                                # Local change by id unsupported; inform user to use account settings
                                st.info("Use Account Settings to update your password in local mode.")
                            except Exception as exc:  # noqa: BLE001
                                _handle_error(exc)
                    else:
                        st.info("Password is managed by backend/auth provider")

        # Pagination controls
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Prev") and offset > 0:
                st.session_state[offset_key] = max(0, offset - int(limit))
                st.rerun()
        with c2:
            if st.button("Next") and len(items) == int(limit):
                st.session_state[offset_key] = offset + int(limit)
                st.rerun()

        # Clear one-time highlight after first render
        if "users_list_highlight" in st.session_state and st.session_state.get("users_list_highlight"):
            st.session_state.pop("users_list_highlight", None)
