from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Optional

import streamlit as st

try:
    import extra_streamlit_components as stx
except ImportError:  # pragma: no cover - optional dependency
    stx = None

_COOKIE_MANAGER = None
_FALLBACK_STORE_KEY = "_cookie_fallback_store"


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


def get_cookie_manager():
    global _COOKIE_MANAGER
    if stx is None:
        return None
    if _COOKIE_MANAGER is None:
        _COOKIE_MANAGER = stx.CookieManager()
    return _COOKIE_MANAGER


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
        manager.delete(name)
    store = _get_fallback_store()
    store.pop(name, None)

