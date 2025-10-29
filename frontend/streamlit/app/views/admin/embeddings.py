from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List
from uuid import uuid4

import streamlit as st

from services import api_client
from services.api_client import ApiError

HOW_IT_WORKS_MD = (
    "This admin workspace coordinates document uploads before triggering embedding jobs. "
    "Review backend contracts for payloads and errors: "
    "[API reference](../../backend/docs/API_REFERENCE.md#documents--embeddings) "
    "and [API errors](../../backend/docs/API_ERRORS.md)."
)

ACCEPT_EXTENSIONS = ["pdf", "docx", "pptx", "xlsx", "txt", "html"]
SOURCE_NAME = "manual-upload"


def _ensure_defaults() -> None:
    defaults = {
        "profile": "legacy_profile",
        "tags": "",
        "lang_hint": "auto",
        "update_alias": False,
        "evaluate": False,
        "upload_concurrency": 3,
        "files": [],
        "last_job_id": "",
        "job_snapshot": {},
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _add_files_from_uploader(uploaded_files: List[Any]) -> None:
    if not uploaded_files:
        return

    known = {(f["name"], f["size"]) for f in st.session_state["files"]}
    for item in uploaded_files:
        signature = (item.name, item.size)
        if signature in known:
            continue
        data = item.read()
        record = {
            "id": str(uuid4()),
            "name": item.name,
            "size": item.size,
            "mime": item.type or "application/octet-stream",
            "state": "Queued",
            "progress": 0,
            "upload_id": None,
            "error": None,
            "data": data,
        }
        st.session_state["files"].append(record)
        known.add(signature)


def _map_upload_error(err: ApiError) -> str:
    status = getattr(err, "status", None)
    if status == 415:
        return "File type not allowed. Try PDF, DOCX, PPTX, XLSX, TXT, or HTML."
    if status == 413:
        return "File exceeds backend limit. Split the document and retry."
    if status == 400:
        return "Invalid upload request"
    if status == 404:
        return "Upload not found"
    if status == 409:
        return "Upload conflict"
    msg = getattr(err, "message", None) or "Upload failed"
    return msg


def _upload_record(record: Dict[str, any], tags: str, lang_hint: str) -> None:
    record["state"] = "Uploading"
    record["progress"] = 50
    file_buffer = io.BytesIO(record["data"])
    file_buffer.seek(0)
    try:
        resp = api_client.upload_file(
            (record["name"], file_buffer, record["mime"]),
            source=SOURCE_NAME,
            tags=tags or None,
            lang_hint=lang_hint or None,
        )
    except ApiError as exc:
        record["state"] = "Failed"
        record["progress"] = 0
        record["upload_id"] = None
        record["error"] = _map_upload_error(exc)
    except Exception as exc:  # noqa: BLE001
        record["state"] = "Failed"
        record["progress"] = 0
        record["upload_id"] = None
        record["error"] = f"Upload failed: {exc}"
    else:
        record["state"] = "Uploaded"
        record["progress"] = 100
        record["upload_id"] = (resp or {}).get("upload_id")
        record["error"] = None
        record["data"] = b""
    finally:
        file_buffer.close()


def _process_uploads(concurrency: int, tags: str, lang_hint: str) -> None:
    queue = [f for f in st.session_state["files"] if f["state"] == "Queued"]
    if not queue:
        return

    max_workers = max(1, min(int(concurrency), 5))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_upload_record, rec, tags, lang_hint): rec["id"] for rec in queue}
        for future in as_completed(futures):
            future.result()


def _render_settings() -> None:
    st.markdown("### Job Settings")
    col_left, col_right = st.columns(2)
    with col_left:
        st.text_input("Profile", key="profile", help="Embedding profile, e.g. legacy_profile.")
        st.text_input("Tags", key="tags", help="Optional comma-separated tags.")
        st.selectbox("Language hint", options=["auto", "es", "en", "pt"], key="lang_hint")
    with col_right:
        st.checkbox("Update alias after indexing", key="update_alias")
        st.checkbox("Run evaluation after job", key="evaluate")
        st.number_input(
            "Upload concurrency",
            min_value=3,
            max_value=5,
            value=int(st.session_state.get("upload_concurrency", 3) or 3),
            step=1,
            key="upload_concurrency",
            help="Number of simultaneous uploads (3-5).",
        )


def _render_file_table() -> None:
    st.markdown("### Selected Files")
    files = st.session_state["files"]
    if not files:
        st.info("No files selected yet. Choose files using the picker above.")
        return

    header_cols = st.columns([0.35, 0.15, 0.2, 0.15, 0.15])
    header_cols[0].markdown("**Name**")
    header_cols[1].markdown("**Size**")
    header_cols[2].markdown("**Type**")
    header_cols[3].markdown("**State**")
    header_cols[4].markdown("**Actions**")

    for record in files:
        cols = st.columns([0.35, 0.15, 0.2, 0.15, 0.15])
        cols[0].write(record["name"])
        cols[1].write(_human_size(record["size"]))
        cols[2].write(record["mime"])
        cols[3].progress(min(int(record["progress"]), 100))
        cols[3].caption(record["state"])

        action_col = cols[4]
        if record["state"] == "Failed":
            if action_col.button("Retry", key=f"retry_{record['id']}"):
                record["state"] = "Queued"
                record["progress"] = 0
                record["error"] = None
        remove_label = "Remove" if record["state"] != "Uploaded" else "Remove file"
        if action_col.button(remove_label, key=f"remove_{record['id']}"):
            st.session_state["files"] = [f for f in files if f["id"] != record["id"]]
            st.rerun()

        if record.get("upload_id"):
            cols[0].caption(f"upload_id: {record['upload_id']}")
        if record.get("error"):
            cols[0].error(record["error"])


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"

    return f"{size_bytes / (1024 * 1024):.2f} MB"


def render(client) -> None:  # noqa: D401 - Streamlit view
    """Render the Documents & Embeddings admin view."""
    st.title("Documents & Embeddings (Admin)")

    role = st.session_state.get("role", "user")
    if role != "admin":
        st.error("Access restricted")
        return

    _ensure_defaults()

    st.markdown(HOW_IT_WORKS_MD)

    _render_settings()

    uploader_files = st.file_uploader(
        "Select documents for ingestion",
        type=ACCEPT_EXTENSIONS,
        accept_multiple_files=True,
        help="Supported formats: PDF, DOCX, PPTX, XLSX, TXT, HTML.",
    )
    _add_files_from_uploader(uploader_files)

    _render_file_table()

    has_queue = any(file["state"] == "Queued" for file in st.session_state["files"])
    if st.button("Start Uploads", disabled=not has_queue):
        with st.spinner("Uploading files..."):
            _process_uploads(
                st.session_state.get("upload_concurrency", 3),
                st.session_state.get("tags", ""),
                st.session_state.get("lang_hint", "auto"),
            )
        st.rerun()

    uploaded = [f for f in st.session_state["files"] if f["state"] == "Uploaded" and f.get("upload_id")]
    job_disabled = len(uploaded) == 0
    if st.button("Create Embedding Job", disabled=job_disabled):
        _handle_create_job(uploaded)

    _render_job_panel()

    _ = client  # reserved for future use


def _handle_create_job(uploaded: List[Dict[str, Any]]) -> None:
    upload_ids = [f["upload_id"] for f in uploaded if f.get("upload_id")]
    if not upload_ids:
        st.warning("No valid uploads available. Please upload files first.")
        return

    tags_raw = st.session_state.get("tags", "")
    tags_list = [tag.strip() for tag in tags_raw.split(",") if tag.strip()]
    lang_hint = st.session_state.get("lang_hint", "auto") or "auto"
    payload_tags = tags_list or None

    try:
        resp = api_client.create_ingest_job(
            upload_ids=upload_ids,
            profile=st.session_state.get("profile", "legacy_profile"),
            tags=payload_tags,
            lang_hint=lang_hint,
            priority=None,
            update_alias=bool(st.session_state.get("update_alias", False)),
            evaluate=bool(st.session_state.get("evaluate", False)),
        )
    except ApiError as exc:
        status = getattr(exc, "status", None)
        if status == 422:
            st.error("Profile not recognized. Update the profile or backend configuration.")
        elif status == 404:
            st.error("One or more uploads were not found. Refresh uploads and retry.")
        elif status == 409:
            st.error("Conflicting job already in progress. Wait or clear queued uploads.")
        elif status and status >= 500:
            st.error("Unable to create job. Check backend logs and retry.")
        else:
            st.error("Job creation failed. Check backend logs and retry.")
            detail = getattr(exc, "message", None)
            if detail:
                st.caption(detail)
        return
    except Exception as exc:  # noqa: BLE001
        st.error("Job creation failed. Check backend logs and retry.")
        st.caption(str(exc))
        return

    job_id = (resp or {}).get("job_id")
    st.session_state["last_job_id"] = job_id or ""
    st.session_state["job_snapshot"] = resp or {}
    if job_id:
        st.toast(f"Embedding job `{job_id}` created. Continue in Assistant.")
    else:
        st.toast("Embedding job created. Continue in Assistant.")


def _render_job_panel() -> None:
    job_id = st.session_state.get("last_job_id")
    if not job_id:
        return

    st.markdown("### Job Monitoring")
    st.caption(
        "Backend docs: "
        "[Uploads](../../backend/docs/API_REFERENCE.md#documents--embeddings) | "
        "[Ingest jobs](../../backend/docs/API_REFERENCE.md#documents--embeddings) | "
        "[Job status](../../backend/docs/API_REFERENCE.md#documents--embeddings) | "
        "[Chat](../../backend/docs/API_REFERENCE.md#chat)"
    )

    try:
        job = api_client.get_job(job_id)
    except ApiError as exc:
        if getattr(exc, "status", None) == 404:
            st.warning("Job not found. It may have expired on the backend.")
            if st.button("Clear job reference", key="job_not_found_clear"):
                st.session_state["last_job_id"] = ""
                st.session_state.pop("job_snapshot", None)
            return
        st.error(f"Failed to load job {job_id}: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load job {job_id}: {exc}")
        return

    st.session_state["job_snapshot"] = job
    status = (job.get("status") or "").lower()

    col1, col2 = st.columns(2)
    with col1:
        st.write(f"**Job ID:** {job.get('job_id', job_id)}")
        st.write(f"**Status:** {job.get('status', '-') }")
        st.write(f"**Profile:** {job.get('profile', '-') }")
        st.write(f"**Current phase:** {job.get('current_phase') or '-'}")
    with col2:
        st.write(f"**Created:** {job.get('created_at') or '-'}")
        st.write(f"**Started:** {job.get('started_at') or '-'}")
        st.write(f"**Finished:** {job.get('finished_at') or '-'}")

    inputs = job.get("inputs") or {}
    st.markdown("**Inputs**")
    inputs_cols = st.columns(3)
    uploads_count = inputs.get("uploads_count", "-")
    inputs_cols[0].write(f"Uploads: {uploads_count}")
    tags_display = inputs.get("tags") or []
    if isinstance(tags_display, list):
        tags_display = ", ".join(tags_display)
    inputs_cols[0].write(f"Tags: {tags_display or '-'}")
    inputs_cols[1].write(f"Lang hint: {inputs.get('lang_hint', 'auto')}")
    inputs_cols[1].write(f"Priority: {inputs.get('priority', '-')}")
    inputs_cols[2].write(f"Update alias: {inputs.get('update_alias', False)}")
    inputs_cols[2].write(f"Evaluate: {inputs.get('evaluate', False)}")

    progress = job.get("progress") or {}
    files_total = progress.get("files_total") or uploads_count or 0
    files_processed = progress.get("files_processed") or 0
    try:
        files_total_val = int(files_total)
    except (TypeError, ValueError):
        files_total_val = 0
    if files_total_val:
        ratio = min(max(files_processed / max(files_total_val, 1), 0.0), 1.0)
        st.progress(ratio, text=f"Files processed: {files_processed}/{files_total_val}")
    st.write(
        f"Chunks indexed: {progress.get('chunks_indexed', 0)} / {progress.get('chunks_total', 0)} "
        f"(dedupe skipped: {progress.get('dedupe_skipped', 0)})"
    )

    if job.get("logs_tail"):
        st.markdown("**Logs**")
        st.code("\n".join(job["logs_tail"]))

    if status == "succeeded":
        st.success("Embedding job succeeded.")
        action_cols = st.columns(2)
        if action_cols[0].button("Go to Assistant", key="job_success_go"):
            st.session_state["nav_target"] = "Assistant"
            st.rerun()
        if action_cols[1].button("Clear list", key="job_success_clear"):
            st.session_state["files"] = []
            st.rerun()
        return

    if status == "failed":
        error_info = job.get("error")
        if isinstance(error_info, dict):
            message = error_info.get("message") or "Job failed."
        else:
            message = error_info or "Job failed."
        st.error(message)
        st.caption("Review the logs above, resolve the issue, then re-upload and retry if needed.")
        if st.button("Clear job reference", key="job_failed_clear"):
            st.session_state["last_job_id"] = ""
            st.session_state.pop("job_snapshot", None)
            st.rerun()
        return

    time.sleep(1.5)
    st.rerun()
