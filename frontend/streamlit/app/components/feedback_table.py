from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st


def _truncate(value: str, length: int = 80) -> str:
    if len(value) <= length:
        return value
    cutoff = max(0, length - 3)
    return value[:cutoff].rstrip() + "..."


def _escape(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _format_timestamp(raw: Optional[str]) -> Dict[str, str]:
    if not raw:
        return {"display": "n/a", "sort": ""}
    value = raw
    try:
        ts = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(ts)
        local_dt = dt.astimezone()
        return {"display": local_dt.strftime("%Y-%m-%d %H:%M:%S"), "sort": local_dt.isoformat()}
    except Exception:
        return {"display": value, "sort": value}


def derive_feedback_item(item: Dict[str, Any]) -> Dict[str, Any]:
    metadata_raw = item.get("metadata_json") or item.get("metadata")
    metadata: Dict[str, Any] = {}
    metadata_error = False
    if isinstance(metadata_raw, str) and metadata_raw.strip():
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            metadata_error = True
            metadata = {}
    question = (metadata.get("question") or "").strip()
    answer_preview = (metadata.get("answer_preview") or "").strip()
    mode = (metadata.get("mode") or "n/a").strip() or "n/a"
    mode_normalized = mode.lower()
    message_id = (metadata.get("message_id") or "").strip()
    client = (metadata.get("client") or "").strip()
    ui_version = (metadata.get("ui_version") or "").strip()
    if client and ui_version:
        client_tag = f"{client}@{ui_version}"
    elif client:
        client_tag = client
    elif ui_version:
        client_tag = f"@{ui_version}"
    else:
        client_tag = ""
    rating_value = item.get("rating")
    rating_label = "like" if str(rating_value) in {"1", "like", "+1"} or rating_value == 1 else "dislike"
    rating_icon = "👍" if rating_label == "like" else "👎"
    user_id = item.get("user_id")
    session_id = item.get("session_id") or ""
    session_short = session_id[:8] if session_id else ""
    if user_id not in (None, ""):
        user_display = str(user_id)
    else:
        user_display = "anonymous"
    if session_short:
        user_display = f"{user_display} | {session_short}"
    timestamp = _format_timestamp(item.get("created_at"))
    comment = (item.get("comment") or "").strip()
    csv_row = {
        "id": item.get("id"),
        "created_at": item.get("created_at"),
        "user_id": user_id,
        "session_id": session_id,
        "session_short": session_short,
        "rating": rating_value,
        "category": item.get("category"),
        "question": question,
        "answer_preview": answer_preview,
        "comment": comment,
        "mode": mode,
        "client": client,
        "ui_version": ui_version,
    }
    return {
        "id": item.get("id"),
        "raw": item,
        "metadata": metadata,
        "metadata_error": metadata_error,
        "question": question,
        "question_truncated": _truncate(question),
        "answer_preview": answer_preview,
        "comment": comment,
        "comment_truncated": _truncate(comment),
        "mode": mode,
        "mode_normalized": mode_normalized,
        "client_tag": client_tag,
        "rating_label": rating_label,
        "rating_icon": rating_icon,
        "user_display": user_display,
        "created_at_display": timestamp["display"],
        "created_at_sort": timestamp["sort"],
        "message_id": message_id,
        "client": client,
        "ui_version": ui_version,
        "csv": csv_row,
        "search_blob": f"{question} {comment}".lower(),
    }


def _build_table(rows: List[Dict[str, Any]]) -> None:
    header = (
        "<tr>"
        "<th>Time</th>"
        "<th>User</th>"
        "<th>Rating</th>"
        "<th>Question</th>"
        "<th>Comment</th>"
        "<th>Mode</th>"
        "<th>Client</th>"
        "<th>ID</th>"
        "</tr>"
    )
    body_cells: List[str] = []
    for row in rows:
        question_title = _escape(row["question"])
        comment_title = _escape(row["comment"])
        mode_cell = _escape(row["mode"])
        rating_text = f"{row['rating_icon']} {row['rating_label']}"
        if row["metadata_error"]:
            mode_cell = f"{mode_cell} <span style='background:#fee;color:#a00;padding:0.1rem 0.4rem;border-radius:0.6rem;font-size:0.75rem;'>metadata error</span>"
        body_cells.append(
            "<tr>"
            f"<td>{_escape(row['created_at_display'])}</td>"
            f"<td>{_escape(row['user_display'])}</td>"
            f"<td>{_escape(rating_text)}</td>"
            f"<td title=\"{question_title}\">{_escape(row['question_truncated'])}</td>"
            f"<td title=\"{comment_title}\">{_escape(row['comment_truncated'])}</td>"
            f"<td>{mode_cell}</td>"
            f"<td>{_escape(row['client_tag'] or 'n/a')}</td>"
            f"<td>{_escape(row['id'])}</td>"
            "</tr>"
        )
    html_table = (
        "<style>"
        ".feedback-table{width:100%;border-collapse:collapse;font-size:0.92rem;}"
        ".feedback-table th,.feedback-table td{padding:0.4rem 0.6rem;border-bottom:1px solid rgba(49,51,63,0.2);text-align:left;vertical-align:top;}"
        ".feedback-table tbody tr:nth-child(even){background-color:rgba(240,242,246,0.6);}"
        "</style>"
        f"<table class='feedback-table'><thead>{header}</thead><tbody>{''.join(body_cells)}</tbody></table>"
    )
    st.markdown(html_table, unsafe_allow_html=True)


def _details_tabs(row: Dict[str, Any], admin_raw_toggle: bool) -> None:
    tabs = ["Overview", "Metadata"]
    if admin_raw_toggle:
        tabs.append("Raw JSON")
    tab_objects = st.tabs(tabs)

    with tab_objects[0]:
        st.markdown("#### Overview")
        st.markdown(f"**Question**\n\n{row['question'] or 'n/a'}")
        st.markdown(f"**Answer preview**\n\n{row['answer_preview'] or 'n/a'}")
        st.markdown(f"**Comment**\n\n{row['comment'] or 'n/a'}")
        st.markdown(
            "**Summary**\n\n"
            f"- Rating: {row['rating_icon']} {row['rating_label']}\n"
            f"- Mode: {row['mode']}\n"
            f"- User: {row['user_display']}\n"
            f"- Session: {row['raw'].get('session_id') or 'n/a'}\n"
            f"- Created: {row['raw'].get('created_at') or 'n/a'}"
        )

    with tab_objects[1]:
        st.markdown("#### Metadata")
        if row["metadata_error"]:
            st.error("Metadata error: unable to parse JSON payload.")
            st.code(row["raw"].get("metadata_json") or "", language="json")
        else:
            if row["metadata"]:
                st.json(row["metadata"])
            else:
                st.info("No metadata available.")
        st.caption(
            f"Message: {row['message_id'] or 'n/a'} | Client: {row['client'] or 'n/a'} | UI version: {row['ui_version'] or 'n/a'}"
        )

    if admin_raw_toggle:
        with tab_objects[2]:
            st.markdown("#### Raw JSON")
            st.code(json.dumps(row["raw"], indent=2, ensure_ascii=False), language="json")


def render_feedback_table(
    items: List[Dict[str, Any]],
    *,
    admin_raw_toggle: bool = False,
    derived_items: Optional[List[Dict[str, Any]]] = None,
    selection_key: str = "feedback_table",
) -> Dict[str, Any]:
    rows = derived_items if derived_items is not None else [derive_feedback_item(item) for item in items]
    if not rows:
        return {"csv_rows": [], "selected": None, "derived_items": []}

    rows_sorted = sorted(rows, key=lambda r: r["created_at_sort"], reverse=True)
    _build_table(rows_sorted)

    options = ["(none)"] + [f"{row['created_at_display']} | #{row['id']}" for row in rows_sorted]
    selection = st.selectbox("Row details", options, index=0, key=f"{selection_key}_details")
    selected_row = None
    if selection != "(none)":
        idx = options.index(selection) - 1
        if 0 <= idx < len(rows_sorted):
            selected_row = rows_sorted[idx]
            with st.expander(f"Feedback #{selected_row['id']} details", expanded=True):
                _details_tabs(selected_row, admin_raw_toggle)

    csv_rows = [row["csv"] for row in rows_sorted]
    return {"csv_rows": csv_rows, "selected": selected_row, "derived_items": rows_sorted}
