from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

import streamlit as st
from app_config.env import get_config
from app.services import storage
from app.services import api_client

try:
    import extra_streamlit_components as stx
except ImportError:  # pragma: no cover - optional dependency
    stx = None


_COOKIE_MANAGER_STATE_KEY = "_cookie_manager_component"
_FALLBACK_STORE_KEY = "_cookie_fallback_store"
_API_TOKEN_KEY = "api_token"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_token(username: str, ttl_min: int, secret: str) -> str:
    if not secret:
        raise ValueError("SESSION_SECRET is required to issue tokens")
    now = int(time.time())
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + max(1, int(ttl_min)) * 60,
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return f"{_b64url(body)}.{_b64url(signature)}"


def verify_token(token: str | None, secret: str | None) -> Optional[str]:
    if not token or not secret:
        return None
    try:
        body_part, sig_part = token.split(".", 1)
    except ValueError:
        return None

    try:
        body_bytes = _b64url_decode(body_part)
        sig_bytes = _b64url_decode(sig_part)
    except (base64.binascii.Error, ValueError):
        return None

    expected_sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_sig, sig_bytes):
        return None

    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        return None
    username = payload.get("sub")
    return username if isinstance(username, str) else None


def _get_fallback_store() -> dict:
    store = st.session_state.setdefault(_FALLBACK_STORE_KEY, {})
    # purge expired entries
    now = time.time()
    expired = [key for key, item in store.items() if item.get("exp", 0) < now]
    for key in expired:
        store.pop(key, None)
    return store


def cookies_available() -> bool:
    return stx is not None or _FALLBACK_STORE_KEY in st.session_state


def _cookie_manager_key(cfg: Optional[dict]) -> str:
    name = (cfg or {}).get("SESSION_COOKIE_NAME", "assistant_session")
    return f"{name}_manager"


def get_cookie_manager():
    if stx is None:
        return None
    if _COOKIE_MANAGER_STATE_KEY not in st.session_state:
        cfg = get_config()
        st.session_state[_COOKIE_MANAGER_STATE_KEY] = stx.CookieManager(
            key=_cookie_manager_key(cfg)
        )
    return st.session_state[_COOKIE_MANAGER_STATE_KEY]


def set_cookie(name: str, value: str, max_age: int) -> None:
    manager = get_cookie_manager()
    if manager is not None:
        manager.set(name, value, max_age=max_age)
        return
    store = _get_fallback_store()
    store[name] = {"value": value, "exp": time.time() + max(1, max_age)}


def get_cookie(name: str) -> Optional[str]:
    manager = get_cookie_manager()
    if manager is not None:
        cookies = manager.get_all() or {}
        return cookies.get(name)
    store = _get_fallback_store()
    item = store.get(name)
    if not item:
        return None
    if item.get("exp", 0) < time.time():
        store.pop(name, None)
        return None
    return item.get("value")


def delete_cookie(name: str) -> None:
    manager = get_cookie_manager()
    if manager is not None:
        try:
            manager.delete(name)
        except KeyError:
            # Cookie not present; ignore
            pass
        except Exception:
            # Non-fatal: fall through to fallback store cleanup
            pass
    store = _get_fallback_store()
    store.pop(name, None)


def _api_cookie_name(cfg: Optional[dict] = None) -> str:
    cfg = cfg or get_config()
    return f"{cfg.get('SESSION_COOKIE_NAME', 'assistant_session')}_api"


# ---------------- New session helpers (mode-aware auth) ----------------
def _set_auth_state(email: str, role: str) -> None:
    st.session_state["is_authenticated"] = True
    st.session_state["username"] = email
    st.session_state["role"] = role
    st.session_state["email"] = email
    # Back-compat keys
    st.session_state["authenticated"] = True
    st.session_state["auth_user"] = email


def _clear_auth_state() -> None:
    st.session_state["is_authenticated"] = False
    st.session_state["username"] = ""
    st.session_state["role"] = "user"
    st.session_state.pop("email", None)
    st.session_state.pop("user_id", None)
    st.session_state["authenticated"] = False
    st.session_state["auth_user"] = None


def issue_session_token(username: str, role: str, ttl_min: int, secret: str) -> str:
    body = {"u": username, "r": role, "exp": int(time.time()) + max(1, int(ttl_min)) * 60}
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64url(raw)}.{_b64url(sig)}"


def verify_session_token(token: Optional[str], secret: Optional[str]) -> Optional[dict]:
    if not token or not secret:
        return None
    try:
        b, s = token.split(".", 1)
        raw = _b64url_decode(b)
        sig = _b64url_decode(s)
        exp_sig = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(exp_sig, sig):
            return None
        data = json.loads(raw.decode("utf-8"))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        u = data.get("u"); r = data.get("r", "user")
        if not isinstance(u, str):
            return None
        return {"username": u, "role": r}
    except Exception:
        return None


def try_restore_session_from_token() -> bool:
    cfg = get_config()
    token = get_cookie(cfg.get("SESSION_COOKIE_NAME", "assistant_session"))
    data = verify_session_token(token, cfg.get("SESSION_SECRET"))
    if not data:
        return False
    _set_auth_state(data["username"], data.get("role", "user"))
    api_cookie = get_cookie(_api_cookie_name(cfg))
    if api_cookie:
        st.session_state[_API_TOKEN_KEY] = api_cookie
    return True


def is_authenticated() -> bool:
    return bool(st.session_state.get("is_authenticated") or st.session_state.get("authenticated"))


def current_user() -> dict:
    return {
        "username": st.session_state.get("username") or st.session_state.get("auth_user"),
        "role": st.session_state.get("role", "user"),
    }


def require_role(role: str) -> bool:
    return is_authenticated() and (st.session_state.get("role", "user") == role)


def login(email: str, password: str, remember: bool) -> tuple[bool, Optional[str]]:
    cfg = get_config()
    mode = str(cfg.get("AUTH_MODE", "local")).lower()
    api_token: Optional[str] = None
    st.session_state["user_id"] = None
    if mode == "local":
        users = storage.load_users(cfg["AUTH_STORAGE_DIR"])
        hashed = storage.hash_password(password)
        if email not in users or users[email] != hashed:
            return False, "Invalid credentials"
        _set_auth_state(email, "user")
    else:
        # db mode: call backend auth
        try:
            resp = api_client.auth_login(email, password)
        except Exception as exc:  # noqa: BLE001
            return False, f"Login failed: {exc}"
        user = (resp or {}).get("user") or {}
        if (user.get("status") or "").lower() == "suspended":
            return False, "User suspended"
        role = user.get("role") or "user"
        _set_auth_state(user.get("email") or email, role)
        st.session_state["user_id"] = user.get("id")
        st.session_state["email"] = user.get("email") or email
        api_token = (resp or {}).get("token")

    if api_token:
        st.session_state[_API_TOKEN_KEY] = api_token
    else:
        st.session_state.pop(_API_TOKEN_KEY, None)

    if remember and cfg.get("SESSION_SECRET"):
        try:
            token = issue_session_token(email, st.session_state.get("role", "user"), cfg.get("SESSION_TTL_MIN", 1440), cfg["SESSION_SECRET"])
            set_cookie(cfg.get("SESSION_COOKIE_NAME", "assistant_session"), token, max_age=max(60, int(cfg.get("SESSION_TTL_MIN", 1440)) * 60))
            if api_token:
                set_cookie(_api_cookie_name(cfg), api_token, max_age=max(60, int(cfg.get("SESSION_TTL_MIN", 1440)) * 60))
        except Exception:
            # non-fatal
            pass
    if bool(cfg.get("DEBUG_CHAT_UI", False)) or bool(cfg.get("DEBUG_HTTP", False)):
        print("[auth] logged in user_id=", st.session_state.get("user_id"))
    return True, None


def logout() -> None:
    cfg = get_config()
    delete_cookie(cfg.get("SESSION_COOKIE_NAME", "assistant_session"))
    delete_cookie(_api_cookie_name(cfg))
    st.session_state.pop(_API_TOKEN_KEY, None)
    st.session_state.pop("chat_history", None)
    _clear_auth_state()


def get_token() -> Optional[str]:
    token = st.session_state.get(_API_TOKEN_KEY)
    if token:
        return token
    return get_cookie(_api_cookie_name())


def get_auth_headers() -> dict:
    token = get_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}

