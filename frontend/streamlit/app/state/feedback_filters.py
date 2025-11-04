from __future__ import annotations

from datetime import date
from typing import Any, Dict

import streamlit as st

_PREFIX = "feedback_filters_"

_DEFAULTS: Dict[str, Any] = {
    "date_from": None,
    "date_to": None,
    "rating_filter": "all",
    "mode_filter": "all",
    "user_filter": "",
    "search_text": "",
    "page": 0,
    "page_size": 25,
    "admin_raw_toggle": False,
}


def _key(name: str) -> str:
    return f"{_PREFIX}{name}"


def _ensure_defaults() -> None:
    for name, value in _DEFAULTS.items():
        st.session_state.setdefault(_key(name), value)


def get_filters() -> Dict[str, Any]:
    _ensure_defaults()
    return {name: st.session_state[_key(name)] for name in _DEFAULTS}


def set_filters(**updates: Any) -> None:
    _ensure_defaults()
    for name, value in updates.items():
        if name in _DEFAULTS:
            st.session_state[_key(name)] = value


def clear_filters() -> None:
    for name, value in _DEFAULTS.items():
        st.session_state[_key(name)] = value


def reset_pagination() -> None:
    set_filters(page=0)


def to_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None

