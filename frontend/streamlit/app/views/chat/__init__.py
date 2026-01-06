from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import uuid
import html

import streamlit as st

from app.services.feedback_api import build_feedback_payload
from app_config.env import get_config

CONFIG = get_config()
DEBUG_CHAT_UI_STRICT = bool(CONFIG.get("DEBUG_CHAT_UI_STRICT", False))
DEBUG_CHAT_UI = bool(CONFIG.get("DEBUG_CHAT_UI", False)) or DEBUG_CHAT_UI_STRICT
LOGGER = logging.getLogger("chat_ui")
RAG_ASSETS_DIR = Path(CONFIG.get("RAG_ASSETS_DIR", "data/rag-assets"))
CHAT_FIGURES_DEBUG = str(CONFIG.get("CHAT_FIGURES_DEBUG", "0")).strip().lower() in {"1", "true", "yes", "on"}
if DEBUG_CHAT_UI:
    LOGGER.setLevel(logging.DEBUG)


def logp(msg: str, **kwargs: Any) -> None:
    if not DEBUG_CHAT_UI:
        return
    try:
        payload = json.dumps(kwargs, default=str)
    except Exception:  # noqa: BLE001
        payload = str(kwargs)
    try:
        LOGGER.debug("UI:%s | %s", msg, payload)
    except Exception:  # noqa: BLE001
        print(f"UI:{msg} | {payload}")

MODE_DETAILS = {
    "rag": {
        "icon": "📚",
        "label": "RAG",
        "color": "#2a6fdf",
        "tooltip": "Answer grounded on your documents.",
    },
    "hybrid": {
        "icon": "🧭",
        "label": "Hybrid",
        "color": "#7a3bdc",
        "tooltip": "Combined documents + model judgment.",
    },
    "fallback": {
        "icon": "🛟",
        "label": "Fallback",
        "color": "#a37c00",
        "tooltip": "No sufficient evidence; controlled backup answer.",
    },
    "direct": {
        "icon": "⚡",
        "label": "Direct",
        "color": "#2c9b5d",
        "tooltip": "Answered without retrieval.",
    },
    "unknown": {
        "icon": "❓",
        "label": "Unknown",
        "color": "#666666",
        "tooltip": "Answer confidence unavailable.",
    },
}

ANSWER_FIELD_ORDER = ("answer", "answer2", "answer3")
ANSWER_PLACEHOLDER = "No answer content returned."
QUESTION_TIME_FORMAT = "%Y-%m-%d %H:%M"


def inject_chat_css() -> None:
    st.markdown(
        """
<style>
.aiv2-chat .user-row {
    display: flex;
    justify-content: flex-end;
}
.aiv2-chat .user-bubble {
    display: inline-block;
    width: fit-content;
    max-width: 75%;
    background: #eaf2ff;
    border: 1px solid #cfe0ff;
    color: #1b2a4e;
    border-radius: 14px;
    padding: 10px 14px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, .04);
    vertical-align: top;
}
.aiv2-chat .user-bubble__text {
    white-space: pre-wrap;
    line-height: 1.45;
}
.aiv2-chat .user-bubble__ts {
    text-align: right;
    font-size: .78rem;
    color: #6b7280;
    margin-top: 4px;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def render_user_question(question: str, ts: Optional[str], key: str) -> None:
    clean = (question or "").strip()
    if not clean:
        return
    inject_chat_css()
    ts_label = ""
    if ts:
        try:
            ts_label = datetime.fromisoformat(ts).strftime(QUESTION_TIME_FORMAT)
        except ValueError:
            ts_label = ts
    if not ts_label:
        ts_label = datetime.now().strftime(QUESTION_TIME_FORMAT)
    safe_text = html.escape(clean).replace("\n", "<br>")
    st.markdown(
        f"""
<div class="aiv2-chat">
  <div class="user-row">
    <div class="user-bubble" id="q-{html.escape(key)}">
      <div class="user-bubble__text">{safe_text}</div>
      <div class="user-bubble__ts">{html.escape(ts_label)}</div>
    </div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def _compute_confidence(decision: Dict[str, Any], mode: str) -> Dict[str, Optional[float]]:
    sim_max = _safe_float(decision.get("max_similarity"))
    thr_low = _safe_float(decision.get("threshold_low"))
    thr_high = _safe_float(decision.get("threshold_high"))

    if mode == "fallback":
        return {"label": "Low", "ratio": 0.0, "sim_max": sim_max, "threshold_low": thr_low, "threshold_high": thr_high}

    if sim_max is None or thr_low is None or thr_high is None or thr_high <= thr_low:
        if sim_max is None or thr_low is None:
            return {"label": "Unknown", "ratio": None, "sim_max": sim_max, "threshold_low": thr_low, "threshold_high": thr_high}
        label = "High" if sim_max >= thr_low else "Low"
        return {"label": label, "ratio": None, "sim_max": sim_max, "threshold_low": thr_low, "threshold_high": thr_high}

    if sim_max >= thr_high:
        label = "High"
    elif sim_max >= thr_low:
        label = "Medium"
    else:
        label = "Low"

    ratio = (sim_max - thr_low) / (thr_high - thr_low)
    ratio = max(0.0, min(1.0, ratio))
    return {"label": label, "ratio": ratio, "sim_max": sim_max, "threshold_low": thr_low, "threshold_high": thr_high}


def _should_show_sources(mode: str, decision: Dict[str, Any], sim_max: Optional[float], thr_low: Optional[float]) -> bool:
    if mode == "fallback":
        return False
    gate_failed = decision.get("gate_failed")
    if isinstance(gate_failed, str):
        gate_failed = gate_failed.lower() == "true"
    if gate_failed:
        return False
    if sim_max is not None and thr_low is not None and sim_max < thr_low:
        return False
    return True


def _filter_evidence_chunks(chunks: List[Dict[str, Any]], decision: Dict[str, Any], mode: str) -> List[Dict[str, Any]]:
    sim_max = _safe_float(decision.get("max_similarity"))
    thr_low = _safe_float(decision.get("threshold_low"))
    show_sources = _should_show_sources(mode, decision, sim_max, thr_low)
    if not show_sources:
        return []

    if thr_low is None:
        return list(chunks)

    filtered = []
    for chunk in chunks:
        score = _safe_float(chunk.get("score"))
        if score is None or score >= thr_low:
            filtered.append(chunk)
    return filtered


def _sanitize_snippet(snippet: str, limit: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", snippet or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _format_chunk(chunk: Dict[str, Any]) -> Tuple[str, str, str, Optional[float]]:
    source = chunk.get("source") or ""
    file_name = Path(source).name or (chunk.get("document_id") or "document")
    doc_ref = chunk.get("doc_id") or chunk.get("chunk_id") or "n/a"
    snippet = _sanitize_snippet(chunk.get("snippet") or chunk.get("text") or "")
    score = _safe_float(chunk.get("score"))
    return file_name, doc_ref, snippet, score


def _select_answer_text(payload: Dict[str, Any]) -> Tuple[Optional[str], str]:
    for field in ANSWER_FIELD_ORDER:
        value = payload.get(field)
        if isinstance(value, str):
            if value.strip():
                return value, field
        elif value is not None:
            text = str(value)
            if text.strip():
                return text, field
    return None, "none"


FIGURE_LABEL_PATTERN = re.compile(r"^\s*(related figure\(s\):|figure\(s\):|see images?:)", re.IGNORECASE)


def _sanitize_figure_labels(answer: str) -> Tuple[str, bool]:
    lines = (answer or "").split("\n")
    found = False
    sanitized_lines = []
    skip_block = False
    for line in lines:
        if skip_block:
            if not line.strip():
                skip_block = False
            continue
        match = FIGURE_LABEL_PATTERN.match(line)
        if match:
            found = True
            skip_block = True
            continue
        sanitized_lines.append(line)
    return "\n".join(sanitized_lines), found


def _figure_image_refs(meta_list: Optional[List[Dict[str, Any]]]) -> List[str]:
    refs: List[str] = []
    seen: set[str] = set()
    if not meta_list:
        return refs
    for item in meta_list:
        if not isinstance(item, dict):
            continue
        ctype = str(item.get("chunk_type") or "").lower()
        ref = item.get("image_ref")
        if ctype and ctype != "figure" and not ref:
            continue
        if not isinstance(ref, str):
            continue
        ref_clean = ref.strip()
        if not ref_clean or ref_clean in seen:
            continue
        ref_path = Path(ref_clean)
        if ref_path.is_absolute():
            if CHAT_FIGURES_DEBUG:
                st.warning(f"Skipping absolute image_ref (unsafe): {ref_clean}")
            continue
        seen.add(ref_clean)
        refs.append(ref_clean)
    return refs


def _render_figure_thumbnails(image_refs: List[str]) -> None:
    if not image_refs:
        return
    debug_rows = []
    for ref in image_refs:
        full_path = (RAG_ASSETS_DIR / Path(ref)).resolve()
        exists = full_path.is_file()
        size = None
        if exists:
            try:
                size = full_path.stat().st_size
            except Exception:  # noqa: BLE001
                size = None
        if exists:
            try:
                st.image(str(full_path), caption=None, use_container_width=True)
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Failed to render image {ref}: {exc}")
        else:
            st.warning(f"Image not found: {ref}")
        debug_rows.append({"image_ref": ref, "path": str(full_path), "exists": exists, "size": size})
    if CHAT_FIGURES_DEBUG:
        with st.expander("Figures debug", expanded=False):
            st.write({"RAG_ASSETS_DIR": str(RAG_ASSETS_DIR), "images": debug_rows})


def render_primary_answer(payload: Dict[str, Any]) -> Tuple[str, str]:
    from textwrap import shorten

    answer_value: Optional[str] = None
    source_key = "none"
    for key in ANSWER_FIELD_ORDER:
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            answer_value = candidate
            source_key = key
            break
        if candidate is not None:
            as_text = str(candidate).strip()
            if as_text:
                answer_value = as_text
                source_key = key
                break

    logp("render_primary_answer:start", source=source_key, len_answer=len(answer_value or ""))

    if not answer_value:
        st.info(ANSWER_PLACEHOLDER)
        logp("render_primary_answer:none")
        return "", "none"

    normalized_raw = answer_value.replace("\r\n", "\n").replace("\r", "\n")
    normalized, figure_label_present = _sanitize_figure_labels(normalized_raw)
    figure_refs = _figure_image_refs(payload.get("retrieved_chunks_metadata"))

    if DEBUG_CHAT_UI_STRICT:
        st.markdown("<style>.stMarkdown{outline:1px dotted #915 !important;}</style>", unsafe_allow_html=True)
        with st.container():
            st.markdown(
                f"""
<div style="border:2px dashed #915; padding:12px; background:#fff7fb;">
  <strong>DEBUG — Answer Box (source: {source_key})</strong>
</div>
""",
                unsafe_allow_html=True,
            )
            st.caption(f"len(answer)={len(normalized)} | preview: {shorten(normalized, 120)}")
            st.code(normalized[:400], language="markdown")
            logp("render_primary_answer:box_shown", preview=normalized[:120])

    try:
        st.markdown(normalized)
        logp("render_primary_answer:markdown_rendered", ok=True, len_answer=len(normalized))
    except Exception as exc:  # noqa: BLE001
        logp("render_primary_answer:error", error=str(exc))
        st.error("Error rendering answer.")
        return "", source_key

    if figure_refs:
        with st.expander("Figures", expanded=False):
            _render_figure_thumbnails(figure_refs)
    elif CHAT_FIGURES_DEBUG:
        st.caption("Figures: none to render")

    return normalized, source_key


def _render_mode_chip(mode_key: str) -> None:
    details = MODE_DETAILS.get(mode_key, MODE_DETAILS["unknown"])
    style = (
        "display:inline-flex;align-items:center;gap:0.4rem;"
        f"padding:0.1rem 0.6rem;border-radius:999px;border:1px solid {details['color']};"
        f"color:{details['color']};font-size:0.85rem;font-weight:600;"
    )
    st.markdown(
        f"<span style='{style}'>{details['icon']} {details['label']}</span>",
        unsafe_allow_html=True,
    )
    st.caption(details["tooltip"])


def _render_evidence_list(chunks: List[Dict[str, Any]], sources_used: Optional[str]) -> None:
    if not chunks:
        return
    expanded = bool(chunks)
    title = f"Sources ({len(chunks)})"
    with st.expander(title, expanded=expanded):
        if sources_used == "partial":
            st.info("Used a subset of available context.")
        for idx, chunk in enumerate(chunks, start=1):
            file_name, doc_ref, snippet, score = _format_chunk(chunk)
            st.markdown(f"**{idx}. {file_name}** (doc: `{doc_ref}`)")
            if score is not None:
                st.caption(f"Relevance: {score:.2f}")
            st.markdown(snippet or "_(no preview available)_")
            st.markdown("---")


def _render_mode_explanation(
    mode: str,
    decision: Dict[str, Any],
    sim_max: Optional[float],
    thr_low: Optional[float],
    thr_high: Optional[float],
    show_allowed: bool,
    has_sources: bool,
) -> None:
    explanations = {
        "rag": "We found sufficient evidence (max similarity {sim} ≥ {thr}) and grounded the answer on retrieved documents.",
        "hybrid": "We combined retrieved documents and model judgment because evidence met the threshold (max similarity {sim} ≥ {thr}).",
        "fallback": "No evidence met the quality bar (max similarity {sim} < {thr}); we returned a safe backup answer.",
        "direct": "No retrieval was required. The model answered directly.",
    }
    fallback_message = "No sources displayed because they didn’t meet the quality threshold."

    with st.expander("Why this mode?", expanded=False):
        sim_txt = f"{sim_max:.2f}" if sim_max is not None else "n/a"
        thr_txt = f"{thr_high:.2f}" if mode == "rag" and thr_high is not None else f"{thr_low:.2f}" if thr_low is not None else "n/a"
        template = explanations.get(mode, "Answer strategy details unavailable.")
        st.write(template.format(sim=sim_txt, thr=thr_txt))

        confidence = _compute_confidence(decision, mode)
        ratio = confidence.get("ratio")
        label = confidence.get("label", "Unknown")
        if ratio is not None:
            st.progress(ratio, text=f"Confidence: {label}")
        else:
            st.caption(f"Confidence: {label}")

        if not show_allowed:
            st.info(fallback_message)
        elif not has_sources:
            st.info("No sources displayed because they didn’t meet the quality threshold.")


def render_mode_chip_and_summary(meta: Dict[str, Any], key: Optional[str] = None) -> Dict[str, Any]:
    mode_key = (meta.get("mode") or "unknown").lower()
    _render_mode_chip(mode_key)

    decision = meta.get("decision_explain") or {}
    confidence = _compute_confidence(decision, mode_key)
    sim_max = confidence.get("sim_max")
    thr_low = confidence.get("threshold_low")
    thr_high = confidence.get("threshold_high")
    kept_n = decision.get("kept_n")
    try:
        kept_n_value = int(kept_n)
    except (TypeError, ValueError):
        kept_n_value = 0
    summary_evidence = len(meta.get("used_chunks") or []) or kept_n_value
    summary_line = f"Mode: {MODE_DETAILS.get(mode_key, MODE_DETAILS['unknown'])['label']}. Evidence: {summary_evidence}. Confidence: {confidence.get('label')}."
    st.markdown(summary_line)

    return {"mode_key": mode_key, "decision": decision, "confidence": confidence}


def render_decision_explain(
    meta: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    key: Optional[str] = None,
) -> None:
    ctx = context or {}
    mode_key = ctx.get("mode_key") or (meta.get("mode") or "unknown").lower()
    decision = ctx.get("decision") or meta.get("decision_explain") or {}
    confidence = ctx.get("confidence") or _compute_confidence(decision, mode_key)
    sim_max = confidence.get("sim_max")
    thr_low = confidence.get("threshold_low")
    thr_high = confidence.get("threshold_high")

    raw_chunks = meta.get("used_chunks") or []
    filtered_chunks = _filter_evidence_chunks(raw_chunks, decision, mode_key)
    show_allowed = _should_show_sources(mode_key, decision, sim_max, thr_low)
    if filtered_chunks:
        _render_evidence_list(filtered_chunks, meta.get("sources_used"))
    elif show_allowed and raw_chunks:
        st.caption("No sources met the display threshold.")

    _render_mode_explanation(
        mode_key,
        decision,
        sim_max,
        thr_low,
        thr_high,
        show_allowed,
        bool(filtered_chunks),
    )


def _build_answer_meta(result: Dict[str, Any], answer_text: Optional[str], answer_field: str) -> Dict[str, Any]:
    clean_text = (answer_text or "").strip()
    question_text = (result.get("question") or "").strip()
    return {
        "mode": (result.get("mode") or "unknown").lower(),
        "used_chunks": result.get("used_chunks") or [],
        "sources_used": result.get("sources_used"),
        "decision_explain": result.get("decision_explain") or {},
        "answer_text": clean_text,
        "answer_field": answer_field,
        "has_answer": bool(clean_text),
        "raw_result": result,
        "question_text": question_text,
        "question_ts": datetime.now().strftime(QUESTION_TIME_FORMAT) if question_text else None,
    }


def _render_feedback_controls(
    idx: int,
    question: str,
    answer_text: str,
    meta: Optional[Dict[str, Any]],
    payload: Dict[str, Any],
    api_client,
) -> None:
    msg_seed = f"{idx}_{question}_{answer_text}"
    msg_id = f"{idx}_{abs(hash(msg_seed)) % 1_000_000}"
    already = st.session_state.get(f"fb_done_{msg_id}", False)
    state = st.session_state.feedback_mode.setdefault(idx, {"icon": None})

    cols = st.columns([1, 1, 6], gap="small")
    like_clicked = cols[0].button("\U0001F44D", key=f"like_{msg_id}", disabled=already)
    dislike_clicked = cols[1].button("\U0001F44E", key=f"dislike_{msg_id}", disabled=already)
    if like_clicked:
        state["icon"] = "like"
        state["needs_reset"] = True
        st.session_state.feedback_mode[idx] = state
    if dislike_clicked:
        state["icon"] = "dislike"
        state["needs_reset"] = True
        st.session_state.feedback_mode[idx] = state

    if already:
        with cols[2]:
            st.caption("Thanks for your feedback.")
        return

    if state.get("icon"):
        question_text = (question or "").strip()
        answer_value = (answer_text or "").strip()
        comment_key = f"feedback_comment_{msg_id}"
        if state.pop("needs_reset", None):
            st.session_state.pop(comment_key, None)
            st.session_state.feedback_mode[idx] = state

        with cols[2]:
            st.text_area("Add a note (optional)", key=comment_key)
            if st.button("Submit feedback", key=f"submit_feedback_{msg_id}"):
                if not (st.session_state.get("is_authenticated") or st.session_state.get("authenticated")):
                    st.warning("Please login to send feedback")
                else:
                    feedback_text = (st.session_state.get(comment_key) or "").strip()
                    comment_text = feedback_text if feedback_text else ""
                    if not question_text or not answer_value:
                        st.warning("Nothing to send for feedback")
                    else:
                        if DEBUG_CHAT_UI:
                            rating_preview = 1 if state.get("icon") == "like" else -1
                            logp(
                                "feedback_submit:start",
                                message_id=msg_id,
                                rating=rating_preview,
                                has_note=bool(feedback_text),
                            )
                        mode_value = meta.get("mode") if isinstance(meta, dict) else None
                        rating = 1 if state.get("icon") == "like" else -1
                        payload_value = payload if DEBUG_CHAT_UI_STRICT else None
                        user_id_raw = st.session_state.get("user_id", CONFIG.get("FEEDBACK_DEFAULT_USER_ID"))
                        user_id_value: Optional[int] = None
                        if user_id_raw not in (None, ""):
                            try:
                                user_id_value = int(user_id_raw)
                            except (TypeError, ValueError):
                                logp("feedback_submit:user_id_invalid", raw=user_id_raw)
                                user_id_value = None
                        session_identifier = st.session_state.get("feedback_session_id")
                        if not session_identifier:
                            session_identifier = str(uuid.uuid4())
                            st.session_state["feedback_session_id"] = session_identifier
                        category_value = "like" if rating > 0 else "dislike"
                        metadata_payload: Dict[str, Any] = {
                            "question": question_text,
                            "answer_preview": answer_value[:200],
                            "mode": mode_value,
                            "message_id": msg_id,
                        }
                        if feedback_text:
                            metadata_payload["note"] = feedback_text
                        if payload_value is not None:
                            metadata_payload["raw_response"] = payload_value

                        try:
                            feedback_request = build_feedback_payload(
                                user_id=user_id_value,
                                session_id=session_identifier,
                                rating=rating,
                                category=category_value,
                                comment=comment_text,
                                metadata=metadata_payload,
                            )
                            result = api_client.send_feedback(
                                **feedback_request,
                                message_id=msg_id,
                            )
                        except Exception as exc:  # noqa: BLE001
                            logp("feedback_submit:error", message_id=msg_id, error=str(exc))
                            st.error(f"Failed to save message feedback: {exc}")
                        else:
                            logp("feedback_submit:success", message_id=msg_id)
                            if isinstance(result, dict) and result.get("warning"):
                                st.warning(result["warning"])
                            st.success("Feedback saved.")
                            st.session_state[f"fb_done_{msg_id}"] = True
                            st.session_state.pop(comment_key, None)
                            st.session_state.feedback_mode.pop(idx, None)
                            st.session_state["last_feedback_ok"] = True
                            st.rerun()
    else:
        st.caption("How was this answer?")


def render_assistant_answer(payload: Dict[str, Any], key: str) -> Tuple[str, str]:
    _ = key  # Reserved for future key-aware rendering
    return render_primary_answer(payload)


def render_message(item: Dict[str, Any], idx: int, api_client) -> None:
    raw_payload = item.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    question = (item.get("question") or "").strip()
    message_id = str(item.get("id") or f"legacy-{idx}")
    timestamp = item.get("ts_local") or item.get("timestamp") or ""

    if DEBUG_CHAT_UI:
        logp(
            "message_render:start",
            idx=idx,
            message_id=message_id,
            len_question=len(question),
            answer_len=len((payload.get("answer") or "")),
        )

    render_user_question(question, timestamp, key=message_id)

    answer_markdown, answer_source = render_assistant_answer(payload, key=f"a-{message_id}")
    meta = _build_answer_meta(payload, answer_markdown, answer_source)

    if DEBUG_CHAT_UI:
        logp(
            "message_render:post_answer",
            idx=idx,
            message_id=message_id,
            len_answer=len(answer_markdown),
            answer_source=answer_source,
        )

    context = render_mode_chip_and_summary(meta, key=f"m-{message_id}")
    render_decision_explain(meta, context, key=f"d-{message_id}")

    if DEBUG_CHAT_UI_STRICT:
        with st.expander(f"Debug: Raw payload ({message_id})", expanded=False):
            st.json(raw_payload if isinstance(raw_payload, dict) else {"raw": raw_payload})
        decision_debug = meta.get("decision_explain") or {}
        debug_stats = {
            "answer_source": answer_source,
            "len_answer": len(answer_markdown),
            "len_question": len(question),
            "used_chunks_count": len(meta.get("used_chunks") or []),
            "retrieved_chunks_count": len(payload.get("retrieved_chunks_metadata") or []),
            "mode": meta.get("mode"),
            "sim_max": decision_debug.get("max_similarity"),
            "threshold_low": decision_debug.get("threshold_low"),
            "threshold_high": decision_debug.get("threshold_high"),
        }
        with st.expander(f"Debug: Keys & lengths ({message_id})", expanded=False):
            st.json(debug_stats)
        logp("message_render:debug_stats", idx=idx, stats=debug_stats)

    answer_for_feedback = answer_markdown or (payload.get("answer") if isinstance(payload, dict) else "")
    answer_for_feedback = answer_for_feedback or ANSWER_PLACEHOLDER
    payload_for_feedback = payload if isinstance(payload, dict) else {}
    _render_feedback_controls(idx, question, answer_for_feedback, meta, payload_for_feedback, api_client)


def render(api_client, assistant_title: str, feedback_dir: str) -> None:
    inject_chat_css()
    st.header(assistant_title)
    _ = feedback_dir  # legacy arg: feedback handled via service calls

    st.session_state.setdefault("chat_history", [])
    chat_history: List[Dict[str, Any]] = st.session_state.chat_history

    user_prompt = st.chat_input("Ask a question...")
    if user_prompt:
        if DEBUG_CHAT_UI:
            logp("chat_submit", len_prompt=len(user_prompt))
        user_id_value: Optional[int] = None
        user_id_raw = st.session_state.get("user_id", CONFIG.get("FEEDBACK_DEFAULT_USER_ID"))
        if user_id_raw not in (None, ""):
            try:
                user_id_value = int(user_id_raw)
            except (TypeError, ValueError):
                logp("chat_submit:user_id_invalid", raw=user_id_raw)
                user_id_value = None
        session_identifier = st.session_state.get("feedback_session_id")
        if not session_identifier:
            session_identifier = str(uuid.uuid4())
            st.session_state["feedback_session_id"] = session_identifier
        with st.spinner("Thinking..."):
            result = api_client.chat(user_prompt, user_id=user_id_value, session_id=session_identifier)
        question_from_response = ""
        if isinstance(result, dict):
            question_from_response = (result.get("question") or "").strip()
        chat_history.append(
            {
                "id": str(uuid.uuid4()),
                "question": question_from_response or user_prompt,
                "payload": result,
                "ts_local": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )
        if DEBUG_CHAT_UI:
            logp(
                "chat_history:append",
                new_index=len(chat_history) - 1,
                history_length=len(chat_history),
            )
        st.rerun()

    if DEBUG_CHAT_UI:
        logp("chat_history:length", length=len(chat_history))
        st.write({"debug_len_history": len(chat_history)})
    if DEBUG_CHAT_UI_STRICT:
        with st.expander("Debug: History length & indices", expanded=False):
            st.json({"length": len(chat_history), "indices": list(range(len(chat_history)))})

    if not chat_history:
        st.info("Ask something to get started.")
        return

    for idx, item in enumerate(chat_history):
        with st.container():
            render_message(item, idx, api_client)
        if idx < len(chat_history) - 1:
            st.markdown(
                "<hr style='border:0;border-top:1px solid #e5e7eb;margin:1.5rem 0;'/>",
                unsafe_allow_html=True,
            )
