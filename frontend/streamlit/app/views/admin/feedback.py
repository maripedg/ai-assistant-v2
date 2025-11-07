from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import streamlit as st

from app.components.feedback_table import derive_feedback_item, render_feedback_table
from app.services.feedback_api import list_feedback
from app.services.api_client import ApiError

_FB_DEFAULTS = {
    "fb_date_from": None,
    "fb_date_to": None,
    "fb_rating": "all",
    "fb_mode": "all",
    "fb_user_filter": "",
    "fb_search": "",
    "fb_admin_raw": False,
    "fb_page": 0,
    "fb_page_size": 25,
}


def _to_iso(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


def _has_client_filters(filters: Dict[str, Any]) -> bool:
    return any(
        bool(filters.get(name))
        for name in ("search", "user")
    ) or (filters.get("mode") not in {"all", "", None})


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _init_state() -> None:
    for key, default in _FB_DEFAULTS.items():
        st.session_state.setdefault(key, default)


def _reset_filters(preserve_admin: bool = True) -> None:
    admin_value = bool(st.session_state.get("fb_admin_raw", False)) if preserve_admin else False
    st.session_state["fb_date_from"] = None
    st.session_state["fb_date_to"] = None
    st.session_state["fb_rating"] = "all"
    st.session_state["fb_mode"] = "all"
    st.session_state["fb_user_filter"] = ""
    st.session_state["fb_search"] = ""
    st.session_state["fb_page"] = 0
    st.session_state["fb_page_size"] = _FB_DEFAULTS["fb_page_size"]
    st.session_state["fb_admin_raw"] = admin_value


def _get_filters() -> Dict[str, Any]:
    return {
        "date_from": st.session_state.get("fb_date_from"),
        "date_to": st.session_state.get("fb_date_to"),
        "rating": st.session_state.get("fb_rating", "all"),
        "mode": st.session_state.get("fb_mode", "all"),
        "user": st.session_state.get("fb_user_filter", ""),
        "search": st.session_state.get("fb_search", ""),
        "admin_raw": st.session_state.get("fb_admin_raw", False),
        "page": int(st.session_state.get("fb_page", 0) or 0),
        "page_size": int(st.session_state.get("fb_page_size", _FB_DEFAULTS["fb_page_size"])),
    }


def _clear_legacy_keys() -> None:
    for key in list(st.session_state.keys()):
        if key.startswith("feedback_table"):
            del st.session_state[key]


def _show_debug_keys() -> None:
    if os.getenv("DEBUG_FEEDBACK_UI") == "1":
        debug_keys = [k for k in st.session_state.keys() if k.startswith("fb_") or k.startswith("feedback_table")]
        st.caption(f"[fb_debug] keys: {debug_keys}")


def render(_api_client) -> None:
    _clear_legacy_keys()
    _init_state()
    filters = _get_filters()

    st.header("Feedback History")
    rating_options = ["all", "like", "dislike"]
    rating_labels = {"all": "All", "like": "Like", "dislike": "Dislike"}
    mode_options = ["all", "rag", "hybrid", "fallback", "n/a"]
    mode_labels = {
        "all": "All modes",
        "rag": "rag",
        "hybrid": "hybrid",
        "fallback": "fallback",
        "n/a": "n/a",
    }

    with st.form("fb_filters_form"):
        col_dates, col_rating, col_text = st.columns([1.2, 1, 1.2])

        date_from_input = col_dates.date_input(
            "Date from",
            value=_parse_date(filters["date_from"]),
            format="YYYY-MM-DD",
            key="fb_date_from",
        )
        date_to_input = col_dates.date_input(
            "Date to",
            value=_parse_date(filters["date_to"]),
            format="YYYY-MM-DD",
            key="fb_date_to",
        )

        rating_index = rating_options.index(filters["rating"]) if filters["rating"] in rating_options else 0
        rating_choice = col_rating.selectbox(
            "Rating",
            rating_options,
            index=rating_index,
            format_func=lambda value: rating_labels[value],
            key="fb_rating",
        )
        mode_index = mode_options.index(filters["mode"]) if filters["mode"] in mode_options else 0
        mode_choice = col_rating.selectbox(
            "Mode",
            mode_options,
            index=mode_index,
            format_func=lambda value: mode_labels[value],
            key="fb_mode",
        )

        user_input = col_text.text_input("User or session", value=filters["user"], key="fb_user_filter")
        search_input = col_text.text_input("Search question/comment", value=filters["search"], key="fb_search")
        admin_raw_toggle = col_text.checkbox(
            "Admin raw JSON",
            value=bool(filters["admin_raw"]),
            help="Show raw JSON tab in row details.",
            key="fb_admin_raw",
        )

        col_apply, col_clear, _ = st.columns([1, 1, 6])
        submitted = col_apply.form_submit_button("Apply")
        cleared = col_clear.form_submit_button("Clear filters")

    if cleared:
        _reset_filters()
        st.rerun()

    if submitted:
        st.session_state["fb_date_from"] = _parse_date(date_from_input)
        st.session_state["fb_date_to"] = _parse_date(date_to_input)
        st.session_state["fb_rating"] = rating_choice
        st.session_state["fb_mode"] = mode_choice
        st.session_state["fb_user_filter"] = user_input.strip()
        st.session_state["fb_search"] = search_input.strip()
        st.session_state["fb_admin_raw"] = bool(admin_raw_toggle)
        st.session_state["fb_page"] = 0
        st.rerun()

    filters = _get_filters()
    page = filters["page"]
    page_size = filters["page_size"]
    offset = page * page_size

    placeholder = st.empty()
    placeholder.info("Loading feedback...")
    try:
        with st.spinner("Loading feedback"):
            payload = list_feedback(
                limit=page_size,
                offset=offset,
                date_from=_to_iso(filters["date_from"]),
                date_to=_to_iso(filters["date_to"]),
                rating=filters["rating"],
                user=filters["user"],
            )
    except ApiError as err:
        placeholder.empty()
        st.error("Failed to load feedback.")
        if err.details:
            st.caption(str(err.details))
        if st.button("Retry", key="fb_retry"):
            st.rerun()
        _show_debug_keys()
        return

    placeholder.empty()

    raw_items: List[Dict[str, Any]] = payload.get("items", []) if isinstance(payload, dict) else []
    derived_items = [derive_feedback_item(item) for item in raw_items]

    filtered_rows: List[Dict[str, Any]] = []
    search_term = (filters.get("search") or "").strip().lower()
    user_term = (filters.get("user") or "").strip().lower()
    rating_filter = filters.get("rating") or "all"
    mode_filter = (filters.get("mode") or "all").lower()

    for row in derived_items:
        include = True
        if rating_filter in {"like", "dislike"} and row["rating_label"] != rating_filter:
            include = False
        if include and mode_filter not in {"all", ""}:
            if mode_filter == "n/a":
                include = row["mode_normalized"] in {"n/a", "na", ""}
            else:
                include = row["mode_normalized"] == mode_filter
        if include and user_term:
            user_raw = str(row["raw"].get("user_id") or "").lower()
            session_raw = str(row["raw"].get("session_id") or "").lower()
            include = user_term in user_raw or user_term in session_raw
        if include and search_term:
            include = search_term in row["search_blob"]
        if include:
            filtered_rows.append(row)

    filtered_rows.sort(key=lambda r: r["created_at_sort"], reverse=True)
    filtered_raw_items = [row["raw"] for row in filtered_rows]

    total_remote = None
    total_candidate = payload.get("total") if isinstance(payload, dict) else None
    try:
        total_remote = int(total_candidate) if total_candidate is not None else None
    except (TypeError, ValueError):
        total_remote = None

    like_count = sum(1 for row in filtered_rows if row["rating_label"] == "like")
    dislike_count = sum(1 for row in filtered_rows if row["rating_label"] == "dislike")
    total_count = like_count + dislike_count
    like_pct = (like_count / total_count * 100) if total_count else 0.0
    dislike_pct = (dislike_count / total_count * 100) if total_count else 0.0
    mode_counts = {
        "rag": sum(1 for row in filtered_rows if row["mode_normalized"] == "rag"),
        "hybrid": sum(1 for row in filtered_rows if row["mode_normalized"] == "hybrid"),
        "fallback": sum(1 for row in filtered_rows if row["mode_normalized"] == "fallback"),
        "n/a": sum(1 for row in filtered_rows if row["mode_normalized"] in {"n/a", "na", ""}),
    }
    visible_total = total_remote if (isinstance(total_remote, int) and not _has_client_filters(filters)) else len(filtered_rows)

    st.subheader("Feedback KPIs")
    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Total feedback", visible_total)
    kpi_cols[1].metric("Like %", f"{like_pct:.1f}%", delta=like_count)
    kpi_cols[2].metric("Dislike %", f"{dislike_pct:.1f}%", delta=dislike_count)
    kpi_cols[3].write("**Modes**\n" + " | ".join(f"{mode}: {count}" for mode, count in mode_counts.items()))

    has_prev = page > 0
    if isinstance(total_remote, int):
        has_next = (page + 1) * page_size < total_remote
    else:
        has_next = len(raw_items) == page_size

    nav_prev, nav_page, nav_next = st.columns([1, 3, 1])
    if nav_prev.button("Prev", disabled=not has_prev, key="fb_prev"):
        st.session_state["fb_page"] = max(page - 1, 0)
        st.rerun()
    nav_page.caption(f"Page {page + 1} | Page size {page_size}")
    if nav_next.button("Next", disabled=not has_next, key="fb_next"):
        st.session_state["fb_page"] = page + 1
        st.rerun()

    if not filtered_rows:
        st.info("No feedback found. Try clearing filters.")
        _show_debug_keys()
        return

    table_result = render_feedback_table(
        filtered_raw_items,
        admin_raw_toggle=bool(filters["admin_raw"]),
        derived_items=filtered_rows,
        selection_key="fb_table",
    )

    csv_rows = table_result.get("csv_rows", [])
    action_cols = st.columns([1, 1, 6])
    csv_data = b""
    if csv_rows:
        csv_buffer = io.StringIO()
        fieldnames = [
            "id",
            "created_at",
            "user_id",
            "session_id",
            "session_short",
            "rating",
            "category",
            "question",
            "answer_preview",
            "comment",
            "mode",
            "client",
            "ui_version",
        ]
        writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)
        csv_buffer.seek(0)
        csv_data = csv_buffer.getvalue().encode("utf-8-sig")
    action_cols[0].download_button(
        "Export CSV",
        data=csv_data,
        file_name="feedback-page.csv",
        mime="text/csv",
        disabled=not csv_rows,
        key="fb_export_csv",
    )

    if action_cols[1].button("Refresh", key="fb_refresh"):
        st.rerun()

    _show_debug_keys()
