from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

import streamlit as st

from app.components.feedback_table import derive_feedback_item, render_feedback_table
from app.services.feedback_api import list_feedback
from app.services.api_client import ApiError
from app.state import feedback_filters


def _to_iso(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


def _has_client_filters(filters: Dict[str, Any]) -> bool:
    return any(
        bool(filters.get(name))
        for name in ("search_text", "user_filter")
    ) or (filters.get("mode_filter") not in {"all", "", None})


def render(_api_client) -> None:
    st.header("Feedback History")
    filters = feedback_filters.get_filters()

    rating_options = [
        ("All", "all"),
        ("👍 Like", "like"),
        ("👎 Dislike", "dislike"),
    ]
    mode_options = [
        ("All modes", "all"),
        ("rag", "rag"),
        ("hybrid", "hybrid"),
        ("fallback", "fallback"),
        ("n/a", "n/a"),
    ]

    with st.form("feedback_filters_form"):
        col_dates, col_rating, col_text = st.columns([1.2, 1, 1.2])

        date_from_input = col_dates.date_input(
            "Date from",
            value=feedback_filters.to_date(filters["date_from"]),
            format="YYYY-MM-DD",
        )
        date_to_input = col_dates.date_input(
            "Date to",
            value=feedback_filters.to_date(filters["date_to"]),
            format="YYYY-MM-DD",
        )

        rating_choice = col_rating.selectbox(
            "Rating",
            rating_options,
            index=next(
                (idx for idx, (_, value) in enumerate(rating_options) if value == filters["rating_filter"]),
                0,
            ),
        )
        mode_choice = col_rating.selectbox(
            "Mode",
            mode_options,
            index=next(
                (idx for idx, (_, value) in enumerate(mode_options) if value == filters["mode_filter"]),
                0,
            ),
        )

        user_input = col_text.text_input("User or session", value=filters["user_filter"])
        search_input = col_text.text_input("Search question/comment", value=filters["search_text"])
        admin_raw_toggle = col_text.checkbox(
            "Admin raw JSON",
            value=filters["admin_raw_toggle"],
            help="Show raw JSON tab in row details.",
        )

        col_apply, col_clear, _ = st.columns([1, 1, 6])
        submitted = col_apply.form_submit_button("Apply")
        cleared = col_clear.form_submit_button("Clear filters")

    if cleared:
        feedback_filters.clear_filters()
        st.rerun()

    if submitted:
        feedback_filters.set_filters(
            date_from=_to_iso(date_from_input),
            date_to=_to_iso(date_to_input),
            rating_filter=rating_choice[1],
            mode_filter=mode_choice[1],
            user_filter=user_input.strip(),
            search_text=search_input.strip(),
            admin_raw_toggle=admin_raw_toggle,
        )
        feedback_filters.reset_pagination()
        st.rerun()

    filters = feedback_filters.get_filters()
    page = int(filters["page"] or 0)
    page_size = int(filters["page_size"] or 25)
    offset = page * page_size

    placeholder = st.empty()
    placeholder.info("Loading feedback...")
    try:
        with st.spinner("Loading feedback"):
            payload = list_feedback(
                limit=page_size,
                offset=offset,
                date_from=filters["date_from"],
                date_to=filters["date_to"],
                rating=filters["rating_filter"],
                user=filters["user_filter"],
            )
    except ApiError as err:
        placeholder.empty()
        st.error("Failed to load feedback.")
        if err.details:
            st.caption(str(err.details))
        if st.button("Retry", key="feedback_retry"):
            st.rerun()
        return

    placeholder.empty()

    raw_items: List[Dict[str, Any]] = payload.get("items", []) if isinstance(payload, dict) else []
    derived_items = [derive_feedback_item(item) for item in raw_items]

    filtered_rows: List[Dict[str, Any]] = []
    search_term = (filters.get("search_text") or "").strip().lower()
    user_term = (filters.get("user_filter") or "").strip().lower()
    rating_filter = filters.get("rating_filter") or "all"
    mode_filter = (filters.get("mode_filter") or "all").lower()

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

    st.subheader("Key metrics")
    kpi_cols = st.columns(4)
    kpi_cols[0].metric("Total feedback", visible_total)
    kpi_cols[1].metric("👍 Like %", f"{like_pct:.1f}%", delta=f"{like_count}")
    kpi_cols[2].metric("👎 Dislike %", f"{dislike_pct:.1f}%", delta=f"{dislike_count}")
    kpi_cols[3].write("**Modes**\n" + " | ".join(f"{mode}: {count}" for mode, count in mode_counts.items()))

    has_prev = page > 0
    if isinstance(total_remote, int):
        has_next = (page + 1) * page_size < total_remote
    else:
        has_next = len(raw_items) == page_size

    nav_prev, nav_page, nav_next = st.columns([1, 3, 1])
    if nav_prev.button("Prev", disabled=not has_prev, key="feedback_prev"):
        feedback_filters.set_filters(page=max(page - 1, 0))
        st.rerun()
    nav_page.caption(f"Page {page + 1} | Page size {page_size}")
    if nav_next.button("Next", disabled=not has_next, key="feedback_next"):
        feedback_filters.set_filters(page=page + 1)
        st.rerun()

    if not filtered_rows:
        st.info("No feedback found. Try clearing filters.")
        return

    table_result = render_feedback_table(
        filtered_raw_items,
        admin_raw_toggle=filters["admin_raw_toggle"],
        derived_items=filtered_rows,
        selection_key="admin_feedback",
    )

    csv_rows = table_result.get("csv_rows", [])
    action_cols = st.columns([1, 1, 6])
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
        action_cols[0].download_button(
            "Export CSV",
            data=csv_buffer.getvalue().encode("utf-8-sig"),
            file_name="feedback-page.csv",
            mime="text/csv",
        )
    else:
        action_cols[0].button("Export CSV", disabled=True)

    if action_cols[1].button("Refresh", key="feedback_refresh"):
        st.rerun()
